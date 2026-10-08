"""
财经日历本地存储 — SQLite。

为什么单独落库
--------------
东财财经日历接口只在前端点"刷新"时才去抓，一旦抓不到（比如 WAF 拦了、
限速触发），面板就空了。落一份本地表之后，GET 只读本地，任何时候都有东西
可显示；抓取失败只影响"数据有多新"，不影响"有没有数据"。

路径取 env `FINANCE_PANEL_DB`，默认 `~/.hithink-finance/finance_panel.sqlite`。
注意这个库里**只有**日历文本，没有任何行情表 —— 行情永远从 DuckDB 只读取。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# 表结构按 (event_date, title) 唯一去重：同一场发布会东财会在多次同步里
# 重复返回，不去重的话日历面板会越刷越长。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_calendar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date  TEXT NOT NULL,
    title       TEXT NOT NULL,
    category    TEXT,
    source      TEXT,
    payload_json TEXT,
    fetched_at  TEXT,
    UNIQUE (event_date, title)
);
CREATE INDEX IF NOT EXISTS idx_finance_calendar_date
    ON finance_calendar (event_date);

-- ETF 份额存量。local .duckdb 的 v_etf_daily 只有价量额、没有份额，
-- 这是"大资金追踪"唯一的新数据。见 finance_panel/etf_shares.py 的口径说明。
--
-- `granularity` 必须落库：东财 F10 给的是**季频报告期**份额，直接当"日度份额
-- 变动"读会得出"汇金今天在买"这种根本不存在的精度。不标粒度 = 口径信息丢失。
--
-- `share_change` 落库而非读时算：份额是存量数据，变动量依赖"上一条已落库的
-- 记录"，读时算在历史补录/乱序增量下不可复现。
CREATE TABLE IF NOT EXISTS etf_share_daily (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    thscode           TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    shares_outstanding REAL,
    share_change      REAL,
    source            TEXT,
    granularity       TEXT,
    fetched_at        TEXT,
    UNIQUE (thscode, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_etf_share_date
    ON etf_share_daily (trade_date);

-- 定期报告披露的「前十名持有人」。与 etf_share_daily 是两条独立的线：
-- 份额是高频代理指标，持有人是低频但权威的原始披露。
--
-- `status` 沿用 halo 的三态口径：verified / disputed / pending。
-- **disputed 的行保留原始抽取值不改写** —— 对不上就是对不上，把数字"修正"成
-- 看起来自洽的样子会彻底毁掉这一列的可信度。
CREATE TABLE IF NOT EXISTS etf_disclosed_holding (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thscode       TEXT NOT NULL,
    report_period TEXT NOT NULL,
    holder_name   TEXT NOT NULL,
    hold_share    REAL,
    hold_pct      REAL,
    status        TEXT,
    source_title  TEXT,
    fetched_at    TEXT,
    UNIQUE (thscode, report_period, holder_name)
);
CREATE INDEX IF NOT EXISTS idx_etf_holding_period
    ON etf_disclosed_holding (report_period);
"""

DEFAULT_DB_PATH = os.path.join("~", ".hithink-finance", "finance_panel.sqlite")

_lock = threading.Lock()


def resolve_db_path() -> str:
    """FINANCE_PANEL_DB → 展开用户目录的绝对路径。"""
    return os.path.expanduser(os.getenv("FINANCE_PANEL_DB") or DEFAULT_DB_PATH)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = os.path.expanduser(db_path) if db_path else resolve_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """建表。幂等，重复调用安全。"""
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def upsert_events(
    events: Iterable[Dict[str, Any]],
    source: str = "eastmoney",
    db_path: Optional[str] = None,
) -> int:
    """写入一批事件，按 (event_date, title) 去重。

    用 `INSERT ... ON CONFLICT DO UPDATE` 而不是 `INSERT OR IGNORE`：重复
    抓到的同一条事件如果 category 变了（东财改了分类），要能更新过去，
    否则库里会一直留着旧的错误分类。payload 与 fetched_at 一起刷新。

    Returns:
        本次真正写入（新增或更新）的行数。
    """
    init_db(db_path)
    payload = [
        (
            str(e.get("date"))[:10],
            str(e.get("title") or "").strip(),
            (e.get("category") or None),
            source,
            json.dumps(e.get("payload"), ensure_ascii=False) if e.get("payload") is not None else None,
            datetime.now().isoformat(timespec="seconds"),
        )
        for e in events
        if e.get("date") and str(e.get("title") or "").strip()
    ]
    if not payload:
        return 0

    with _lock:
        conn = _connect(db_path)
        try:
            cur = conn.executemany(
                """
                INSERT INTO finance_calendar
                    (event_date, title, category, source, payload_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_date, title) DO UPDATE SET
                    category     = excluded.category,
                    source       = excluded.source,
                    payload_json = excluded.payload_json,
                    fetched_at   = excluded.fetched_at
                """,
                payload,
            )
            conn.commit()
            # sqlite3 的 rowcount 在 executemany 下是累计变更行数。
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(payload)
        finally:
            conn.close()


def query_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按日期升序读事件。端点只要 date/title/category 三个字段。"""
    sql = "SELECT event_date, title, category FROM finance_calendar"
    clauses: List[str] = []
    params: List[Any] = []
    if start_date:
        clauses.append("event_date >= ?")
        params.append(str(start_date)[:10])
    if end_date:
        clauses.append("event_date <= ?")
        params.append(str(end_date)[:10])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY event_date ASC, title ASC"

    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # 表还没建（第一次读就撞上 sync 失败）时返回空，而不是 500。
            return []
        finally:
            conn.close()

    return [
        {"date": r[0], "title": r[1], "category": r[2] or ""}
        for r in rows
    ]


# ----------------------------------------------------------------------
# ETF 份额
# ----------------------------------------------------------------------


def upsert_shares(
    rows: Iterable[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> int:
    """写入一批 ETF 份额观测，按 (thscode, trade_date) 去重。

    `share_change` 在写库时按**同批次内该代码的上一条**（升序）计算；批次起点
    接上一条库内记录。计算放在这里而不是读时，理由见表注释。

    缺失的 `shares_outstanding`（东财给了 '---'）写 NULL 而不是 0。
    """
    init_db(db_path)
    items = [r for r in rows or [] if r.get("thscode") and r.get("trade_date")]
    if not items:
        return 0

    # 按代码分组升序，逐组算变动。变动 = 本期 - 上期。
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for r in items:
        by_code.setdefault(str(r["thscode"]), []).append(r)

    now = datetime.now().isoformat(timespec="seconds")
    payload: List[tuple] = []
    for thscode, group in by_code.items():
        group.sort(key=lambda r: str(r["trade_date"]))
        prev: Optional[float] = None
        # 批次起点接库内最后一条，让增量续抓后的第一行也有变动量。
        prev = _last_share(thscode, db_path)
        for r in group:
            shares = r.get("shares_outstanding")
            shares = None if shares is None else float(shares)
            change = None
            if shares is not None and prev is not None:
                change = shares - prev
            payload.append(
                (
                    thscode,
                    str(r["trade_date"])[:10],
                    shares,
                    change,
                    r.get("source"),
                    r.get("granularity"),
                    now,
                )
            )
            # 只有拿到有效份额才更新 prev：一条缺失不能让后续变动全变成 None。
            if shares is not None:
                prev = shares

    with _lock:
        conn = _connect(db_path)
        try:
            conn.executemany(
                """
                INSERT INTO etf_share_daily
                    (thscode, trade_date, shares_outstanding, share_change,
                     source, granularity, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (thscode, trade_date) DO UPDATE SET
                    shares_outstanding = excluded.shares_outstanding,
                    share_change      = excluded.share_change,
                    source            = excluded.source,
                    granularity       = excluded.granularity,
                    fetched_at        = excluded.fetched_at
                """,
                payload,
            )
            conn.commit()
            return len(payload)
        finally:
            conn.close()


def _last_share(thscode: str, db_path: Optional[str] = None) -> Optional[float]:
    """库内该 ETF 最后一个**有效**份额值（按日期倒序，跳过 NULL）。

    跳过 NULL 是必须的：最近一条如果是缺失记录，用它当基准会让整批增量
    的变动量全部变成"相对一个不存在的值"。
    """
    with _lock:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT shares_outstanding FROM etf_share_daily
                WHERE thscode = ? AND shares_outstanding IS NOT NULL
                ORDER BY trade_date DESC LIMIT 1
                """,
                (thscode,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
    return float(row[0]) if row and row[0] is not None else None


def latest_share_dates(
    codes: Iterable[str],
    db_path: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """每只 ETF 库内最新份额日期 —— 增量抓取的续抓起点。

    返回值里可能含 None（库里还没有该代码），调用方据此决定全量抓。
    """
    out: Dict[str, Optional[str]] = {}
    with _lock:
        conn = _connect(db_path)
        try:
            for code in codes or []:
                row = conn.execute(
                    "SELECT MAX(trade_date) FROM etf_share_daily WHERE thscode = ?",
                    (str(code),),
                ).fetchone()
                out[str(code)] = row[0] if row and row[0] else None
        except sqlite3.OperationalError:
            return {str(c): None for c in (codes or [])}
        finally:
            conn.close()
    return out


def query_shares(
    thscode: str,
    db_path: Optional[str] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """取单只 ETF 的份额序列（**按日期升序**）。

    limit 取最近 N 条后仍返回升序 —— 分析层算 5 日累计变动依赖升序，
    这里把方向定死，避免调用方各自 reverse 一次。
    """
    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT trade_date, shares_outstanding, share_change, granularity
                FROM (
                    SELECT trade_date, shares_outstanding, share_change, granularity
                    FROM etf_share_daily
                    WHERE thscode = ? AND shares_outstanding IS NOT NULL
                    ORDER BY trade_date DESC LIMIT ?
                ) ORDER BY trade_date ASC
                """,
                (str(thscode), int(limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    return [
        {
            "trade_date": r[0],
            "shares_outstanding": r[1],
            "share_change": r[2],
            "granularity": r[3],
        }
        for r in rows
    ]


# ----------------------------------------------------------------------
# ETF 披露持有人
# ----------------------------------------------------------------------


def upsert_holdings(
    rows: Iterable[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> int:
    """写入披露持有人，按 (thscode, report_period, holder_name) 去重。

    同一报告期重复同步时刷新 status/占比 —— 后续交叉数据（份额总量）到位后
    需要把 pending 升级成 verified，这是 upsert 而不是 INSERT OR IGNORE 的原因。
    """
    init_db(db_path)
    items = [
        r
        for r in rows or []
        if r.get("thscode") and r.get("report_period") and r.get("holder_name")
    ]
    if not items:
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    payload = [
        (
            str(r["thscode"]),
            str(r["report_period"])[:10],
            str(r["holder_name"]).strip(),
            _opt_float(r.get("hold_share")),
            _opt_float(r.get("hold_pct")),
            r.get("status") or "pending",
            r.get("source_title"),
            now,
        )
        for r in items
    ]

    with _lock:
        conn = _connect(db_path)
        try:
            conn.executemany(
                """
                INSERT INTO etf_disclosed_holding
                    (thscode, report_period, holder_name, hold_share, hold_pct,
                     status, source_title, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (thscode, report_period, holder_name) DO UPDATE SET
                    hold_share   = excluded.hold_share,
                    hold_pct     = excluded.hold_pct,
                    status       = excluded.status,
                    source_title = excluded.source_title,
                    fetched_at   = excluded.fetched_at
                """,
                payload,
            )
            conn.commit()
            return len(payload)
        finally:
            conn.close()


def _opt_float(value: Any) -> Optional[float]:
    """None/''/非法值 → None。**空串绝不当 0**。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def known_report_periods(
    codes: Iterable[str],
    db_path: Optional[str] = None,
) -> Dict[str, List[str]]:
    """每只 ETF 库内已有的报告期（升序）—— 增量同步据此跳过已处理报告期。"""
    out: Dict[str, List[str]] = {}
    with _lock:
        conn = _connect(db_path)
        try:
            for code in codes or []:
                rows = conn.execute(
                    """
                    SELECT DISTINCT report_period FROM etf_disclosed_holding
                    WHERE thscode = ? ORDER BY report_period ASC
                    """,
                    (str(code),),
                ).fetchall()
                out[str(code)] = [r[0] for r in rows if r[0]]
        except sqlite3.OperationalError:
            return {str(c): [] for c in (codes or [])}
        finally:
            conn.close()
    return out


def query_holdings(
    thscode: str,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """读某只 ETF 的披露持有人，按报告期倒序、同期按占比降序。"""
    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT report_period, holder_name, hold_share, hold_pct, status
                FROM etf_disclosed_holding
                WHERE thscode = ?
                ORDER BY report_period DESC, hold_pct DESC
                """,
                (str(thscode),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    return [
        {
            "thscode": str(thscode),
            "report_period": r[0],
            "holder_name": r[1],
            "hold_share": r[2],
            "hold_pct": r[3],
            "status": r[4],
        }
        for r in rows
    ]
