"""
量价异动组装层 — DuckDB 取数 + 分组计算 + coverage 汇总。

为什么是"一次批量 SQL"
----------------------
自选股面板一次要算几十只票。逐票查询意味着几十次连接往返，在只读单连接
（`DuckDBSource` 用 `_conn_lock` 串行化）的约束下会直接串成一条长队。这里
改成**一条 SQL 取回所有标的的窗口数据**，分组计算全在 Python 侧做 ——
规则本来就在 `pulse.compute_pulse` 这个纯函数里，搬到 Python 不损失什么，
却省掉了 N-1 次往返。

取数口径
--------
* **volume 取 `v_daily`**（原始成交量，除权不影响成交额口径）
* **close 取 `v_daily_qfq`**（前复权价，除权日不产生假暴跌）
* 两个视图 join 在 (thscode, date) 上；**任一侧缺失就整行丢掉**，不做
  left join 补 None —— 补出来的 None 在中枢里会让中位数失真。
* 时间窗口取 `window * 3` 个自然日：20 个交易日 ≈ 28 个自然日，乘 3 留足
  节假日余量，保证窗口内一定能凑够 `window + 1` 条。

DuckDB 全程**只读**（`DuckDBSource` 由 registry 以 `read_only=True` 建连），
本模块不写、不建、不迁移任何 .duckdb 文件。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .pulse import (
    DEFAULT_HIGH,
    DEFAULT_LOW,
    DEFAULT_WINDOW,
    VOLUME_DOWN,
    VOLUME_UP,
    compute_pulse,
)

logger = logging.getLogger(__name__)

# 批量取数 SQL。`resolved` 负责把 "301190" 和 "301190.SZ" 两种写法都收敛到
# 库内标准 thscode；`latest` 给出库内最新交易日，用户没指定 date 时用它。
_BATCH_SQL = """
WITH codes AS (
    SELECT * FROM (VALUES {placeholders}) AS t(raw)
),
resolved AS (
    SELECT DISTINCT s.thscode AS thscode
    FROM codes c
    JOIN v_symbol s ON s.thscode = c.raw OR s.ticker = c.raw
),
target AS (
    SELECT COALESCE(CAST(? AS DATE), (SELECT MAX(date) FROM v_daily WHERE interval = '1d')) AS d
)
SELECT d.thscode AS thscode, d.date AS date, d.volume AS volume, q.close AS qfq_close
FROM v_daily d
JOIN v_daily_qfq q
  ON q.thscode = d.thscode AND q.date = d.date AND q.interval = '1d'
CROSS JOIN target t
WHERE d.interval = '1d'
  AND d.date <= t.d
  AND d.date >= t.d - (? * INTERVAL 1 DAY)
  AND d.thscode IN (SELECT thscode FROM resolved)
ORDER BY d.thscode, d.date
"""

_LATEST_SQL = "SELECT MAX(date) AS d FROM v_daily WHERE interval = '1d'"


def _as_date(value: Any) -> Optional[str]:
    """date / datetime / str → 'YYYY-MM-DD'。取不到返回 None，不猜。"""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str) and value:
        return value[:10]
    return None


def normalize_codes(raw_codes: Sequence[str]) -> List[str]:
    """清洗入参：去空白、去重、保持顺序、大写。

    **不做**在这里补后缀 —— "301190" → "301190.SZ" 依赖 v_symbol 的映射，
    交给 SQL 里的 `resolved` CTE 统一做，避免在这里硬编码一段会随
    交易所规则变化的推导逻辑。
    """
    seen: Dict[str, None] = {}
    for item in raw_codes or []:
        if not isinstance(item, str):
            continue
        code = item.strip().upper()
        if code:
            seen.setdefault(code, None)
    return list(seen)


async def resolve_codes(src, raw_codes: Sequence[str]) -> Dict[str, str]:
    """把入参映射到库内标准 thscode。

    Returns:
        `{原始写法: 标准 thscode}`。映射不上的**不在** dict 里 —— 调用方
        据此把它报成"代码不存在"缺失项，而不是静默丢掉。
    """
    codes = normalize_codes(raw_codes)
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = await src.execute(
        f"""
        SELECT thscode, ticker FROM v_symbol
        WHERE thscode IN ({placeholders}) OR ticker IN ({placeholders})
        """,
        codes + codes,
    )
    mapping: Dict[str, str] = {}
    for r in rows:
        thscode, ticker = r.get("thscode"), r.get("ticker")
        if thscode and ticker:
            mapping.setdefault(str(ticker).upper(), str(thscode))
        if thscode:
            mapping.setdefault(str(thscode).upper(), str(thscode))
    return mapping


async def fetch_batch(
    src,
    codes: Sequence[str],
    window: int = DEFAULT_WINDOW,
    target_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """一次批量 SQL 取回所有标的的窗口数据。"""
    if not codes:
        return []
    sql = _BATCH_SQL.format(placeholders=",".join("(?)" for _ in codes))
    params: List[Any] = list(codes)
    params.append(target_date or None)          # COALESCE(?, 最新交易日)
    params.append(max(window, 1) * 3)           # 自然日回看窗
    return await src.execute(sql, params)


async def latest_trade_date(src) -> Optional[str]:
    """库内最新交易日。取不到返回 None（不补今天 —— 今天可能还没收盘）。"""
    rows = await src.execute(_LATEST_SQL)
    if not rows:
        return None
    return _as_date(rows[0].get("d"))


async def compute_pulses(
    src,
    raw_codes: Sequence[str],
    target_date: Optional[str] = None,
    window: int = DEFAULT_WINDOW,
    low: float = DEFAULT_LOW,
    high: float = DEFAULT_HIGH,
) -> Dict[str, Any]:
    """端点的完整组装逻辑。

    Returns:
        `{ok, trade_date, coverage, items, missing}`。
        `items` 按 `abs(multiple - 1)` 降序 —— 离常态越远越靠前，面板
        不用在前端再排一次。
    """
    codes = normalize_codes(raw_codes)
    if not codes:
        return {
            "ok": False,
            "trade_date": None,
            "coverage": {"total": 0, "included": 0, "volume_up": 0, "volume_down": 0},
            "items": [],
            "missing": [],
            "reason": "codes 为空",
        }

    mapping = await resolve_codes(src, codes)

    unresolved = [c for c in codes if c not in mapping]
    resolved_codes = [mapping[c] for c in codes if c in mapping]

    rows = await fetch_batch(src, resolved_codes, window=window, target_date=target_date)

    # 分组：thscode -> [(date, volume, qfq_close), ...]，SQL 已 ORDER BY 升序
    series: Dict[str, List[Tuple[Any, float, float]]] = {}
    for r in rows:
        series.setdefault(r["thscode"], []).append(
            (r.get("date"), r.get("volume"), r.get("qfq_close"))
        )

    # 实际使用的交易日：指定了就用指定的（即使当天无数据也照实返回，
    # 前端据此显示"该日无行情"，而不是偷偷换成另一天），没指定就用库内最新。
    if target_date:
        trade_date = _as_date(target_date)
    else:
        trade_date = _as_date(rows[-1].get("date")) if rows else await latest_trade_date(src)

    items: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []

    for raw in codes:
        if raw in unresolved:
            missing.append({"thscode": raw, "reason": "代码不在本地行情库（v_symbol 无此代码）"})
            continue

        thscode = mapping[raw]
        bars = series.get(thscode) or []
        if not bars:
            missing.append({"thscode": raw, "reason": f"{trade_date or '指定日期'} 无行情数据"})
            continue

        result = compute_pulse(thscode, bars, window=window, low=low, high=high)
        if result.get("missing"):
            # 回报原始写法而不是归一化后的代码，前端要用它对齐请求列表。
            missing.append({"thscode": raw, "reason": result.get("reason")})
        else:
            # 回填原始写法 + 归一化代码两个字段：前者对齐 UI 列表，后者
            # 让前端点进去查详情时能直接用。
            result["thscode"] = raw
            result["code"] = thscode
            result["date"] = _as_date(result.get("date")) or result.get("date")
            items.append(result)

    items.sort(key=lambda r: abs((r.get("multiple") or 0.0) - 1.0), reverse=True)

    coverage = {
        "total": len(codes),
        "included": len(items),
        "volume_up": sum(1 for r in items if r.get("volume_state") == VOLUME_UP),
        "volume_down": sum(1 for r in items if r.get("volume_state") == VOLUME_DOWN),
    }

    return {
        "ok": True,
        "trade_date": trade_date,
        "coverage": coverage,
        "items": items,
        "missing": missing,
    }
