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
* 换连接必须**先关旧、再开新**（`_swap_connection`）：`duckdb.connect(path)`
  在同一进程内对同一路径返回同一份 DatabaseInstance，只要该路径上还有连接开着，
  新连接就接进那份实例、复用它的缓存元数据。实测：老连接常开时，宿主把表从
  3 行重写成 200 万行，新开的连接读到的仍是 3 行；把连接全部关掉再开才是新实例。
  所以"读到不一致元数据"要救回来，只有关掉该路径上的**全部**连接这一条路。
* 底层库正在被别的进程改写时（宿主机 ETL + 容器内服务），读到的元数据可能是
  写到一半的。这类错误由 `_heal_and_run` 兜：重试、换连接、再失败才报
  `DuckDBCorruptReadError`。**不要**用 `os.stat` 判断库变没变 —— Docker Desktop
  的 bind mount 上属性缓存会陈旧（实测滞后 30+ 分钟）。
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
    return {
        "memory_limit": f"{DUCKDB_MEMORY_LIMIT_MB}MB",
        "enable_external_access": "false",
    }

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 100_000

# 连接读到"写到一半的文件"时，重开连接重试的次数。窗口只有另一个进程 checkpoint
# 的那几秒，正常一次就够；不放大是因为每次尝试都要重开库、重读它的元数据。
CORRUPTION_RETRIES = 3

# 换连接之间等一下：错的是"另一个进程正在 checkpoint"的那段时间，等过去就好。
# 递增（0.5s / 1.0s / 1.5s）三次共覆盖 ~3 秒，仍在 Go 侧 10s 查询超时之内。
CORRUPTION_RETRY_DELAY = 0.5

# 反序列化存储元数据失败的两种形态：异常类型是 SERIALIZATION，文本里带
# `Failed to deserialize`。只按类型判断会漏掉被包了一层的路径，所以两者都认。
_CORRUPTION_MARKERS = ("Failed to deserialize", "Serialization Error")


class DuckDBCorruptReadError(RuntimeError):
    """读到了写到一半的 DuckDB 文件，重开连接重试后仍然失败。

    触发条件：宿主机上的 ETL（如 `indicators_sync.py`）正在写同一个 `*.duckdb`，
    而服务在容器里读它。宿主机上再开一个 read_only 连接会被 DuckDB 的文件锁挡住
    （`IO Error: Could not set lock on file`），但 Docker Desktop 的 bind mount 不
    传递 POSIX 锁 —— 容器里的打开会静默成功，随后把写到一半/已被复用的元数据块
    当块指针，报 `field id mismatch, expected: N, got: M`。这时**每一次**表查询都会
    失败，直到连接被换掉。

    对调用方的含义：不是 SQL 的问题，重试即可；把它映射成 5xx 而不是 4xx。
    """

    def __init__(self, name: str, db_path: str, cause: BaseException) -> None:
        super().__init__(
            f"数据源 {name}（{os.path.basename(db_path)}）正在被另一个进程写入，"
            f"读取到不一致的元数据，重开连接重试 {CORRUPTION_RETRIES} 次仍失败。"
            f"这是宿主机 ETL 写库的中间态，不是 SQL 的问题，稍后重试即可。"
            f"底层错误：{cause}"
        )
        self.name = name
        self.db_path = db_path
        self.cause = cause


def _looks_like_corrupt_read(exc: BaseException) -> bool:
    """这条错误是否意味着"连接读到了不一致的文件内容"。

    只认反序列化失败。SQL 写错（Binder Error）、权限、锁冲突都不算：它们重试
    仍是同样的结果，重开连接反而会把一条好连接丢掉。
    """
    if isinstance(exc, duckdb.SerializationException):
        return True
    text = str(exc)
    return any(marker in text for marker in _CORRUPTION_MARKERS)


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
        if not self._swap_connection(sig):
            return False
        logger.info("检测到 %s 数据变更，重开连接（generation=%d）",
                    self.name, self._generation)
        return True

    def _swap_connection(self, sig: Optional[tuple]) -> bool:
        """关掉旧连接 → 开一条新连接 → 换上去。失败时返回 False 且没有连接可用。

        **顺序不能反（先关旧、再开新）**：`duckdb.connect(path)` 在同一进程里对
        同一路径返回的是同一份 DatabaseInstance —— 只要该路径上还有一条连接开着，
        新连接就接进那份实例，连同它已缓存的元数据一起复用。实测：保持一条老连接
        常开，宿主把表从 3 行重写成 200 万行，同一进程里新开的连接读到的仍然是
        **3 行**；把该路径的连接全部关掉后再开，才读到 200 万行。

        所以"先开新的、成功后再关旧的"在坏实例上永远救不回来：新连接还是那份坏
        实例，每次查询都报同一个 `Serialization Error`。要拿到干净实例，必须先把
        这个路径上的连接全部关掉（`_reopen_broken` 是这条不变式的直接使用者）。
        """
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            try:
                conn = duckdb.connect(self.db_path, read_only=self.read_only, config=_duckdb_config())
                conn.execute("SELECT 1").fetchone()
            except Exception as exc:  # noqa: BLE001
                self._reopen_failures += 1
                logger.warning("重开 DuckDB 连接失败 %s: %s", self.name, exc)
                return False
            self._conn = conn
            self._signature = sig
            self._generation += 1
        return True

    def _reopen_broken(self) -> bool:
        """连接已损坏时强制重开，**不**看文件签名。

        签名探测在这条路径上不可用：Docker Desktop 的 bind mount 上 os.stat 的
        属性缓存会陈旧（实测滞后 30+ 分钟），宿主机写入看不见 —— 一条在 checkpoint
        中间打开的坏连接不会被 maybe_reopen 换掉，于是每次表查询都失败，直到进程
        重启。2026-10-08 indicators 全量 400 `Serialization Error` 就是这么来的。
        """
        if not self._swap_connection(self._file_signature()):
            return False
        logger.warning("DuckDB 连接读到不一致的元数据，已换新连接 %s（generation=%d）",
                       self.name, self._generation)
        return True

    async def _heal_and_run(self, run_once):
        """读带重试：连接读到不一致的元数据就换一条新连接再跑一次。

        长驻连接是在某次 ETL checkpoint 中间打开的，它把写到一半的元数据块当成了
        块指针，之后每一次表查询都会失败；而签名探测在 bind mount 上看不到宿主机
        写入，不会触发重开。没有这一层，坏连接会一直坏到进程重启。

        只对"读到不一致内容"这一类错误重试，SQL 错误（Binder/Parser/权限）原样
        抛出 —— 否则每次笔误都会白白丢掉连接。
        """
        last: Optional[BaseException] = None
        for attempt in range(CORRUPTION_RETRIES + 1):
            try:
                return await run_once()
            except Exception as exc:  # noqa: BLE001
                if not _looks_like_corrupt_read(exc):
                    raise
                last = exc
                if attempt >= CORRUPTION_RETRIES:
                    break
                # 先等再重开：写完一个 checkpoint 需要几秒，立刻重开会再次读到
                # 中间态（实测三次立即重试全部落在同一个窗口里 → 503）。
                delay = CORRUPTION_RETRY_DELAY * (attempt + 1)
                if delay:
                    await asyncio.sleep(delay)
                if not await asyncio.to_thread(self._reopen_broken):
                    # 重开都失败，再试只会拿到同样的错误，早点把原因说清楚
                    break
                logger.warning(
                    "查询读到不一致的 DuckDB 元数据 %s，换连接重试（第 %d 次）：%s",
                    self.name, attempt + 1, exc,
                )
        raise DuckDBCorruptReadError(self.name, self.db_path, last) from last

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

        Raises:
            DuckDBCorruptReadError: 连接读到写到一半的元数据，换连接重试后仍失败。
        """
        async with self._semaphore:
            async def run_once() -> List[Dict[str, Any]]:
                if self._conn is None:
                    await self.initialize()
                else:
                    # 查数据之前先探一下版本：ETL 昨晚重算过，这里就会重开连接。
                    await asyncio.to_thread(self.maybe_reopen)
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, self._execute_sync, query, params
                )

            return await self._heal_and_run(run_once)

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
            async def run_once() -> Any:
                if self._conn is None:
                    await self.initialize()
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, self._execute_scalar_sync, query, params
                )

            return await self._heal_and_run(run_once)

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
