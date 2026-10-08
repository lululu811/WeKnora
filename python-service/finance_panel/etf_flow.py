"""
ETF 大资金流向分析 — 纯规则层，不依赖 FastAPI / DuckDB。

为什么份额变动比成交额更值得看
------------------------------
成交额放大可能只是高频资金来回对倒；份额增加才意味着有真实增量资金在场外
申购进来。所以"大资金追踪"的主信号是**份额存量变动**，成交额只作为佐证。

不变式
------
* **缺失 ≠ 0。** 库内没有份额记录 / 观测点不足 / 基准份额缺失，一律
  `{missing: True, reason: ...}`。`share_change_pct=0` 在金融语义里是"份额没动"，
  拿它顶替"没数据"会读出一个假结论。
* **粒度如实标注。** 现有份额源是**季频**（东财 F10 规模变动页），所以
  `share_change_pct` 实为"相对上一报告期"而非"日度"。字段名保留日度语义是为了
  与下游 DuckDB 日频口径对齐，实际观测频率由 `granularity` + `change_basis`
  两个字段说明，**不靠字段名骗人**。
* **所有数字 Python 算完**，HTTP 层只做序列化。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 信号阈值（与 volume 倍数阈值 1.382 同源，都用斐波那契位）。
SIGNAL_SHARE_PCT = 2.0        # |份额变动| > 2%
SIGNAL_TURNOVER_MULTIPLE = 1.382  # 成交额放大倍率 > 1.382

DEFAULT_WINDOW = 5            # 近 5 日累计变动

# 交叉验证容差：披露占比 vs 份额总量反算，±5 个百分点算对得上。
RECONCILE_TOLERANCE_PCT = 5.0

STATUS_VERIFIED = "verified"
STATUS_DISPUTED = "disputed"
STATUS_PENDING = "pending"


def _missing(thscode: str, reason: str) -> Dict[str, Any]:
    """缺失结果的统一形状 —— 与 `pulse._missing` 同约定。"""
    return {"thscode": thscode, "missing": True, "reason": reason}


def pct_change(current: float, base: float) -> Optional[float]:
    """百分比变动。基准为 0 / None → None（除零没有"无穷大"这个正确答案）。"""
    if current is None or base is None or base == 0:
        return None
    return (float(current) / float(base) - 1.0) * 100.0


def compute_flow(
    thscode: str,
    name: str = "",
    shares: Optional[Sequence[Dict[str, Any]]] = None,
    turnover: Optional[float] = None,
    close: Optional[float] = None,
    multiple: Optional[float] = None,
    window: int = DEFAULT_WINDOW,
    signal_share_pct: float = SIGNAL_SHARE_PCT,
    signal_multiple: float = SIGNAL_TURNOVER_MULTIPLE,
) -> Dict[str, Any]:
    """计算单只 ETF 的资金流向。

    Args:
        thscode: 库内 thscode，如 "510300.SH"。原样回填。
        name: 展示名。缺失不影响计算。
        shares: **按日期升序**的份额观测
            `[{trade_date, shares_outstanding, share_change, granularity}]`。
            None / 空 → 缺失。
        turnover: 当日成交额（元）。None → 字段为 null，不当 0。
        close: 当日收盘价。None → 字段为 null。
        multiple: 当日成交额相对中枢的放大倍率，**由调用方算好传入**
            （需要行情序列，规则层只做纯计算）。None → 不参与信号判定。
        window: 累计变动窗口（观测点数）。

    Returns:
        正常：`{thscode, name, close, turnover, shares_outstanding,
        share_change_pct, change_5d_pct, signal, ...}`
        缺失：`{thscode, name, missing: True, reason}`
    """
    rows = list(shares or [])
    if not rows:
        return _missing(thscode, "库内无 ETF 份额观测（先跑 /api/finance/etf-flow/sync）")

    latest = rows[-1]
    latest_shares = latest.get("shares_outstanding")
    if latest_shares is None:
        return _missing(thscode, f"最新一条份额缺失（trade_date={latest.get('trade_date')}）")

    granularity = latest.get("granularity") or "unknown"

    # ---- 单期变动%：优先用落库的 share_change，回退到相邻两期现算 ----
    prev = rows[-2] if len(rows) >= 2 else None
    change_pct: Optional[float] = None
    if prev is not None and prev.get("shares_outstanding") is not None:
        change_pct = pct_change(latest_shares, prev.get("shares_outstanding"))

    # ---- 窗口累计变动%：当前 vs 往前第 window 期 ----
    change_5d_pct: Optional[float] = None
    change_5d_reason: Optional[str] = None
    if len(rows) >= window + 1:
        base = rows[-(window + 1)].get("shares_outstanding")
        change_5d_pct = pct_change(latest_shares, base)
        if change_5d_pct is None:
            change_5d_reason = f"窗口基准份额缺失（trade_date={rows[-(window + 1)].get('trade_date')}）"
    else:
        # 观测点不足就明说有几条，而不是拿现有几条算一个"5 日"变动。
        change_5d_reason = f"观测点不足：需要 {window + 1} 条，实际 {len(rows)} 条"

    # ---- 信号标记：份额变动 与 成交放大 任一超阈值 ----
    signal = mark_signal(
        {
            "share_change_pct": None if change_pct is None else change_pct,
        },
        multiple=multiple,
        share_pct=signal_share_pct,
        mult_threshold=signal_multiple,
    )

    return {
        "thscode": thscode,
        "name": name or "",
        # 显式 False 而不是省略这个键：halo 的「同一形状」不变式要求调用方
        # 永远能读 result["missing"]，缺键会变成 KeyError 而不是 False。
        "missing": False,
        "close": None if close is None else float(close),
        "turnover": None if turnover is None else float(turnover),
        "shares_outstanding": float(latest_shares),
        "share_change_pct": None if change_pct is None else round(change_pct, 4),
        "change_5d_pct": None if change_5d_pct is None else round(change_5d_pct, 4),
        "turnover_multiple": None if multiple is None else round(float(multiple), 4),
        "signal": signal,
        "granularity": granularity,
        # 观测基准：把"日度/季频"这件事显式写进响应，前端不必猜字段名。
        "change_basis": (
            "prev_observation" if len(rows) >= 2 else "no_prev_observation"
        ),
        "observations": len(rows),
        "trade_date": latest.get("trade_date"),
        "reason": change_5d_reason,
    }


def mark_signal(
    flow: Dict[str, Any],
    multiple: Optional[float] = None,
    share_pct: float = SIGNAL_SHARE_PCT,
    mult_threshold: float = SIGNAL_TURNOVER_MULTIPLE,
) -> bool:
    """信号标记：|份额变动%| > 阈值 **或** 成交放大倍率 > 阈值。

    纯函数且**不看缺失项**：任一指标缺失时该指标不参与判定，另一个仍可触发。
    两项都缺 → False（没有信号 ≠ 有反信号）。
    """
    if flow.get("missing"):
        return False
    pct = flow.get("share_change_pct")
    if pct is not None and abs(float(pct)) > share_pct:
        return True
    if multiple is not None and float(multiple) > mult_threshold:
        return True
    return False


def reconcile_status(
    hold_pct: Optional[float],
    hold_share: Optional[float],
    total_shares: Optional[float],
    tolerance: float = RECONCILE_TOLERANCE_PCT,
) -> str:
    """披露占比 vs 份额总量反算 → verified / disputed / pending。

    * **pending** —— 缺交叉数据（没有份额总量，或披露值本身缺失）。缺数据
      不是"对不上"，混进 disputed 会让真正的口径冲突被淹没。
    * **disputed** —— 两个口径都齐且对不上。**保留原始值不改写**。

    注意粒度差异：披露占比是季末时点值，份额总量若来自季频源可同口径对比；
    跨频对比会天然产生偏差，因此调用方只在频率一致时才传 total_shares。
    """
    if hold_pct is None or hold_share is None or not total_shares:
        return STATUS_PENDING
    implied = float(hold_share) / float(total_shares) * 100.0
    if abs(implied - float(hold_pct)) <= tolerance:
        return STATUS_VERIFIED
    return STATUS_DISPUTED


def is_huijin(name: Optional[str]) -> bool:
    """识别中央汇金 / 证金系持有人。

    **关键词必须枚举到主体名，不能只写「证金」**：证金公司的法定全称是
    「中国证券金融股份有限公司」，"证金" 两个字在这串里**并不相邻**
    （证券金融 = 证·券·金·融）。只匹配「证金」会把最有代表性的国家队主体
    整类漏掉 —— 这正是实测踩到的坑。
    """
    if not name:
        return False
    return any(k in name for k in ("汇金", "证金", "证券金融"))


# ----------------------------------------------------------------------
# 组装层（DuckDB 只读）
# ----------------------------------------------------------------------
#
# 以上是纯函数、不碰 DuckDB；以下是唯一接触 I/O 的部分，规则计算仍然全部
# 调用上面的纯函数完成。DuckDB 由 registry 以 read_only=True 建连，
# 本模块只 SELECT，不写、不建、不迁移任何 .duckdb。

# 池内 ETF 的行情窗口。日频成交额取最近 window+1 条，当日与中枢分开取。
_QUOTE_SQL = """
WITH codes AS (
    SELECT * FROM (VALUES {placeholders}) AS t(raw)
),
resolved AS (
    SELECT DISTINCT u.thscode AS thscode, u.name AS name
    FROM codes c
    JOIN v_etf_universe u
      ON u.thscode = c.raw OR u.ticker = c.raw
),
target AS (
    SELECT COALESCE(CAST(? AS DATE), (SELECT MAX(trade_date) FROM v_etf_daily)) AS d
)
SELECT s.thscode AS thscode, s.trade_date AS trade_date, s.close AS close,
       s.turnover AS turnover, r.name AS name
FROM v_etf_daily s
JOIN resolved r ON r.thscode = s.thscode
CROSS JOIN target t
WHERE s.trade_date <= t.d
  AND s.trade_date >= t.d - (? * INTERVAL 1 DAY)
ORDER BY s.thscode, s.trade_date
"""

# 必须 AS 别名：裸 MAX(trade_date) 的结果列名是 "max(trade_date)"（小写），
# 按 "trade_date" 取会取不到，trade_date 就静默变成 None。
_LATEST_SQL = "SELECT MAX(trade_date) AS trade_date FROM v_etf_daily"


def _as_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value)
    return s[:10] if s else None


async def fetch_quotes(
    src,
    codes: Sequence[str],
    window: int = DEFAULT_WINDOW,
    target_date: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """一次批量 SQL 取回池内 ETF 的行情窗口。

    Returns:
        `{thscode: {name, trade_date, close, turnover, multiple}}`。
        `multiple` = 当日成交额 / 之前 window 日均额（**不含当日**，
        与 `pulse` 的中枢口径一致）。
    """
    if not codes:
        return {}
    sql = _QUOTE_SQL.format(placeholders=",".join("(?)" for _ in codes))
    params: List[Any] = list(codes)
    params.append(target_date or None)
    params.append(max(window, 1) * 3)  # 自然日回看，留足节假日余量
    rows = await src.execute(sql, params)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    names: Dict[str, str] = {}
    for r in rows:
        thscode = r.get("thscode")
        if not thscode:
            continue
        grouped.setdefault(thscode, []).append(r)
        if r.get("name"):
            names.setdefault(thscode, r["name"])

    out: Dict[str, Dict[str, Any]] = {}
    for thscode, bars in grouped.items():
        last = bars[-1]
        center = [
            b.get("turnover")
            for b in bars[-(window + 1):-1]
            if b.get("turnover") is not None
        ]
        multiple: Optional[float] = None
        if center and last.get("turnover") is not None:
            avg = sum(center) / len(center)
            # 中枢为 0 → 倍数无意义（除零），保持 None 而不是 0。
            if avg > 0:
                multiple = last["turnover"] / avg
        out[thscode] = {
            "name": names.get(thscode, ""),
            "trade_date": _as_date(last.get("trade_date")),
            "close": last.get("close"),
            "turnover": last.get("turnover"),
            "multiple": multiple,
        }
    return out


async def latest_trade_date(src) -> Optional[str]:
    rows = await src.execute(_LATEST_SQL)
    if not rows:
        return None
    return _as_date(rows[0].get("trade_date") or rows[0].get("MAX(trade_date)"))


async def compute_flows(
    src,
    pool: Sequence[Dict[str, str]],
    window: int = DEFAULT_WINDOW,
    target_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """端点 A 的完整组装逻辑。

    Args:
        pool: `etf_shares.load_pool()` 的返回值（`[{code, name}]`）。
        db_path: SQLite 路径，透传给 store。

    Returns:
        `{ok, trade_date, items, missing}`，`items` 按 `abs(share_change_pct)`
        降序。`share_change_pct` 为 null 的项**排到末尾**而不是当 0 参与排序 ——
        Python 的 `abs(None)` 会直接抛错，且把缺失当 0 会让缺失项挤在中段。
    """
    from . import store  # 局部导入：store 依赖 sqlite，纯规则层不需要

    codes = [p["code"] for p in (pool or []) if p.get("code")]
    if not codes:
        return {"ok": False, "trade_date": None, "items": [], "missing": [],
                "reason": "ETF 追踪池为空（检查 etf_pool.yaml）"}

    quotes = await fetch_quotes(src, codes, window=window, target_date=target_date)
    trade_date = _as_date(target_date) if target_date else await latest_trade_date(src)

    items: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []

    for entry in pool:
        code = entry.get("code")
        if not code:
            continue
        thscode = next(
            (t for t in quotes if t == code or t.startswith(code + ".")), ""
        )
        quote = quotes.get(thscode) if thscode else None
        # 份额按**池内裸代码**查询：upsert 时写入的就是 pool 裸代码（端点在
        # 注入 r["thscode"] = code 时用的是 pool_codes），若改用解析后的
        # 带后缀 thscode（510300.SH）会一行都匹配不上。
        series = store.query_shares(code, db_path=db_path)
        result = compute_flow(
            thscode or code,
            name=(quote or {}).get("name") or entry.get("name", ""),
            shares=series,
            turnover=(quote or {}).get("turnover"),
            close=(quote or {}).get("close"),
            multiple=(quote or {}).get("multiple"),
            window=window,
        )
        if result.get("missing"):
            missing.append({"thscode": code, "name": result.get("name", ""),
                            "reason": result.get("reason")})
            continue
        items.append(result)

    def _sort_key(r: Dict[str, Any]):
        pct = r.get("share_change_pct")
        # None 排最后：缺失不参与排序，也不被当成 0 混在中段。
        return (pct is None, -abs(float(pct)))

    items.sort(key=_sort_key)

    return {
        "ok": True,
        "trade_date": trade_date,
        "items": items,
        "missing": missing,
        "counts": {"total": len(codes), "included": len(items), "missing": len(missing)},
    }