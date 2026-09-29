"""
全市场选股 — 集合式扫描

对应 Go: internal/agent/tools/hithink_finance/pattern/scan.go

为什么是集合式的
----------------
逐只调 `scan_patterns` 是 N+1：5571 只 A 股 = 5571 次往返，实测 36 秒。
这里改成**一条 SQL 一次性取回全市场每个标的最近两天的指标**，然后在
Python 侧复用 `detect_signals` 判定。指标列名一致，所以判定逻辑和
`/zettaranc/scan` 走的是同一份代码，不会出现"选股池和单票扫描口径不同"。

两条硬约束
----------
* 标的池以 `dim_symbol.asset_type = 'a-share'` 为准，并按 thscode 排序，
  全量覆盖（早期版本硬编码 `LIMIT 100` 且无 ORDER BY，只扫到 600xxx.SH）。
* 指标为 NULL 的标的直接剔除。`COALESCE(col, 0)` 会把"没算出来"变成
  RSI6=0 → 超卖，把空数据标的选进买入池。
"""

from typing import Any, Dict, List, Optional, Tuple

from .data_loader import INDICATOR_COLUMNS, INDICATORS_ONLY_FIELDS
from .signals import detect_signals, summarize_signals

MAX_UNIVERSE = 20_000

# 取最近 10 天，与 /zettaranc/scan 的 fetch_indicators_only 下限一致。
# 少于 5 天时"布林带收口"和"ATR扩张"用的是残缺窗口，判定会和单票扫描
# 不一致 —— 选股池和单票扫描必须是同一套口径。
LOOKBACK_DAYS = 10

# 策略命中所需的信号名 -> 指标别名。
# 列出映射是为了让 STRATEGY_RULES 里写错名字时能在启动/测试期暴露出来，
# 而不是像以前那样静默地永远匹配不到。
SCREEN_SIGNAL_FIELDS: Dict[str, str] = {
    "MACD金叉": "dif",
    "RSI6超卖": "rsi6",
    "CCI超卖": "cci",
    "Williams%R超卖": "willr",
    "MFI超卖": "mfi",
    "Z-Score超卖": "zscore",
    "Donchian上轨突破": "dc_upper",
    "ATR扩张": "atr",
    "布林带收口": "bb_width",
    "CMF资金流入": "cmf",
    "CMF资金流出": "cmf",
    "Vortex死叉": "vi_plus",
    "ADX空头趋势": "adx",
}

# 这些信号依赖**价格/成交量**（close、vol），而 `v_indicators_daily` 里没有
# 任何价格列 —— 它只有指标。market 的 `v_daily_qfq` 才有点位，两个库是独立
# 的 DuckDB 文件且都是 read_only（不能 ATTACH），所以全市场快照拿不到它们。
#
# 登记在这里而不是留个空集，是为了让 STRATEGY_RULES 引用它们时：测试直接红，
# 接口在 `unsupported_signals` 里如实回报，而不是静默地永远匹配不到 ——
# `anomaly` 策略当初就是这么"死"掉的。
SCREEN_UNSUPPORTED = {
    "放量突破",        # analyze_volume_price，需要 close + vol
    "Donchian上轨突破",  # 需要 close
}

CORE_FIELDS = ("rsi6", "macd_hist", "mfi", "adx", "bb_upper", "atr", "obv")


# 涨停选股的候选集：真实涨停记录。
#
# 窗口取最近 6 个交易日而不是"最新一天"：只取一天的话，池子被截断到 50 行，
# 指标筛完常常剩不到几只可选；6 天累计能让"曾经涨停过"的票都进候选，
# 再用指标做二次排序。选出来的每一只都会带上 limit_up 明细（含出处表名）。
LIMIT_UP_LOOKBACK_DAYS = 6


def collapse_limit_up_pool(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把涨停池按代码归并，每只票保留**连板数最大**的那条记录。

    一只连板股在窗口内每天都会留一行，连板数逐日递增（601811.SH 在近 6 个
    交易日里留了 5 行，连板数 1→2→3→4→5）。用 `dict.setdefault` 或直接
    `by_code[code] = row` 归并时，最后写进去的那行会覆盖前面的：SQL 按
    trade_date DESC 排序，于是最新的 5 连板会被最早那天的 1 连板顶掉 ——
    实测 601811.SH 就这样把 5 板显示成了 1 板，排名字段也跟着错。
    """
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        code = row.get("thscode")
        if not code:
            continue
        prev = best.get(code)
        if prev is None or (row.get("continue_day_cnt") or 0) > (
            prev.get("continue_day_cnt") or 0
        ):
            best[code] = row
    return best


def build_limit_up_pool_sql() -> str:
    """从 special 库取最近 N 个交易日出现过的涨停票。

    连板数、封板时间、封单金额都是这张表的原生列，不是算出来的。
    `limit_up` 策略过去只匹配 CMF/ADX/Aroon 三个代理指标，从没读过这张表，
    拿"资金流入且趋势向上"冒充"涨停"；现在候选集直接来自真实记录。

    窗口天数是**字面量**而不是 `?`：DuckDB 不接受 `INTERVAL ? DAY`，会在
    `?` 处抛 Parser Error。本地直连 duckdb 时如果用绑定参数同样过不了这一关，
    而单元测试只做语法断言、发现不了 —— 只有真正打一次 HTTP 才暴露。
    窗口是模块常量而非用户输入，没有注入面。
    """
    return f"""
        SELECT thscode, name, trade_date, limit_up_time,
               continue_day_cnt, seal_money, last_price
        FROM v_limit_up_pool
        WHERE trade_date >= (
            SELECT MAX(trade_date) - INTERVAL {LIMIT_UP_LOOKBACK_DAYS} DAY
            FROM v_limit_up_pool
        )
        ORDER BY trade_date DESC, continue_day_cnt DESC
    """


def build_universe_sql() -> str:
    """全市场 a 股清单（来自 market 库）。"""
    return """
        SELECT thscode, name
        FROM dim_symbol
        WHERE asset_type = 'a-share' AND thscode IS NOT NULL
        ORDER BY thscode
        LIMIT ?
    """


def build_indicator_snapshot_sql() -> str:
    """按给定代码列表取回每只标的最近 N 天的指标（indicators 库）。

    `dim_symbol` 在 market 库、指标在 indicators 库，两者是独立的 DuckDB 文件，
    且都是 read_only 连接（不能 ATTACH）。所以代码清单走一个绑定参数传进来，
    用 `IN (SELECT unnest(string_split(?, ',')))` 做哈希查找 —— 5571 个代码
    只占一个参数。
    """
    select_list = ",\n               ".join(
        f"{INDICATOR_COLUMNS[f]} AS {f}"
        for f in INDICATORS_ONLY_FIELDS
        if f != "date"
    )
    return f"""
        SELECT thscode,
               CAST(date AS VARCHAR) AS date,
               {select_list}
        FROM v_indicators_daily
        WHERE thscode IN (SELECT unnest(string_split(?, ',')))
        QUALIFY row_number() OVER (PARTITION BY thscode ORDER BY date DESC) <= ?
        ORDER BY thscode, date DESC
    """


def group_by_symbol(
    rows: List[Dict],
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """把扁平结果集按 thscode 分组，并判断指标是否完整。

    返回 {thscode: {"name":..., "rows": [...], "data_complete": bool,
    "missing": [...]}}。rows 仍然是最新的在前。
    """
    names = names or {}
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        code = row.get("thscode")
        if not code:
            continue
        entry = grouped.setdefault(code, {
            "name": names.get(code, row.get("name", "")),
            "rows": [],
            "data_complete": True,
            "missing": [],
        })
        if row.get("date"):
            entry["rows"].append({k: v for k, v in row.items()
                                  if k not in ("thscode", "name")})

    for entry in grouped.values():
        if not entry["rows"]:
            entry["data_complete"] = False
            continue
        latest = entry["rows"][0]
        entry["missing"] = [f for f in CORE_FIELDS if latest.get(f) is None]
        entry["data_complete"] = not entry["missing"]
    return grouped


def evaluate_group(
    entry: Dict[str, Any],
    match_signals: List[str],
    min_count: int,
    allow_neutral: bool = False,
    direction: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """对一只标的跑信号判定并按规则过滤。指标不全直接淘汰。

    `direction` 取代旧版 `allow_neutral` 的开关语义。旧实现写的是
    `s["signal"] == "bullish" or allow_neutral`：一旦某条规则开了
    allow_neutral（`anomaly` 就开了），整个条件对**所有**方向短路成真，
    bearish 信号照样入选；而 score 累加的是被夹到 [0,1] 的无符号
    strength，于是命中 3 个看跌信号的票排在命中 1 个中性信号的票之前
    —— 策略稳定地选出一批看跌票，却顶着"异常检测"的名字。

    现在把方向变成显式契约：
      direction="bullish"  只要 bullish（超卖金叉、MACD金叉…）
      direction="bearish"  只要 bearish
      direction=None       保持旧语义：bullish，外加 allow_neutral 时的 neutral
    """
    if not entry.get("data_complete"):
        return None
    try:
        signals = detect_signals(entry["rows"])
    except Exception:
        return None

    wanted = set(match_signals)
    if direction == "bullish":
        allowed = {"bullish"}
    elif direction == "bearish":
        allowed = {"bearish"}
    elif allow_neutral:
        allowed = {"bullish", "neutral"}
    else:
        allowed = {"bullish"}

    matched = [
        s for s in signals
        if s["name"] in wanted
        and s["signal"] in allowed
    ]
    if len(matched) < min_count:
        return None
    return {
        "thscode": "",
        "name": entry.get("name", ""),
        "score": round(sum(s["strength"] for s in matched), 2),
        "matched_signals": [s["name"] for s in matched],
        "matched_directions": sorted({s["signal"] for s in matched}),
        "summary": summarize_signals(signals),
    }


def screen(
    rows: List[Dict],
    rule: Dict[str, Any],
    limit: int,
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """纯函数：结果集 + 规则 -> 排名后的候选。"""
    grouped = group_by_symbol(rows, names)
    candidates: List[Dict[str, Any]] = []
    incomplete = 0
    for code, entry in grouped.items():
        if not entry["data_complete"]:
            incomplete += 1
        hit = evaluate_group(
            entry, rule["match_signals"], rule["min_count"],
            allow_neutral=bool(rule.get("allow_neutral")),
            direction=rule.get("direction"),
        )
        if hit is not None:
            hit["thscode"] = code
            candidates.append(hit)

    candidates.sort(key=lambda c: (-c["score"], c["thscode"]))
    return {
        "matched": len(candidates),
        "incomplete": incomplete,
        "stocks": candidates[:limit],
    }


def unsupported_signals(rule: Dict[str, Any]) -> List[str]:
    """规则里引用了全市场扫描不支持的形态信号（会静默匹配不到）。"""
    return [s for s in rule["match_signals"] if s in SCREEN_UNSUPPORTED]
