"""板块 / 情绪 / 期股联动 —— 大盘工作台的数据层。

为什么单独一个模块
------------------
`/api/market/*` 之前只有两个端点（snapshot、dragon-tiger），而库里躺着的东西
远不止那点：848 个板块指数、858 个指数的五年日线、连板梯队、集合竞价、股指期货。
**这些数据没有任何端点在读，前端 proxy 路由里连 sector/index/board 都没有。**
所以这不是"再加几张卡"，是把一批没有出口的数据接到界面上。

本模块只做取数与口径，不做展示。展示在大盘 tab（`MarketDashboard.vue`）。

口径约定（全模块统一，不逐条重复）
--------------------------------
* **空 ≠ 零。** 查不到就是 null。0 在金融语义里是"今天真的没动"，用它顶替缺失
  会读出一个不存在的结论（同一约定见 `main.py` 的 market_snapshot 文档串）。
* **每个数据块独立降级。** 任何一个数据源不可用只让对应块为空并记进
  `unavailable`，不拖垮整页 —— 期货库挂了不能导致板块榜也不显示。
* **多天口径必须写清基准日。** `trade_date` 与 `as_of` 一起返回，前端不需要猜。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 板块指数的四种分类。与 `index.v_index_universe.tag` 的取值一一对应。
SECTOR_TAGS: Tuple[str, ...] = ("industry", "concept", "tszs", "region")

#: 板块榜默认回看多少个交易日算放量倍数。20 日中位数比 5 日稳、比 60 日灵。
_SECTOR_LOOKBACK_DAYS = 40


def _f(v: Any) -> Optional[float]:
    """转 float；null / NaN / 不可解析一律 None（绝不返回 0 顶替缺失）。"""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN != NaN


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or not b:
        return None
    return (a - b) / b * 100.0


# ══════════════════════════════════════════════════════════════════════════
# 板块指数
# ══════════════════════════════════════════════════════════════════════════

_SECTOR_SQL = """
WITH hist AS (
    SELECT thscode, trade_date, close, turnover,
           LAG(close) OVER (PARTITION BY thscode ORDER BY trade_date) AS prev_close,
           MEDIAN(turnover) OVER (
               PARTITION BY thscode ORDER BY trade_date
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS turnover_ma20
    FROM v_index_daily
    WHERE trade_date >= ?
)
SELECT h.thscode, h.trade_date, h.close, h.prev_close, h.turnover,
       h.turnover_ma20, u.name AS name, u.tag AS tag
FROM hist h
JOIN v_index_universe u ON u.thscode = h.thscode
WHERE h.trade_date = (SELECT max(trade_date) FROM hist)
"""


async def list_sectors(
    src_index: Any,
    src_special: Any,
    tag: Optional[str] = None,
    limit: int = 60,
    lookback_days: int = _SECTOR_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """板块指数今日榜。

    **默认排序是三个独立信号依次比较，不是加权合成分**：涨停家数 desc →
    放量倍数 desc → |涨跌幅| desc。理由：合成分必须给权重，而权重是拍的；
    字典序能表达"先看有没有涨停，再看有没有放量，最后才看涨了多少"这个
    真实的判断顺序，而且每一档用户都能自己改。

    涨停家数走 special 库，与板块榜所在库不同，**不能一条 SQL join** ——
    所以先取今日涨停股代码（约百来个），再用这些代码去 index 库反查板块归属，
    而不是把 12 万行的成分表整个拉出来（那会撞上 python-service 单查询 100k
    上限，见 `zettaranc/filters.py` 的同一处说明）。
    """
    unavailable: List[str] = []
    if src_index is None:
        return {"ok": False, "items": [], "trade_date": None,
                "unavailable": ["index"], "reason": "index 数据源未就绪"}

    # 先取最新交易日，再由它反推窗口起点。
    # 不能直接把"回看 N 个交易日"当参数塞进 `trade_date >= ?` —— 那个位置要的是
    # DATE 而非整数（duckdb 会报 "Cannot compare values of type DATE and INTEGER"）。
    # 交易日 ≠ 自然日，所以窗口按自然日放宽 1.5 倍，宁可多取几行也不取少了。
    try:
        latest_rows = await src_index.execute(
            "SELECT max(trade_date) AS d FROM v_index_daily", []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("板块榜：取最新交易日失败：%s", exc)
        return {"ok": False, "items": [], "trade_date": None,
                "unavailable": ["index"], "reason": f"查询失败：{exc}"}

    latest = latest_rows[0].get("d") if latest_rows else None
    if latest is None:
        return {"ok": True, "trade_date": None, "items": [], "unavailable": [],
                "reason": "index 库内无指数日线"}

    start = latest - timedelta(days=int(lookback_days * 1.5))

    params: List[Any] = [start]
    sql = _SECTOR_SQL
    if tag and tag in SECTOR_TAGS:
        sql += " AND u.tag = ?\n"
        params.append(tag)
    # 排序在 Python 里做，不在 SQL 里。涨停家数来自 special 库、板块日线来自
    # index 库，**跨库没法 join**，所以这个排序列直到 Python 侧合并后才存在 ——
    # 写在 SQL 的 ORDER BY 里会直接报 "Referenced column not found"。
    # 代价是本分类的板块全量过网（最多 390 行），换来实现正确。
    try:
        rows = await src_index.execute(sql, params)
    except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
        logger.warning("板块榜查询失败：%s", exc)
        return {"ok": False, "items": [], "trade_date": None,
                "unavailable": ["index"], "reason": f"查询失败：{exc}"}

    trade_date = rows[0].get("trade_date") if rows else None

    # ---- 涨停家数：另一个库，先取涨停代码再反查 ----
    limit_up_by_sector: Dict[str, int] = {}
    if src_special is not None and rows:
        try:
            pool = await src_special.execute(
                "SELECT DISTINCT thscode FROM v_limit_up_pool WHERE trade_date = ?",
                [trade_date],
            )
            codes = [str(r.get("thscode") or "") for r in pool if r.get("thscode")]
            if codes:
                ph = ",".join("(?)" for _ in codes)
                own = await src_index.execute(
                    f"""
                    SELECT index_thscode, COUNT(DISTINCT thscode) AS n
                    FROM v_index_constituents
                    WHERE thscode IN ({ph})
                    GROUP BY 1
                    """,
                    codes,
                )
                for r in own:
                    limit_up_by_sector[str(r.get("index_thscode"))] = int(r.get("n") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("板块涨停家数反查失败：%s", exc)
            unavailable.append("special")

    items: List[Dict[str, Any]] = []
    for r in rows:
        close = _f(r.get("close"))
        prev_close = _f(r.get("prev_close"))
        turnover = _f(r.get("turnover"))
        ma20 = _f(r.get("turnover_ma20"))
        items.append({
            "thscode": r.get("thscode"),
            "name": r.get("name"),
            "tag": r.get("tag"),
            "close": close,
            "change_pct": _pct(close, prev_close),
            "turnover": turnover,
            # 放量倍数：成交额 / 近 20 日中位额。分母缺失时是 null，
            # 不是 1.0 —— 拿 1.0 顶替会让"没有历史"被读成"量能正常"。
            "volume_multiple": (turnover / ma20) if (turnover is not None and ma20) else None,
            # 该板块内今日涨停的家数。0 是真的"一只没停"，与"没查到"不同，
            # 所以反查失败时这整个块作废而不是给 0。
            "limit_up_count": limit_up_by_sector.get(str(r.get("thscode"))) if not unavailable
            else None,
        })

    # 字典序：涨停家数 → 放量倍数 → |涨跌幅|。缺失一律排最后（不当 0 参与比较）。
    def _sort_key(i: Dict[str, Any]) -> Tuple[int, float, float]:
        lu = i["limit_up_count"]
        vm = i["volume_multiple"]
        cp = i["change_pct"]
        return (
            -(lu if lu is not None else -1),
            -(vm if vm is not None else -1.0),
            -(abs(cp) if cp is not None else -1.0),
        )

    items.sort(key=_sort_key)
    items = items[:limit]

    return {
        "ok": True,
        "trade_date": trade_date,
        "items": items,
        "total": len(rows),
        "unavailable": unavailable,
        "sort_rule": "limit_up_count > volume_multiple > abs(change_pct)",
    }


# ══════════════════════════════════════════════════════════════════════════
# 连板梯队
# ══════════════════════════════════════════════════════════════════════════

async def limit_up_ladder(src_special: Any, days: int = 30) -> Dict[str, Any]:
    """近 N 个交易日 × 连板档位（2/3/4/5/6/7+ 板）的家数矩阵。

    这是市场情绪的位置感：涨停家数只告诉你"今天热不热"，梯队告诉你
    "热度在往上走还是在塌"——连板高度从 2 板堆到 7 板，和每天都是 2 板，
    是完全不同的两种市场。
    """
    if src_special is None:
        return {"ok": False, "days": [], "unavailable": ["special"],
                "reason": "special 数据源未就绪"}
    try:
        rows = await src_special.execute(
            """
            SELECT trade_date, board_level, SUM(board_num) AS board_num,
                   SUM(COALESCE(sign_level, 0)) AS sign_level
            FROM v_limit_up_ladder
            GROUP BY 1, 2
            ORDER BY 1 DESC, 2 ASC
            """,
            [],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("连板梯队查询失败：%s", exc)
        return {"ok": False, "days": [], "unavailable": ["special"],
                "reason": f"查询失败：{exc}"}

    by_date: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        d = r.get("trade_date")
        if d is None:
            continue
        by_date.setdefault(d, []).append({
            "board_level": r.get("board_level"),
            "board_num": int(r.get("board_num") or 0),
            "sign_level": int(r.get("sign_level") or 0),
        })

    dates = sorted(by_date.keys(), reverse=True)[:days]
    return {
        "ok": True,
        "days": [{"trade_date": d, "levels": by_date[d]} for d in dates],
        "unavailable": [],
    }


# ══════════════════════════════════════════════════════════════════════════
# 期股联动：股指期货基差
# ══════════════════════════════════════════════════════════════════════════

#: 品种 → (主力连续合约, 对应现货指数, 中文名)
#:
#: **必须用主力连续（ZL）而不是挂牌月**：IF2610 / IF2612 / IF2703 是不同到期日的
#: 合约，拿它们直接减现货指数，算出来的是**跨月价差**不是基差，换月那天会出现
#: 一个根本不存在的跳空。库里已有 ZL 合成合约（v_futures_daily 里 4 个股指品种
#: 各一条），它已经把换月接好了。
#:
#: 期货报价单位就是指数点位（实测 IF 收 4248 对沪深300 现货 4310），
#: 所以不需要乘数换算，直接相减。
_INDEX_FUTURES: Tuple[Tuple[str, str, str, str], ...] = (
    ("IF", "IFZL.CFE", "000300.SH", "沪深300"),
    ("IC", "ICZL.CFE", "000905.SH", "中证500"),
    ("IH", "IHZL.CFE", "000016.SH", "上证50"),
    ("IM", "IMZL.CFE", "000852.SH", "中证1000"),
)


def _percentile(values: Sequence[float], current: float) -> Optional[float]:
    """当前值在历史序列里的百分位（0~100）。

    为什么不给绝对值当结论：「-1.4% 贴水」本身没有信息量 —— 股指期货常年贴水，
    -1.4% 是常态还是极端只有历史能回答。分位数才把这个数变成判断。
    """
    if not values:
        return None
    below = sum(1 for v in values if v < current)
    return round(below / len(values) * 100.0, 1)


async def index_futures_basis(
    src_futures: Any,
    src_index: Any,
    lookback_days: int = 1250,
) -> Dict[str, Any]:
    """股指期货基差（期货主力连续 − 现货指数），附 5 年分位数。

    **两个日期必须同一天。** 实测期货只更新到 T-1、现货已到 T，期货与现货的
    最新日不同。拿 T-1 的期货减 T 的现货，会把整整一天的涨跌混进"基差"里 ——
    那不是基差，是跨日差，而这个错算完之后看起来完全正常。

    所以 `basis_date` 取**两边共同的最新交易日**，并单独把 `spot_latest` 返回
    给前端用于"今天涨跌多少"—— 那是另一件事，不该借用基差日的数字。
    """
    unavailable: List[str] = []
    if src_futures is None or src_index is None:
        missing = [n for n, s in (("futures", src_futures), ("index", src_index)) if s is None]
        return {"ok": False, "items": [], "basis_date": None, "spot_latest": None,
                "unavailable": missing, "reason": f"数据源未就绪：{'、'.join(missing)}"}

    # ---- 现货：全量序列（5 年 × 4 个指数 ≈ 4900 行，单次返回无压力）----
    spot_codes = [spot for _, _, spot, _ in _INDEX_FUTURES]
    spot_latest_date: Optional[Any] = None
    spot_hist: Dict[str, Dict[Any, float]] = {}
    try:
        ph = ",".join("(?)" for _ in spot_codes)
        rows = await src_index.execute(
            f"""
            SELECT thscode, trade_date, close
            FROM v_index_daily
            WHERE thscode IN ({ph})
            ORDER BY thscode, trade_date
            """,
            spot_codes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("基差-现货序列查询失败：%s", exc)
        return {"ok": False, "items": [], "basis_date": None, "spot_latest": None,
                "unavailable": ["index"], "reason": f"查询失败：{exc}"}

    for r in rows:
        code = str(r.get("thscode"))
        d = r.get("trade_date")
        c = _f(r.get("close"))
        if d is None or c is None:
            continue
        spot_hist.setdefault(code, {})[d] = c
        if spot_latest_date is None or d > spot_latest_date:
            spot_latest_date = d

    items: List[Dict[str, Any]] = []
    basis_date: Optional[Any] = None
    futures_latest: Optional[Any] = None
    for variety, fut_code, spot_code, name in _INDEX_FUTURES:
        spot_series = spot_hist.get(spot_code) or {}
        if not spot_series:
            continue
        try:
            frows = await src_futures.execute(
                """
                SELECT trade_date, close_price
                FROM v_futures_daily
                WHERE thscode = ?
                ORDER BY trade_date
                """,
                [fut_code],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("基差-期货序列查询失败 %s：%s", fut_code, exc)
            unavailable.append("futures")
            continue

        fut_series: Dict[Any, float] = {}
        for r in frows:
            d = r.get("trade_date")
            c = _f(r.get("close_price"))
            if d is not None and c is not None:
                fut_series[d] = c
        if not fut_series:
            continue

        f_latest = max(fut_series)
        if futures_latest is None or f_latest > futures_latest:
            futures_latest = f_latest

        # 共同交易日 = 两边都有的最大日期。这是唯一正确的基差日。
        common = sorted(set(fut_series) & set(spot_series))
        if not common:
            continue
        day = common[-1]
        if basis_date is None or day > basis_date:
            basis_date = day

        fut_close = fut_series[day]
        spot_close = spot_series[day]
        basis = fut_close - spot_close
        basis_pct = _pct(fut_close, spot_close)

        # 历史序列：只取共同交易日，且截到 lookback_days 个点（≈5 年）。
        window = common[-lookback_days:]
        pairs: List[Tuple[Any, float]] = []
        for d in window:
            p = _pct(fut_series[d], spot_series[d])
            if p is not None:
                pairs.append((d, p))
        hist_pct = [p for _, p in pairs]

        # 近 60 日的「基差率 + 现货日涨跌」双序列，供前端画背离对照。
        # 日涨跌用**前一个共同交易日**做分母 —— 用自然日前一天会在停牌/缺采样时
        # 把两天的涨跌算进一天，那会让背离信号完全失真。
        tail = pairs[-60:]
        series: List[Dict[str, Any]] = []
        for i, (d, bp) in enumerate(tail):
            spot_day_pct = None
            if i > 0:
                prev_d = tail[i - 1][0]
                spot_day_pct = _pct(spot_series[d], spot_series[prev_d])
            series.append({
                "trade_date": d,
                "basis_pct": round(bp, 4),
                "spot_pct": round(spot_day_pct, 3) if spot_day_pct is not None else None,
            })

        items.append({
            "variety": variety,
            "name": name,
            "futures_thscode": fut_code,
            "spot_thscode": spot_code,
            "futures_close": fut_close,
            "spot_close": spot_close,
            "basis": round(basis, 2),
            "basis_pct": round(basis_pct, 4) if basis_pct is not None else None,
            # 贴水（<0）在 A 股语境下意味着对冲盘重 / 情绪弱，用绿色；
            # 这与大盘页「红涨绿跌」同一套语义，不另造颜色。
            "percentile": _percentile(hist_pct, basis_pct) if basis_pct is not None else None,
            "history_size": len(hist_pct),
            "series": series,
        })

    return {
        "ok": True,
        "basis_date": basis_date,
        "futures_latest": futures_latest,
        "spot_latest": spot_latest_date,
        "items": items,
        "unavailable": unavailable,
        "note": "基差 = 主力连续收盘 − 现货指数收盘；percentile 为其在历史中的百分位",
    }


# ══════════════════════════════════════════════════════════════════════════
# 行业归属（批量）
# ══════════════════════════════════════════════════════════════════════════

async def industry_map_batch(src_index: Any, thscodes: Sequence[str]) -> Dict[str, Any]:
    """一次查一批股票的行业归属（{thscode: {level1, level2}}）。

    前端 `finance/api/watchlist.ts` 已经声明了这个端点（`fetchIndustryMap`）但
    后端一直 404。这里按它声明的形状实现，并把段位规则从 `halo.industry_map`
    直接 import —— **不复制那条规则**，否则两处会各自漂移。

    与 halo 版的差别只有一个：halo 版是单只股票一次查询（分析路径用），
    这里一次查一批（工作台一次要渲染整张自选表）。
    """
    from halo.industry_map import _FINE_LEVEL_HINT_MAX_MEMBERS, _LEVEL_PREFIX

    if src_index is None:
        return {"ok": False, "data": {}, "unavailable": ["index"],
                "reason": "index 数据源未就绪"}
    codes = [str(c).strip() for c in (thscodes or []) if str(c).strip()]
    if not codes:
        return {"ok": True, "data": {}, "unavailable": []}

    try:
        ph = ",".join("(?)" for _ in codes)
        rows = await src_index.execute(
            f"""
            SELECT c.thscode AS stock, u.thscode AS index_code,
                   u.name AS industry_name,
                   (SELECT COUNT(*) FROM v_index_constituents c2
                     WHERE c2.index_thscode = u.thscode) AS members
            FROM v_index_constituents c
            JOIN v_index_universe u ON c.index_thscode = u.thscode
            WHERE c.thscode IN ({ph}) AND u.tag = 'industry'
            ORDER BY stock, members DESC
            """,
            codes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("行业归属批量查询失败：%s", exc)
        return {"ok": False, "data": {}, "unavailable": ["index"],
                "reason": f"查询失败：{exc}"}

    by_stock: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_stock.setdefault(str(r.get("stock")), []).append(r)

    out: Dict[str, Dict[str, Optional[str]]] = {}
    for code in codes:
        ms = by_stock.get(code) or []
        level1 = level2 = None
        for m in ms:
            idx = str(m.get("index_code") or "")
            prefix = idx.split(".")[0][:3]
            level = _LEVEL_PREFIX.get(prefix)
            if level is None:
                level = 2 if (m.get("members") or 0) <= _FINE_LEVEL_HINT_MAX_MEMBERS else 1
            name = m.get("industry_name")
            if level == 1 and level1 is None:
                level1 = name
            elif level == 2 and level2 is None:
                level2 = name
        # 查不到就是 None（前端据此跳过行业判断），**不拿股票名顶上**。
        out[code] = {"level1": level1, "level2": level2}

    return {"ok": True, "data": out, "unavailable": []}
