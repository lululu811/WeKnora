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
from datetime import datetime, timedelta, timezone
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


# ══════════════════════════════════════════════════════════════════════════
# 期股联动：核心大宗商品看板
# ══════════════════════════════════════════════════════════════════════════

_COMMODITY_MAPPINGS = [
    {"variety": "CU", "name": "沪铜", "category": "有色金属", "a_share_hint": "紫金矿业 / 江西铜业 / 铜陵有色"},
    {"variety": "AL", "name": "沪铝", "category": "有色金属", "a_share_hint": "中国铝业 / 云铝股份 / 神火股份"},
    {"variety": "AU", "name": "沪金", "category": "贵金属", "a_share_hint": "山东黄金 / 中金黄金 / 赤峰黄金"},
    {"variety": "AG", "name": "沪银", "category": "贵金属", "a_share_hint": "盛达资源 / 银泰黄金"},
    {"variety": "RB", "name": "螺纹钢", "category": "黑色建材", "a_share_hint": "宝钢股份 / 华菱钢铁 / 中信特钢"},
    {"variety": "I", "name": "铁矿石", "category": "黑色建材", "a_share_hint": "河钢资源 / 金岭矿业"},
    {"variety": "SC", "name": "原油", "category": "能源化工", "a_share_hint": "中国海油 / 中国石油 / 中远海能"},
    {"variety": "EC", "name": "欧线集运", "category": "交运航运", "a_share_hint": "中远海控 / 招商轮船"},
    {"variety": "SA", "name": "纯碱", "category": "能源化工", "a_share_hint": "远兴能源 / 三友化工"},
    {"variety": "FG", "name": "玻璃", "category": "黑色建材", "a_share_hint": "旗滨集团 / 福耀玻璃"},
    {"variety": "LH", "name": "生猪", "category": "农林牧渔", "a_share_hint": "牧原股份 / 温氏股份 / 新希望"},
    {"variety": "M", "name": "豆粕", "category": "农林牧渔", "a_share_hint": "海大集团 / 大北农"},
]


async def list_commodities(src_futures: Any) -> Dict[str, Any]:
    """获取核心大宗商品期货最新行情、涨跌幅及关联 A 股板块提示。"""
    if src_futures is None:
        return {"ok": False, "items": [], "unavailable": ["futures"], "reason": "futures 数据源未就绪"}

    target_varieties = [m["variety"] for m in _COMMODITY_MAPPINGS]
    ph = ",".join("(?)" for _ in target_varieties)
    sql = f"""
    WITH ranked AS (
        SELECT variety_code, thscode, name, close_price, volume, turnover, trade_date
        FROM v_futures_latest
        WHERE variety_code IN ({ph})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY variety_code ORDER BY turnover DESC) = 1
    ),
    hist AS (
        SELECT d.thscode, d.trade_date, d.close_price,
               LAG(d.close_price) OVER (PARTITION BY d.thscode ORDER BY d.trade_date) AS prev_close
        FROM v_futures_daily d
        WHERE d.thscode IN (SELECT thscode FROM ranked)
          AND d.trade_date >= CURRENT_DATE - INTERVAL 30 DAY
    ),
    latest_hist AS (
        SELECT thscode, trade_date, close_price, prev_close,
               ROUND((close_price - prev_close) / NULLIF(prev_close, 0) * 100, 2) AS change_pct
        FROM hist
        QUALIFY ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY trade_date DESC) = 1
    )
    SELECT r.variety_code, r.name, r.thscode, r.close_price, lh.prev_close, lh.change_pct,
           r.volume, r.turnover, r.trade_date
    FROM ranked r
    LEFT JOIN latest_hist lh ON r.thscode = lh.thscode
    """

    try:
        rows = await src_futures.execute(sql, target_varieties)
    except Exception as exc:  # noqa: BLE001
        logger.warning("大宗商品期货行情查询失败：%s", exc)
        return {"ok": False, "items": [], "unavailable": ["futures"], "reason": f"查询失败：{exc}"}

    by_variety = {str(r.get("variety_code")): r for r in rows if r.get("variety_code")}
    trade_date = rows[0].get("trade_date") if rows else None

    items = []
    for meta in _COMMODITY_MAPPINGS:
        v = meta["variety"]
        r = by_variety.get(v)
        if not r:
            continue
        linked = [s.strip() for s in meta["a_share_hint"].split("/") if s.strip()]
        items.append({
            "variety": v,
            "name": meta["name"],
            "category": meta["category"],
            "a_share_hint": meta["a_share_hint"],
            "linked_stocks": linked,
            "thscode": r.get("thscode"),
            "contract_name": r.get("name"),
            "close_price": _f(r.get("close_price")),
            "prev_close": _f(r.get("prev_close")),
            "change_pct": _f(r.get("change_pct")),
            "volume": _f(r.get("volume")),
            "turnover": _f(r.get("turnover")),
            "trade_date": str(r.get("trade_date")) if r.get("trade_date") else None,
        })

    return {
        "ok": True,
        "trade_date": str(trade_date) if trade_date else None,
        "items": items,
        "unavailable": [],
    }


# ══════════════════════════════════════════════════════════════════════════
# 机构持仓：个股公募与 ETF 重仓穿透
# ══════════════════════════════════════════════════════════════════════════

async def get_stock_fund_holdings(src_fund: Any, stock_thscode: str, limit: int = 20) -> Dict[str, Any]:
    """穿透查询持有指定股票的公募基金与 ETF 重仓明细及机构集中度统计。"""
    if src_fund is None:
        return {"ok": False, "items": [], "unavailable": ["fund"], "reason": "fund 数据源未就绪"}

    code = str(stock_thscode).strip()
    if not code:
        return {"ok": False, "items": [], "reason": "stock_thscode 不能为空"}

    code_pattern = f"{code}.%" if "." not in code else code
    params = [code, code_pattern]

    sql = """
    SELECT h.fund_thscode,
           COALESCE(p.fund_name, e.name, h.fund_thscode) AS fund_name,
           p.manager_name,
           h.hold_ratio,
           h.position_count,
           h.security_market_value_rate_pct,
           h.period_increase_rate_pct,
           h.investment_rank,
           CASE WHEN e.thscode IS NOT NULL THEN 'ETF' ELSE '主动权益' END AS fund_category
    FROM v_fund_holdings h
    LEFT JOIN v_fund_profile p ON p.thscode = h.fund_thscode
    LEFT JOIN v_etf_universe e ON e.thscode = h.fund_thscode
    WHERE h.stock_thscode = ? OR h.stock_thscode LIKE ?
    ORDER BY h.hold_ratio DESC
    LIMIT ?
    """

    sum_sql = """
    SELECT COUNT(*) AS total_funds,
           ROUND(SUM(hold_ratio), 4) AS total_hold_ratio,
           ROUND(AVG(security_market_value_rate_pct), 4) AS avg_nav_ratio,
           COUNT(CASE WHEN period_increase_rate_pct > 0 THEN 1 END) AS increase_count,
           COUNT(CASE WHEN period_increase_rate_pct < 0 THEN 1 END) AS decrease_count,
           COUNT(CASE WHEN period_increase_rate_pct = 0 THEN 1 END) AS unchanged_count
    FROM v_fund_holdings
    WHERE stock_thscode = ? OR stock_thscode LIKE ?
    """

    try:
        rows = await src_fund.execute(sql, [code, code_pattern, limit])
        summary_rows = await src_fund.execute(sum_sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("基金持仓穿透查询失败 %s：%s", code, exc)
        return {"ok": False, "items": [], "unavailable": ["fund"], "reason": f"查询失败：{exc}"}

    summary = {}
    if summary_rows:
        sr = summary_rows[0]
        summary = {
            "total_funds": int(sr.get("total_funds") or 0),
            "total_hold_ratio": _f(sr.get("total_hold_ratio")),
            "avg_nav_ratio": _f(sr.get("avg_nav_ratio")),
            "increase_count": int(sr.get("increase_count") or 0),
            "decrease_count": int(sr.get("decrease_count") or 0),
            "unchanged_count": int(sr.get("unchanged_count") or 0),
        }

    items = []
    for r in rows:
        items.append({
            "fund_thscode": r.get("fund_thscode"),
            "fund_name": r.get("fund_name"),
            "fund_category": r.get("fund_category") or "公募基金",
            "manager_name": r.get("manager_name"),
            "hold_ratio": _f(r.get("hold_ratio")),
            "position_count": _f(r.get("position_count")),
            "security_market_value_rate_pct": _f(r.get("security_market_value_rate_pct")),
            "period_increase_rate_pct": _f(r.get("period_increase_rate_pct")),
            "investment_rank": r.get("investment_rank"),
        })

    return {
        "ok": True,
        "stock_thscode": code,
        "summary": summary,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


# ══════════════════════════════════════════════════════════════════════════
# 连板梯队详情、异动归因与炸板跌停监控
# ══════════════════════════════════════════════════════════════════════════

async def get_limit_ladder_detail(
    src_special: Any, min_board: int = 2, limit: int = 50
) -> Dict[str, Any]:
    """获取最新交易日连板梯队个股清单、空间板最高标及各档位明细。"""
    if src_special is None:
        return {"ok": False, "items": [], "unavailable": ["special"], "reason": "special 数据源未就绪"}

    sql = """
    SELECT trade_date, continue_day_cnt, thscode, name, last_price, limit_up_time, seal_money
    FROM v_limit_up_pool
    WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool)
      AND continue_day_cnt >= ?
    ORDER BY continue_day_cnt DESC, seal_money DESC
    LIMIT ?
    """
    try:
        rows = await src_special.execute(sql, [min_board, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("连板梯队详情查询失败：%s", exc)
        return {"ok": False, "items": [], "unavailable": ["special"], "reason": f"查询失败：{exc}"}

    items = []
    trade_date = None
    max_board = 0
    by_level: Dict[int, List[Dict[str, Any]]] = {}

    for r in rows:
        d = r.get("trade_date")
        if d and not trade_date:
            trade_date = str(d)
        cnt = int(r.get("continue_day_cnt") or 0)
        if cnt > max_board:
            max_board = cnt

        item = {
            "continue_day_cnt": cnt,
            "thscode": r.get("thscode"),
            "name": r.get("name"),
            "last_price": _f(r.get("last_price")),
            "limit_up_time": r.get("limit_up_time"),
            "seal_money": _f(r.get("seal_money")),
        }
        items.append(item)
        by_level.setdefault(cnt, []).append(item)

    return {
        "ok": True,
        "trade_date": trade_date,
        "max_board": max_board,
        "total_count": len(items),
        "items": items,
        "by_level": {f"{k}板": v for k, v in sorted(by_level.items(), reverse=True)},
        "unavailable": [],
    }


async def get_stock_anomaly_reasons(
    src_special: Any, thscode: Optional[str] = None, limit: int = 10
) -> Dict[str, Any]:
    """获取个股大涨/大跌异动归因分析、驱动逻辑与关键词。"""
    if src_special is None:
        return {"ok": False, "items": [], "unavailable": ["special"], "reason": "special 数据源未就绪"}

    code = str(thscode).strip() if thscode else ""
    if code:
        sql = """
        SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list
        FROM v_anomaly_list
        WHERE thscode = ? OR thscode LIKE ?
        ORDER BY capture_date DESC
        LIMIT ?
        """
        code_pat = f"{code}.%" if "." not in code else code
        params = [code, code_pat, limit]
    else:
        sql = """
        SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list
        FROM v_anomaly_list
        WHERE capture_date = (SELECT MAX(capture_date) FROM v_anomaly_list)
        ORDER BY capture_date DESC
        LIMIT ?
        """
        params = [limit]

    try:
        rows = await src_special.execute(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("异动归因查询失败 %s：%s", code, exc)
        return {"ok": False, "items": [], "unavailable": ["special"], "reason": f"查询失败：{exc}"}

    import json as _json

    items = []
    for r in rows:
        kw_raw = r.get("keyword_list")
        keywords = []
        if kw_raw:
            try:
                keywords = _json.loads(kw_raw) if isinstance(kw_raw, str) else list(kw_raw)
            except Exception:
                keywords = [str(kw_raw)]

        items.append({
            "capture_date": str(r.get("capture_date")) if r.get("capture_date") else None,
            "thscode": r.get("thscode"),
            "stock_name": r.get("stock_name"),
            "tag_name": r.get("tag_name"),
            "analysis_content": r.get("analysis_content"),
            "keywords": keywords,
        })

    return {
        "ok": True,
        "thscode": code or None,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


async def get_limit_break_and_down(src_special: Any, limit: int = 20) -> Dict[str, Any]:
    """获取最新炸板池（开板回落）与跌停池（极端核按钮亏钱效应）。"""
    if src_special is None:
        return {"ok": False, "break_pool": [], "down_pool": [], "unavailable": ["special"], "reason": "special 数据源未就绪"}

    break_sql = """
    SELECT trade_date, thscode, name, last_price, open_times, price_change_ratio
    FROM v_limit_break_pool
    WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_break_pool)
    ORDER BY open_times DESC
    LIMIT ?
    """

    down_sql = """
    SELECT trade_date, thscode, name, last_price, price_change_ratio, first_limit_time, last_limit_time
    FROM v_limit_down_pool
    WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_down_pool)
    ORDER BY price_change_ratio ASC
    LIMIT ?
    """

    try:
        break_rows = await src_special.execute(break_sql, [limit])
        down_rows = await src_special.execute(down_sql, [limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("炸板与跌停池查询失败：%s", exc)
        return {"ok": False, "break_pool": [], "down_pool": [], "unavailable": ["special"], "reason": f"查询失败：{exc}"}

    break_items = []
    trade_date = None
    for r in break_rows:
        if not trade_date and r.get("trade_date"):
            trade_date = str(r.get("trade_date"))
        break_items.append({
            "thscode": r.get("thscode"),
            "name": r.get("name"),
            "last_price": _f(r.get("last_price")),
            "open_times": int(r.get("open_times") or 0),
            "price_change_ratio": _f(r.get("price_change_ratio")),
        })

    down_items = []
    for r in down_rows:
        if not trade_date and r.get("trade_date"):
            trade_date = str(r.get("trade_date"))
        down_items.append({
            "thscode": r.get("thscode"),
            "name": r.get("name"),
            "last_price": _f(r.get("last_price")),
            "price_change_ratio": _f(r.get("price_change_ratio")),
            "first_limit_time": r.get("first_limit_time"),
            "last_limit_time": r.get("last_limit_time"),
        })

    return {
        "ok": True,
        "trade_date": trade_date,
        "break_pool": break_items,
        "down_pool": down_items,
        "break_count": len(break_items),
        "down_count": len(down_items),
        "unavailable": [],
    }


async def get_dividend_calendar(
    src_market: Any,
    thscode: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_dividend: float = 0.0,
    min_bonus: float = 0.0,
    sort_by: str = "date",
    limit: int = 50,
) -> Dict[str, Any]:
    """获取股票除权除息与分红送转事件日历。"""
    if src_market is None:
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["market"],
            "reason": "market 数据源未就绪",
        }

    limit = max(1, min(limit, 200))

    order_clause = "e.ex_date DESC"
    if sort_by == "yield":
        order_clause = "div_yield_pct DESC NULLS LAST, e.ex_date DESC"
    elif sort_by == "bonus":
        order_clause = "e.per_share_bonus DESC, e.ex_date DESC"
    elif sort_by == "dividend":
        order_clause = "e.dividend_per_share DESC, e.ex_date DESC"

    if thscode:
        sql = f"""
        WITH latest_p AS (
            SELECT thscode, close
            FROM v_daily_qfq
            WHERE date = (SELECT max(date) FROM v_daily_qfq)
        )
        SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio,
               p.close,
               ROUND(e.dividend_per_share / NULLIF(p.close, 0) * 100, 2) AS div_yield_pct
        FROM raw_adjustment_events e
        LEFT JOIN v_symbol s ON e.thscode = s.thscode
        LEFT JOIN latest_p p ON e.thscode = p.thscode
        WHERE e.thscode = ?
        ORDER BY {order_clause}
        LIMIT ?
        """
        params = [thscode, limit]
    else:
        if not start_date:
            start_date = "2026-01-01"
        if not end_date:
            end_date = "2099-12-31"

        sql = f"""
        WITH latest_p AS (
            SELECT thscode, close
            FROM v_daily_qfq
            WHERE date = (SELECT max(date) FROM v_daily_qfq)
        )
        SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio,
               p.close,
               ROUND(e.dividend_per_share / NULLIF(p.close, 0) * 100, 2) AS div_yield_pct
        FROM raw_adjustment_events e
        LEFT JOIN v_symbol s ON e.thscode = s.thscode
        LEFT JOIN latest_p p ON e.thscode = p.thscode
        WHERE e.ex_date >= ? AND e.ex_date <= ?
          AND e.dividend_per_share >= ? AND e.per_share_bonus >= ?
        ORDER BY {order_clause}
        LIMIT ?
        """
        params = [start_date, end_date, min_dividend, min_bonus, limit]

    try:
        rows = await src_market.execute(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("分红日历查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["market"],
            "reason": f"查询失败：{exc}",
        }

    items = []
    for r in rows:
        items.append({
            "thscode": r.get("thscode"),
            "name": r.get("name") or r.get("thscode"),
            "ex_date": str(r.get("ex_date")) if r.get("ex_date") else None,
            "dividend_per_share": _f(r.get("dividend_per_share")),
            "per_share_bonus": _f(r.get("per_share_bonus")),
            "allotment_ratio": _f(r.get("allotment_ratio")),
            "close": _f(r.get("close")),
            "dividend_yield": _f(r.get("div_yield_pct")),
        })

    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


async def get_hot_stocks(
    src_special: Any,
    mode: str = "skyrocket",
    limit: int = 50,
) -> Dict[str, Any]:
    """获取热股榜（人气总榜 vs 飙升黑马榜）。"""
    if src_special is None:
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["special"],
            "reason": "special 数据源未就绪",
        }

    limit = max(1, min(limit, 100))
    table = "v_skyrocket" if mode == "skyrocket" else "v_hot_stock"
    order_clause = "rank_change DESC, rank ASC" if mode == "skyrocket" else "rank ASC"

    sql = f"""
    SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend
    FROM {table}
    WHERE capture_date = (SELECT MAX(capture_date) FROM {table})
    ORDER BY {order_clause}
    LIMIT ?
    """

    try:
        rows = await src_special.execute(sql, [limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("热股榜查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["special"],
            "reason": f"查询失败：{exc}",
        }

    trade_date = None
    items = []
    for r in rows:
        if not trade_date and r.get("capture_date"):
            trade_date = str(r.get("capture_date"))
        items.append({
            "thscode": r.get("thscode"),
            "ticker": r.get("ticker"),
            "name": r.get("name") or r.get("thscode"),
            "rank": int(r.get("rank") or 0),
            "heat": _f(r.get("heat")),
            "rank_change": int(r.get("rank_change") or 0),
            "rank_trend": r.get("rank_trend"),
        })

    return {
        "ok": True,
        "mode": mode,
        "trade_date": trade_date,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


async def get_hot_stock_trend(
    src_special: Any,
    thscode: str,
    limit: int = 30,
) -> Dict[str, Any]:
    """获取指定个股的历史人气排名趋势。"""
    if src_special is None:
        return {
            "ok": False,
            "items": [],
            "thscode": thscode,
            "unavailable": ["special"],
            "reason": "special 数据源未就绪",
        }

    limit = max(1, min(limit, 100))
    sql = """
    SELECT thscode, trend_date, date_ms, rank
    FROM v_hot_stock_rank_trend
    WHERE thscode = ?
    ORDER BY trend_date DESC
    LIMIT ?
    """

    try:
        rows = await src_special.execute(sql, [thscode, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("个股人气排名趋势查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "thscode": thscode,
            "unavailable": ["special"],
            "reason": f"查询失败：{exc}",
        }

    items = []
    for r in rows:
        items.append({
            "date": str(r.get("trend_date")) if r.get("trend_date") else None,
            "rank": int(r.get("rank") or 0),
        })

    # 正序排列以方便画折线图（最早到最新）
    items.reverse()

    return {
        "ok": True,
        "thscode": thscode,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


async def get_futures_warehouse_receipts(
    src_futures: Any,
    thscode: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """获取大宗商品期货交易所注册仓单及变动量。"""
    if src_futures is None:
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["futures"],
            "reason": "futures 数据源未就绪",
        }

    limit = max(1, min(limit, 100))
    if thscode:
        sql = """
        SELECT trade_date, thscode, amount, amount_change, equivalent_lots
        FROM raw_futures_warehouse_receipts
        WHERE thscode = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """
        params = [thscode, limit]
    else:
        sql = """
        SELECT trade_date, thscode, amount, amount_change, equivalent_lots
        FROM raw_futures_warehouse_receipts
        WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_warehouse_receipts)
        ORDER BY ABS(amount_change) DESC
        LIMIT ?
        """
        params = [limit]

    try:
        rows = await src_futures.execute(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("期货仓单查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["futures"],
            "reason": f"查询失败：{exc}",
        }

    trade_date = None
    items = []
    for r in rows:
        if not trade_date and r.get("trade_date"):
            trade_date = str(r.get("trade_date"))
        items.append({
            "thscode": r.get("thscode"),
            "trade_date": str(r.get("trade_date")) if r.get("trade_date") else None,
            "amount": _f(r.get("amount")),
            "amount_change": _f(r.get("amount_change")),
            "equivalent_lots": _f(r.get("equivalent_lots")),
        })

    return {
        "ok": True,
        "trade_date": trade_date,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


async def get_futures_basis_rate(
    src_futures: Any,
    thscode: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    """获取大宗商品期货与现货基差及升贴水率。"""
    if src_futures is None:
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["futures"],
            "reason": "futures 数据源未就绪",
        }

    limit = max(1, min(limit, 100))
    if thscode:
        sql = """
        SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
        FROM raw_futures_basis
        WHERE thscode = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """
        params = [thscode, limit]
    else:
        sql = """
        SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
        FROM raw_futures_basis
        WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_basis) AND close_basis IS NOT NULL
        ORDER BY ABS(close_basis_rate) DESC
        LIMIT ?
        """
        params = [limit]

    try:
        rows = await src_futures.execute(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("期货基差查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["futures"],
            "reason": f"查询失败：{exc}",
        }

    trade_date = None
    items = []
    for r in rows:
        if not trade_date and r.get("trade_date"):
            trade_date = str(r.get("trade_date"))
        items.append({
            "thscode": r.get("thscode"),
            "trade_date": str(r.get("trade_date")) if r.get("trade_date") else None,
            "spot_price": _f(r.get("spot_price")),
            "close_price": _f(r.get("close_price")),
            "close_basis": _f(r.get("close_basis")),
            "close_basis_rate": _f(r.get("close_basis_rate")),
        })

    return {
        "ok": True,
        "trade_date": trade_date,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


# ══════════════════════════════════════════════════════════════════════════
# ETF / 基金：持有人结构穿透与机构持仓排行
# ══════════════════════════════════════════════════════════════════════════

async def get_etf_holders_detail(
    src_fund: Any,
    fund_thscode: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """获取指定基金/ETF 的持有人结构与前十大持有人。"""
    if src_fund is None:
        return {
            "ok": False,
            "fund_thscode": fund_thscode,
            "structure": None,
            "top_holders": [],
            "unavailable": ["fund"],
            "reason": "fund 数据源未就绪",
        }

    code = str(fund_thscode).strip()
    if not code:
        return {
            "ok": False,
            "fund_thscode": "",
            "structure": None,
            "top_holders": [],
            "reason": "fund_thscode 不能为空",
        }

    limit = max(1, min(limit, 20))

    struct_sql = """
    SELECT h.fund_thscode,
           COALESCE(p.fund_name, u.name, h.fund_thscode) AS fund_name,
           h.merge_scope, h.report_date_ms, h.ins_position,
           h.holder_amount, h.avg_holder_share, h.psnl_rate, h.mgmt_staff_hold_rate
    FROM v_fund_holders h
    LEFT JOIN v_fund_profile p ON h.fund_thscode = p.thscode
    LEFT JOIN v_etf_universe u ON h.fund_thscode = u.thscode
    WHERE h.fund_thscode = ?
    ORDER BY h.report_date_ms DESC
    LIMIT 1
    """

    top_sql = """
    SELECT fund_thscode, holder_name, holder_type, holder_code, rank,
           hold_share, hold_rate_pct, report_date_ms, publish_date_ms
    FROM v_fund_top_holders
    WHERE fund_thscode = ?
      AND report_date_ms = (
          SELECT MAX(report_date_ms)
          FROM v_fund_top_holders
          WHERE fund_thscode = ?
      )
    ORDER BY rank ASC
    LIMIT ?
    """

    try:
        struct_rows = await src_fund.execute(struct_sql, [code])
        top_rows = await src_fund.execute(top_sql, [code, code, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("基金持有人结构查询失败 %s：%s", code, exc)
        return {
            "ok": False,
            "fund_thscode": code,
            "structure": None,
            "top_holders": [],
            "unavailable": ["fund"],
            "reason": f"查询失败：{exc}",
        }

    structure = None
    if struct_rows:
        sr = struct_rows[0]
        ms = sr.get("report_date_ms")
        rep_date = None
        if ms:
            try:
                rep_date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                rep_date = None
        structure = {
            "fund_thscode": sr.get("fund_thscode"),
            "fund_name": sr.get("fund_name"),
            "report_date": rep_date,
            "ins_position": _f(sr.get("ins_position")),
            "psnl_rate": _f(sr.get("psnl_rate")),
            "holder_amount": int(sr.get("holder_amount") or 0) if sr.get("holder_amount") is not None else None,
            "avg_holder_share": _f(sr.get("avg_holder_share")),
            "mgmt_staff_hold_rate": _f(sr.get("mgmt_staff_hold_rate")),
        }

    top_holders = []
    for r in top_rows:
        ms = r.get("report_date_ms")
        rep_date = None
        if ms:
            try:
                rep_date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                rep_date = None
        top_holders.append({
            "fund_thscode": r.get("fund_thscode"),
            "holder_name": r.get("holder_name"),
            "holder_type": r.get("holder_type") or "其他",
            "holder_code": r.get("holder_code"),
            "rank": int(r.get("rank") or 0),
            "hold_share": _f(r.get("hold_share")),
            "hold_rate_pct": _f(r.get("hold_rate_pct")),
            "report_date": rep_date,
        })

    return {
        "ok": True,
        "fund_thscode": code,
        "structure": structure,
        "top_holders": top_holders,
        "count": len(top_holders),
        "unavailable": [],
    }


async def get_etf_institutional_ranking(
    src_fund: Any,
    order_by: str = "institution",
    limit: int = 30,
) -> Dict[str, Any]:
    """获取全市场机构控盘度/散户聚集度最高的基金与 ETF 排名榜单。"""
    if src_fund is None:
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["fund"],
            "reason": "fund 数据源未就绪",
        }

    limit = max(1, min(limit, 100))
    order_col = "h.ins_position DESC" if order_by == "institution" else "h.psnl_rate DESC"

    sql = f"""
    SELECT h.fund_thscode,
           COALESCE(p.fund_name, u.name, h.fund_thscode) AS fund_name,
           h.merge_scope, h.report_date_ms, h.ins_position,
           h.holder_amount, h.avg_holder_share, h.psnl_rate
    FROM v_fund_holders h
    LEFT JOIN v_fund_profile p ON h.fund_thscode = p.thscode
    LEFT JOIN v_etf_universe u ON h.fund_thscode = u.thscode
    WHERE h.ins_position IS NOT NULL
    ORDER BY {order_col}
    LIMIT ?
    """

    try:
        rows = await src_fund.execute(sql, [limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("机构投资者占比排行查询失败：%s", exc)
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "unavailable": ["fund"],
            "reason": f"查询失败：{exc}",
        }

    items = []
    for r in rows:
        ms = r.get("report_date_ms")
        rep_date = None
        if ms:
            try:
                rep_date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                rep_date = None
        items.append({
            "fund_thscode": r.get("fund_thscode"),
            "fund_name": r.get("fund_name"),
            "report_date": rep_date,
            "ins_position": _f(r.get("ins_position")),
            "psnl_rate": _f(r.get("psnl_rate")),
            "holder_amount": int(r.get("holder_amount") or 0) if r.get("holder_amount") is not None else None,
            "avg_holder_share": _f(r.get("avg_holder_share")),
        })

    return {
        "ok": True,
        "order_by": order_by,
        "items": items,
        "count": len(items),
        "unavailable": [],
    }


# ══════════════════════════════════════════════════════════════════════════
# 量化因子：TTM Squeeze 波动挤压与超级趋势 (Supertrend)
# ══════════════════════════════════════════════════════════════════════════

async def get_squeeze_status(
    src_indicators: Any,
    thscode: str,
    limit: int = 30,
) -> Dict[str, Any]:
    """获取股票 TTM Squeeze 波动挤压指标与 Supertrend 顺势追踪状态。"""
    if src_indicators is None:
        return {
            "ok": False,
            "thscode": thscode,
            "is_squeezing": False,
            "squeeze_status": "unavailable",
            "supertrend_direction": None,
            "items": [],
            "unavailable": ["indicators"],
            "reason": "indicators 数据源未就绪",
        }

    code = str(thscode).strip()
    if not code:
        return {
            "ok": False,
            "thscode": "",
            "is_squeezing": False,
            "squeeze_status": "error",
            "reason": "thscode 不能为空",
        }

    limit = max(1, min(limit, 100))

    sql = """
    SELECT date,
           volatility_bbands_20_2_0_upper,
           volatility_bbands_20_2_0_lower,
           volatility_bbands_20_2_0_middle,
           volatility_kc_20_2_upper,
           volatility_kc_20_2_lower,
           volatility_kc_20_2_middle,
           trend_supertrend_10_3_0_trend,
           trend_supertrend_10_3_0_direction,
           trend_supertrend_10_3_0_long,
           trend_supertrend_10_3_0_short,
           momentum_macd_12_26_9_hist,
           statistics_beta_20
    FROM v_indicators_daily
    WHERE thscode = ?
    ORDER BY date DESC
    LIMIT ?
    """

    try:
        rows = await src_indicators.execute(sql, [code, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTM Squeeze 波动挤压查询失败 %s：%s", code, exc)
        return {
            "ok": False,
            "thscode": code,
            "is_squeezing": False,
            "squeeze_status": "error",
            "supertrend_direction": None,
            "items": [],
            "unavailable": ["indicators"],
            "reason": f"查询失败：{exc}",
        }

    if not rows:
        return {
            "ok": True,
            "thscode": code,
            "is_squeezing": False,
            "squeeze_status": "no_data",
            "supertrend_direction": None,
            "items": [],
            "count": 0,
            "unavailable": [],
        }

    items = []
    for r in rows:
        bb_u = _f(r.get("volatility_bbands_20_2_0_upper"))
        bb_l = _f(r.get("volatility_bbands_20_2_0_lower"))
        kc_u = _f(r.get("volatility_kc_20_2_upper"))
        kc_l = _f(r.get("volatility_kc_20_2_lower"))

        # 布林带完全在肯特纳通道内部时为挤压中
        squeezing = None
        if bb_u is not None and bb_l is not None and kc_u is not None and kc_l is not None:
            squeezing = (bb_u <= kc_u) and (bb_l >= kc_l)

        d = r.get("date")
        date_str = str(d)[:10] if d else None

        items.append({
            "date": date_str,
            "bb_upper": bb_u,
            "bb_lower": bb_l,
            "bb_middle": _f(r.get("volatility_bbands_20_2_0_middle")),
            "kc_upper": kc_u,
            "kc_lower": kc_l,
            "kc_middle": _f(r.get("volatility_kc_20_2_middle")),
            "is_squeezing": squeezing,
            "momentum_hist": _f(r.get("momentum_macd_12_26_9_hist")),
            "supertrend_trend": _f(r.get("trend_supertrend_10_3_0_trend")),
            "supertrend_direction": int(r.get("trend_supertrend_10_3_0_direction") or 0) if r.get("trend_supertrend_10_3_0_direction") is not None else None,
            "supertrend_long": _f(r.get("trend_supertrend_10_3_0_long")),
            "supertrend_short": _f(r.get("trend_supertrend_10_3_0_short")),
            "beta": _f(r.get("statistics_beta_20")),
        })

    latest = items[0]
    is_sq = bool(latest.get("is_squeezing"))
    squeeze_status = "on" if is_sq else "off"

    # 判断是否为刚刚爆发（前一天是挤压中，今天解除了）
    if len(items) > 1 and not is_sq and items[1].get("is_squeezing") is True:
        squeeze_status = "fired"

    return {
        "ok": True,
        "thscode": code,
        "is_squeezing": is_sq,
        "squeeze_status": squeeze_status,
        "latest_date": latest.get("date"),
        "supertrend_direction": latest.get("supertrend_direction"),
        "supertrend_level": latest.get("supertrend_long") if latest.get("supertrend_direction") == 1 else latest.get("supertrend_short"),
        "momentum_hist": latest.get("momentum_hist"),
        "beta": latest.get("beta"),
        "items": items,
        "count": len(items),
        "unavailable": [],
    }
