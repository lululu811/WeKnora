"""
DuckDB 数据源 — 连接复用 + 并发控制 + 结果行数上限

不变式
------
* 一个 `DuckDBSource` 只有**一条**连接，且任意时刻只有一个线程在用它。
  `duckdb.DuckDBPyConnection` 不是线程安全的，并发 execute 会得到错乱的结果
  甚至让连接失效，所以这里用 `_conn_lock` 串行化实际执行；
  `_semaphore` 只做准入控制（限制同时排队的请求数）。
* 单次查询最多返回 `max_rows` 行。`indicators.duckdb` 有 12GB，
  没有上限的话一条 `SELECT *` 就能把进程 OOM 掉。被截断时
  `last_result_truncated` 为 True，调用方应当如实告知调用者。
"""

import os
import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

import duckdb

from .base import DataSource, DataSourceType, DataSourceStatus

# DuckDB 的 buffer pool 上限，单位 MiB。
#
# 不设它，DuckDB 会按"可见内存的 80%"给自己 sizing。在 Docker 里没有 cgroup 限制
# 时它看到的是**宿主机**的内存（本机 48 GiB），而真正能给它的只有 Docker VM 的
# 上限（本机 7.75 GiB，redis / postgres / app / frontend 还要分）。结果就是跑几千次
# 查询之后 buffer pool 一路涨到把整个 VM 撑爆，内核 OOM killer 挑走内存占用最高的
# 进程 —— 也就是本服务，exit 137 神秘消失，没有任何 Python 级报错。
#
# 所以这里必须显式给上限：超了就让 DuckDB 自己报错或走临时文件溢出，而不是拖死整台
# 机器。留 2048 MiB 是因为这里的查询都是单票维度的（几千行量级），2 GiB 远远够用；
# 真正吃内存的是"一次扫全市场"那种查询，那类查询本来就该改成分片。
DUCKDB_MEMORY_LIMIT_MB = int(os.getenv("DUCKDB_MEMORY_LIMIT_MB", "2048"))


def _duckdb_config() -> Dict[str, str]:
    """连接配置。**所有** duckdb.connect 都必须走这里，不能各写各的。"""
    return {"memory_limit": f"{DUCKDB_MEMORY_LIMIT_MB}MB"}

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 100_000


class DuckDBSource(DataSource):
    """
    DuckDB 数据源（只读）

    特性：
    - 全局共享只读连接，串行化访问
    - Semaphore 准入控制
    - 查询在事件循环默认线程池中执行，不阻塞事件循环
    - 结果行数硬上限
    """

    def __init__(
        self,
        name: str,
        db_path: str,
        max_concurrent: int = 4,
        read_only: bool = True,
        max_rows: int = DEFAULT_MAX_ROWS,
    ):
        super().__init__(name, {
            "db_path": db_path,
            "max_concurrent": max_concurrent,
            "read_only": read_only,
            "max_rows": max_rows,
        })
        self.db_path = os.path.expanduser(db_path)
        self.read_only = read_only
        self.max_rows = max_rows
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._init_lock = asyncio.Lock()
        self._conn_lock = threading.Lock()
        self._truncated = False
        # ---- 数据版本探针 -------------------------------------------------
        # DuckDB 的 read_only 连接会把打开那一刻的快照钉住直到连接关闭。
        # ETL 在文件层面重算之后，这个连接会一直返回旧值，直到进程重启 ——
        # 批量重算 indicators.duckdb 之后服务读到旧数据就是这么来的。
        #
        # 办法：每次查询前 stat 一次文件，签名变了就重开连接。
        # `os.stat` 是微秒级，放在查询前不构成开销。
        self._generation = 0
        self._signature: Optional[tuple] = None
        self._reopen_failures = 0

    def _file_signature(self) -> Optional[tuple]:
        """(mtime_ns, size)。文件不存在或读不到 stat 时返回 None（不触发重开）。"""
        try:
            st = os.stat(self.db_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    @property
    def generation(self) -> int:
        """数据代次。每次因底层文件变化而重开连接就 +1。

        调用方应把它并进缓存键：ETL 落库后老缓存自然失效，不需要手工清。
        """
        return self._generation

    @property
    def reopen_failures(self) -> int:
        return self._reopen_failures

    def maybe_reopen(self) -> bool:
        """底层文件变了就重开连接。返回是否发生了重开。

        失败时保留旧连接并计数 —— 宁可暂时读到略旧的数据，也不要因为
        ETL 正在写盘而把数据源打成不可用。
        """
        sig = self._file_signature()
        if sig is None or sig == self._signature:
            return False
        try:
            with self._conn_lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                self._conn = duckdb.connect(self.db_path, read_only=self.read_only, config=_duckdb_config())
                self._conn.execute("SELECT 1").fetchone()
                self._signature = sig
                self._generation += 1
        except Exception as exc:  # noqa: BLE001
            self._reopen_failures += 1
            logger.warning("重开 DuckDB 连接失败 %s: %s", self.name, exc)
            return False
        logger.info("检测到 %s 数据变更，重开连接（generation=%d）",
                    self.name, self._generation)
        return True

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.DUCKDB

    @property
    def last_result_truncated(self) -> bool:
        """上一次 execute 是否因为 max_rows 上限丢过行。"""
        return self._truncated

    async def initialize(self) -> None:
        """初始化 — 创建共享只读连接"""
        async with self._init_lock:
            if self._conn is None:
                if not os.path.exists(self.db_path):
                    raise FileNotFoundError(f"DuckDB 文件不存在：{self.db_path}")
                self._conn = duckdb.connect(self.db_path, read_only=self.read_only, config=_duckdb_config())
                self._signature = self._file_signature()
                self._status = DataSourceStatus.HEALTHY

    async def health_check(self) -> bool:
        try:
            if self._conn is None:
                return False
            await self.execute("SELECT 1")
            self._status = DataSourceStatus.HEALTHY
            return True
        except Exception:
            self._status = DataSourceStatus.UNHEALTHY
            return False

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行只读查询。

        Args:
            query: SQL 文本。用户输入必须走 `?` 绑定参数。
            params: 绑定参数序列。

        Returns:
            查询结果列表，每行为 dict。行数受 `max_rows` 限制。
        """
        async with self._semaphore:
            if self._conn is None:
                await self.initialize()
            else:
                # 查数据之前先探一下版本：ETL 昨晚重算过，这里就会重开连接。
                await asyncio.to_thread(self.maybe_reopen)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._execute_sync, query, params
            )

    def _execute_sync(
        self,
        query: str,
        params: Optional[Sequence[Any]],
    ) -> List[Dict[str, Any]]:
        """同步执行查询（在线程池中运行）。连接级串行化在这里生效。"""
        with self._conn_lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError(f"数据源 {self.name} 未初始化")
            cursor = conn.execute(query, list(params) if params else [])
            self._truncated = False
            if cursor.description is None:
                return []
            columns = [desc[0] for desc in cursor.description]
            # 多取一行用来判断是否被截断，避免无脑 fetchall 撑爆内存
            raw = cursor.fetchmany(self.max_rows + 1)
            if len(raw) > self.max_rows:
                raw = raw[: self.max_rows]
                self._truncated = True
            return [dict(zip(columns, row)) for row in raw]

    async def execute_scalar(
        self,
        query: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Any:
        async with self._semaphore:
            if self._conn is None:
                await self.initialize()
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._execute_scalar_sync, query, params
            )

    def _execute_scalar_sync(self, query: str, params: Optional[Sequence[Any]]) -> Any:
        with self._conn_lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError(f"数据源 {self.name} 未初始化")
            row = conn.execute(query, list(params) if params else []).fetchone()
            return row[0] if row else None

    async def close(self) -> None:
        """关闭连接"""
        async with self._init_lock:
            with self._conn_lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
            self._status = DataSourceStatus.UNKNOWN
