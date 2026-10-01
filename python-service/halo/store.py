"""
HALO 年报事实库 — 落表与查询

为什么是 SQLite 而不是 DuckDB
------------------------------
`datasources.duckdb_source.DuckDBSource` 一律以 `read_only=True` 打开，且
它服务于「一次扫几十万行做聚合」的分析型查询。HALO 要做的是相反的事：
把少量抽取结果**写进去**，再按 (thscode, field, period) **精确点查**出来。

- 落表需要对文件有写权限，而 registry 里的 7 个 DuckDB 全部只读；
- SQLite 是标准库，零新增依赖（本服务已在容器里跑，多一个 C 扩展就多一处
  版本对齐）；
- 单文件 + 事务可靠，容器重启不会像自建 DuckDB 那样因为另一个进程持有
  读锁而打不开；
- 点查走主键，HALO 每次分析只取一只股票的几个字段，不需要列式扫描。

代价是没有向量化分析能力——但这正是这里不需要的能力。

数据可信度约定
--------------
`status` 决定一条记录能不能被评分使用：

- ``verified``  — 规则抽取与 LLM 抽取一致，或与 DuckDB 对账通过
- ``disputed``  — 两条通道不一致，待人工按 ``source_page`` 回原文裁决
- ``pending``   — 只有一条通道产出（通常是规则抽不到、只有 LLM 给了数）

**`disputed` 与 `pending` 一律不得进入评分公式**，调用方（未来的评分内核）
必须把它们当作缺失处理并显式标注，绝不能用另一口径的数顶替。这条对应
HALO 的数据铁律：取不到就标缺失，不估算、不补值。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

# 允许进入评分公式的记录状态。
STATUS_VERIFIED = "verified"
STATUS_DISPUTED = "disputed"
STATUS_PENDING = "pending"

# verified 的来源标注。两者都算可信，但依据不同，审计时必须能分开看：
# reconcile = 与 DuckDB 逐位对账通过，是直接的证据；
# pipeline  = 该 scope 下有口径的字段全部对账通过，说明抽取器本身准，本值
#             沿用这份可信度（详见 reconcile.promote_by_pipeline 的说明）。
VERIFIED_BY_RECONCILE = "reconcile"
VERIFIED_BY_PIPELINE = "pipeline"

# 报表口径。report_type 与 DuckDB 的 period 列（annual/quarterly）不同：
# 这里要区分年报/半年报/季报，因为员工数等字段只在年报出现。
REPORT_ANNUAL = "annual"
REPORT_H1 = "h1"
REPORT_Q1 = "q1"
REPORT_Q3 = "q3"
REPORT_TYPES = (REPORT_ANNUAL, REPORT_H1, REPORT_Q1, REPORT_Q3)

# 报表范围。年报里同时有合并报表与母公司报表，两套数字不同。
# HALO 六维要的是合并口径，但抽取时必须显式记录取的是哪一套，否则
# 对账会把「口径不同」误报成「抽错了」。
SCOPE_CONSOLIDATED = "consolidated"
SCOPE_PARENT = "parent"

# 抽取通道。
EXTRACT_RULE = "rule"
EXTRACT_LLM = "llm"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS halo_filing_facts (
    thscode      TEXT NOT NULL,
    period       TEXT NOT NULL,
    report_type  TEXT NOT NULL,
    field        TEXT NOT NULL,
    value        REAL,
    -- 枚举型事实（审计意见类型等）存这里。value 留给数值，布尔编码成 0/1
    -- 以便参与计算（内控非标=1 应当影响风险评分，而不是只被展示）。
    value_text   TEXT,
    unit         TEXT,
    scope        TEXT NOT NULL,
    source_page  INTEGER,
    raw_text     TEXT,
    extract_by   TEXT,
    status       TEXT NOT NULL,
    confidence   REAL,
    verified_by  TEXT,
    verified_at  TEXT,
    -- value_text 参与主键：标量字段（固定资产/员工数）每股一条，它的
    -- value_text 为空；分部字段（segment_revenue）则「一个 field 多条」，
    -- 按业务名区分。不把它放进主键，upsert 会让同 field 的各业务互相覆盖
    -- —— 实测茅台 7 个业务分部被压成 1 个「直销」。
    PRIMARY KEY (thscode, period, report_type, field, scope, value_text)
);
CREATE INDEX IF NOT EXISTS idx_facts_code_period
    ON halo_filing_facts (thscode, period);
"""


def default_db_path() -> str:
    """落库路径。

    默认与 hithink 数据同目录但独立文件——同目录便于运维找得到，独立文件
    是因为那是只读的 hithink 库，绝不能被我们写坏。
    """
    return os.path.expanduser(
        os.getenv("HALO_DB_PATH", "~/.hithink-finance/halo.sqlite")
    )


class FactStore:
    """halo_filing_facts 的读写入口。

    连接是线程本地的一只：sqlite3 连接默认 ``check_same_thread=True``，
    而 FastAPI 的端点在线程池里跑同步代码，跨线程复用同一连接会直接抛
    ProgrammingError。这里每次操作开短连接，SQLite 单写多读本就够用，
    没必要引入连接池。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_db_path()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with self._connect() as conn:
                # WAL：写入方（本模块）与读取方（评分查询）可以并发，
                # 不再互相拿全局锁。
                conn.execute("PRAGMA journal_mode=WAL")
                # 旧版表的主键不含 value_text，CREATE TABLE IF NOT EXISTS 不会
                # 改它，于是 upsert 继续按旧键覆盖分部行。事实表全部可由
                # halo.filing.sync 重新生成，重建的成本远低于带着静默覆盖
                # 继续跑。
                if self._needs_rebuild(conn):
                    conn.execute("DROP TABLE IF EXISTS halo_filing_facts")
                conn.executescript(_SCHEMA)
            self._initialized = True

    @staticmethod
    def _needs_rebuild(conn: sqlite3.Connection) -> bool:
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='halo_filing_facts'"
            ).fetchone()
        except sqlite3.Error:
            return False
        if row is None:
            return False
        ddl = (row[0] or "").lower()
        if "value_text" in ddl:
            return False
        return True

    def upsert(self, records: Iterable[Dict[str, Any]]) -> int:
        """写入/覆盖事实记录，返回受影响行数。

        同一 (thscode, period, report_type, field, scope) 视为同一条事实：
        重新抽取（更准的通道、修正后的页码）应当覆盖旧值，而不是堆积多行。
        """
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            status = r.get("status") or STATUS_PENDING
            rows.append((
                r["thscode"],
                r["period"],
                r["report_type"],
                r["field"],
                r.get("value"),
                r.get("value_text"),
                r.get("unit"),
                r.get("scope", SCOPE_CONSOLIDATED),
                r.get("source_page"),
                r.get("raw_text"),
                r.get("extract_by"),
                status,
                r.get("confidence"),
                r.get("verified_by"),
                r.get("verified_at") or (now if status == STATUS_VERIFIED else None),
            ))
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO halo_filing_facts
                    (thscode, period, report_type, field, value, value_text,
                     unit, scope, source_page, raw_text, extract_by, status,
                     confidence, verified_by, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (thscode, period, report_type, field, scope, value_text) DO UPDATE SET
                    value       = excluded.value,
                    value_text  = excluded.value_text,
                    unit        = excluded.unit,
                    source_page = excluded.source_page,
                    raw_text    = excluded.raw_text,
                    extract_by  = excluded.extract_by,
                    status      = excluded.status,
                    confidence  = excluded.confidence,
                    verified_by = excluded.verified_by,
                    verified_at = excluded.verified_at
                """,
                rows,
            )
        return len(rows)

    def query(
        self,
        thscode: str,
        *,
        period: Optional[str] = None,
        report_type: Optional[str] = None,
        fields: Optional[List[str]] = None,
        scope: Optional[str] = None,
        only_verified: bool = True,
    ) -> List[Dict[str, Any]]:
        """按股票取事实。

        Args:
            only_verified: 默认只返回 ``verified``。评分内核应当保持 True，
                这样 disputed/pending 的记录不会悄悄流进公式——把它们
                当作「缺失」是调用方的义务，不该由查询层悄悄放行。
        """
        sql = "SELECT * FROM halo_filing_facts WHERE thscode = ?"
        params: List[Any] = [thscode]
        if period:
            sql += " AND period = ?"
            params.append(period)
        if report_type:
            sql += " AND report_type = ?"
            params.append(report_type)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if fields:
            placeholders = ",".join("?" for _ in fields)
            sql += f" AND field IN ({placeholders})"
            params.extend(fields)
        if only_verified:
            sql += " AND status = ?"
            params.append(STATUS_VERIFIED)
        # 同一字段可能有多期，按期末倒序；同日多 scope 时按 scope 名稳定排序，
        # 保证测试与前端渲染可复现。
        sql += " ORDER BY period DESC, field ASC, scope ASC"

        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def latest_period(self, thscode: str, report_type: Optional[str] = None) -> Optional[str]:
        """该股票最新一期报告期（仅计 verified），没有则返回 None。"""
        sql = "SELECT MAX(period) AS p FROM halo_filing_facts WHERE thscode = ? AND status = ?"
        params: List[Any] = [thscode, STATUS_VERIFIED]
        if report_type:
            sql += " AND report_type = ?"
            params.append(report_type)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row["p"] if row and row["p"] else None

    def status_summary(self, thscode: str) -> Dict[str, int]:
        """各状态的记录数，用于在报告里如实呈现「取到了多少、待核多少」。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM halo_filing_facts "
                "WHERE thscode = ? GROUP BY status",
                (thscode,),
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}
