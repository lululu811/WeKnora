"""集合竞价 —— 盘前那一分钟的信息。

为什么值得单独一块
------------------
收盘复盘回答"今天发生了什么"，集合竞价回答"**明天/今天怎么开**"。它是大盘
唯一能比昨天更早拿到全市场读数的那一刻：5471 只票在 9:15–9:25 之间各报一次价，
高低开的家数、谁直接顶在涨停上，开盘前十分钟就全知道了。

库里已有 `special.v_auction_snapshot`（每日 5471 行，含竞价涨幅 / 量比 / 换手 /
流通市值）与 `special.v_auction_benchmark`（短线风向标，带行业标签）。
**这两个视图此前没有任何端点在读。**

两条设计决定，都来自实测数据而非偏好
------------------------------------
1. **必须按流通市值设门槛。** 全市场量比>2 的有 910 只，但排到前面的
   「量比 70 倍」有一大半是几亿市值的小盘票 —— 那个倍数意味着 200 万手就能打
   出来，是流动性噪音不是资金信号。实测 50 亿门槛下同样的量比只剩 395 只，
   而排第一的园林股份（52 亿、开盘涨停、量比 70）是**真信号**。
   门槛可调（`min_float_cap`），默认 50 亿，理由写在这里不藏在代码里。

2. **分布和榜单分开给。** 「今天多少只高开」是全市场 5471 只的统计，
   不设门槛也成立；而「哪几只异动」必须过门槛。两个口径混在一张榜里，
   高开家数会被榜单的数量级盖过去。

口径
----
* **空 ≠ 零**：`auction_pct` 实测存在 null（如星帅尔 2026-10-09），
  那是"没报出竞价价"，不是"平开"。排序与着色都按缺失处理。
* **stage 只有 `final`。** 库里有 live/final 两态，实测历史数据全是 final
  （终态 = 9:25 集合竞价撮合完成）。live 阶段的数据采集目前没跑，
  所以响应里回带 `stage`，前端可以据此说明"这是终态不是盘中实时"。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 流通市值门槛（元）。低于这个体量的票，竞价量比倍数不可比 —— 见模块头。
DEFAULT_MIN_FLOAT_CAP = 5e9

#: 榜单默认条数。
DEFAULT_LIMIT = 20


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


async def auction_overview(
    src_special: Any,
    date: Optional[str] = None,
    min_float_cap: float = DEFAULT_MIN_FLOAT_CAP,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """集合竞价全景：全市场开高开低分布 + 短线风向标 + 过门槛的异动榜。"""
    if src_special is None:
        return {"ok": False, "date": None, "stage": None, "breadth": None,
                "benchmark": [], "movers": [], "unavailable": ["special"],
                "reason": "special 数据源未就绪"}

    where = "WHERE snapshot_date = ?" if date else "WHERE snapshot_date = (SELECT max(snapshot_date) FROM v_auction_snapshot)"
    params: List[Any] = [date] if date else []

    # ---- 1. 日期与阶段 ----
    try:
        head = await src_special.execute(
            f"""
            SELECT max(snapshot_date) AS d,
                   arg_max(stage, snapshot_date) AS stage
            FROM v_auction_snapshot {where}
            """,
            params,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("集合竞价：取日期失败：%s", exc)
        return {"ok": False, "date": None, "stage": None, "breadth": None,
                "benchmark": [], "movers": [], "unavailable": ["special"],
                "reason": f"查询失败：{exc}"}

    if not head or head[0].get("d") is None:
        return {"ok": True, "date": None, "stage": None, "breadth": None,
                "benchmark": [], "movers": [], "unavailable": [],
                "reason": "库内无集合竞价快照"}

    snap_date = head[0].get("d")
    stage = head[0].get("stage")

    # ---- 2. 全市场分布（不设市值门槛：这是统计，不是榜单） ----
    breadth: Optional[Dict[str, Any]] = None
    try:
        b = await src_special.execute(
            """
            SELECT
              SUM(CASE WHEN auction_pct IS NULL THEN 1 ELSE 0 END)   AS no_quote,
              SUM(CASE WHEN auction_pct > 0 THEN 1 ELSE 0 END)       AS up,
              SUM(CASE WHEN auction_pct < 0 THEN 1 ELSE 0 END)       AS down,
              SUM(CASE WHEN auction_pct = 0 THEN 1 ELSE 0 END)       AS flat,
              SUM(CASE WHEN auction_pct >= 9.9 THEN 1 ELSE 0 END)    AS limit_up_open,
              SUM(CASE WHEN auction_pct <= -9.9 THEN 1 ELSE 0 END)   AS limit_down_open,
              SUM(CASE WHEN auction_pct > 0.5 THEN 1 ELSE 0 END)     AS up_over_half,
              COUNT(*)                                                AS total
            FROM v_auction_snapshot WHERE snapshot_date = ?
            """,
            [snap_date],
        )
        if b:
            row = b[0]
            breadth = {
                # 没报价的单列一栏：它既不是高开也不是低开，塞进任何一边都会错。
                "no_quote": int(row.get("no_quote") or 0),
                "up": int(row.get("up") or 0),
                "down": int(row.get("down") or 0),
                "flat": int(row.get("flat") or 0),
                "limit_up_open": int(row.get("limit_up_open") or 0),
                "limit_down_open": int(row.get("limit_down_open") or 0),
                "up_over_half": int(row.get("up_over_half") or 0),
                "total": int(row.get("total") or 0),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("集合竞价分布查询失败：%s", exc)

    # ---- 3. 短线风向标（带行业标签的少量代表） ----
    benchmark: List[Dict[str, Any]] = []
    try:
        rows = await src_special.execute(
            """
            SELECT thscode, name, auction_pct, tags
            FROM v_auction_benchmark
            WHERE benchmark_date = ?
            ORDER BY auction_pct DESC NULLS LAST
            """,
            [snap_date],
        )
        for r in rows:
            tags = [t.strip() for t in (r.get("tags") or "").split(",") if t.strip()]
            benchmark.append({
                "thscode": r.get("thscode"),
                "name": r.get("name"),
                "auction_pct": _f(r.get("auction_pct")),
                # tags 是逗号分隔的字符串，空串 → 空数组，不是 [""]。
                "tags": tags,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("集合竞价风向标查询失败：%s", exc)

    # ---- 4. 异动榜（过市值门槛 + 量比，按量比降序） ----
    movers: List[Dict[str, Any]] = []
    try:
        rows = await src_special.execute(
            """
            SELECT thscode, name, auction_pct, auction_volume_ratio,
                   auction_turnover_pct, float_market_cap
            FROM v_auction_snapshot
            WHERE snapshot_date = ?
              AND float_market_cap IS NOT NULL AND float_market_cap >= ?
              AND auction_volume_ratio IS NOT NULL
            ORDER BY auction_volume_ratio DESC NULLS LAST
            LIMIT ?
            """,
            [snap_date, float(min_float_cap), int(limit)],
        )
        for r in rows:
            movers.append({
                "thscode": r.get("thscode"),
                "name": r.get("name"),
                "auction_pct": _f(r.get("auction_pct")),
                "volume_ratio": _f(r.get("auction_volume_ratio")),
                "turnover_pct": _f(r.get("auction_turnover_pct")),
                "float_market_cap": _f(r.get("float_market_cap")),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("集合竞价异动榜查询失败：%s", exc)

    return {
        "ok": True,
        "date": snap_date,
        "stage": stage,
        "breadth": breadth,
        "benchmark": benchmark,
        "movers": movers,
        "min_float_cap": float(min_float_cap),
        "unavailable": [],
        "note": "分布为全市场统计不设门槛；异动榜按流通市值门槛过滤，量比在极小市值票上不可比",
    }
