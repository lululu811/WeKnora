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

# 量比均量窗口。「放量突破」要用"当日量 / 前 N 日均量"判定，N 太少会让
# 均量被单日极值带偏。这里与 volume.py 的 `_calc_vol_avg` 默认窗口保持一致。
VOL_AVG_WINDOW = 10

# 取最近 20 天。
#
# 原来是 10 天，为了与 /zettaranc/scan 的下限一致。现在要加价量维度，
# 10 天不够：算「放量突破」需要「当日 + 前 10 日均量」共 11 根，10 天窗口下
# 均量实际只覆盖 9 根，判定口径与单票 analyze 路径不一致 —— 而"选股池和
# 单票扫描必须同口径"正是这个模块存在的理由。
LOOKBACK_DAYS = 20

# 单次查询的行数预算。
#
# python-service 给每条查询封顶 MAX_QUERY_ROWS=100_000，超了会**静默截断**
# （只保留前 100k 行）。全市场 5,571 只 × 20 天：
#   indicators ≈ 111,206 行、market 价量 ≈ 83,269 行
# 单条查询无论取哪张表都已经贴着上限，**两张表相加更是必然超标**，
# 所以价量维度只能靠"按代码分片"接进来，不能靠把两条查询拼成一条。
#
# 留 20% 余量而不是正好卡 100_000：实际行数会随停牌、新股、数据同步进度
# 波动，正好卡满的阈值迟早会在某天悄悄截断，而截断的表现是"少了些票"，
# 不报错，很难被发现。
QUERY_ROW_BUDGET = 80_000


def shard_codes(codes: List[str]) -> List[List[str]]:
    """把代码列表切成若干片，保证**单条**查询的行数不超过预算。

    之所以要分片：全市场一次查 = 11 万行起，超过单查询 10 万行上限，
    多出来的会被静默丢掉。分片后每片独立成一次查询，没有任何一行会被截断。

    切片大小按"每只票最多 LOOKBACK_DAYS 行"估算，这是最坏情况（上界）；
    实际因停牌、新股通常更少，所以这是安全侧。
    """
    if not codes:
        return []
    per_shard = max(1, QUERY_ROW_BUDGET // max(1, LOOKBACK_DAYS))
    return [codes[i:i + per_shard] for i in range(0, len(codes), per_shard)]


def build_price_snapshot_sql() -> str:
    """按给定代码列表取回最近 N 天的 OHLCV（market 库）。

    `v_indicators_daily` 只有指标、**没有任何价格列**（没有 close、没有
    volume），所以「放量突破」「Donchian上轨突破」这类依赖价量的形态信号
    在选股池里一直是不可用的 —— 它们以前只能走 /zettaranc/analyze 单只扫。
    现在把 `market.v_daily_qfq` 的价量按 (thscode, date) 并进指标行，
    两个形态信号才进得了集合式选股。

    只取 close/high/low/volume 四列：Donchian 上轨要用 high 滚动窗口，
    放量突破要 close + volume，turnover/open 在这两个判定里用不上。
    """
    select_list = ", ".join(PRICE_SNAPSHOT_COLUMNS)
    return f"""
        SELECT thscode,
               CAST(date AS VARCHAR) AS date,
               {select_list}
        FROM v_daily_qfq
        WHERE thscode IN (SELECT unnest(string_split(?, ',')))
        QUALIFY row_number() OVER (PARTITION BY thscode ORDER BY date DESC) <= ?
        ORDER BY thscode, date DESC
    """


# 价量快照的列名。信号判定读的就是这几个 key。
PRICE_SNAPSHOT_COLUMNS = ("close", "high", "low", "volume")


def merge_price_rows(
    indicator_rows: List[Dict[str, Any]],
    price_rows: List[Dict[str, Any]],
) -> int:
    """把价量按 (thscode, date) 并进行指标行，返回实际并上的行数。

    键必须是 (thscode, **date**) 而不是只按 thscode：同一只票有多天的数据，
    只按代码关联会让所有日期都挂上"最新一天"的价量，于是「放量突破」拿
    最新价配昨天的量，量比彻底失真。

    只写 indicator_rows 里**已经存在**的那个 date，不会给指标行补新日期 ——
    新增日期会让 `group_by_symbol` 的"最新行"判定漂移，也可能让只有价量
    没有指标的日期混进来。
    """
    price_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for p in price_rows or []:
        code = p.get("thscode")
        day = p.get("date")
        if not code or not day:
            continue
        price_by_key[(code, str(day))] = p

    merged = 0
    for row in indicator_rows or []:
        code = row.get("thscode")
        day = row.get("date")
        if not code or not day:
            continue
        p = price_by_key.get((code, str(day)))
        if p is None:
            continue
        for col in PRICE_SNAPSHOT_COLUMNS:
            row[col] = p.get(col)
        merged += 1
    return merged


# 没有"信号名 -> 指标别名"的映射表。
#
# 曾经有一份 SCREEN_SIGNAL_FIELDS，注释写着"列出映射是为了让 STRATEGY_RULES
# 里写错名字时能在启动/测试期暴露出来"。实际上**没有任何代码读它** ——
# 一份只声明不使用的配置。而且它早已和真实信号集脱节：缺 KDJ超卖金叉、
# Stochastic超卖金叉、Aroon多头排列、放量突破与全部蜡烛形态。
# 一旦真按它做闸门，会把大半合法信号判成"写错了"。
#
# 真正在起作用的闸门是测试
# tests/unit/test_screener.py::test_every_rule_signal_is_reachable_from_real_indicator_values：
# 它用真实夹具跑一遍 detect_signals，拿到实际能发出的信号名全集，
# 断言每条策略引用的信号都在其中。本轮加"放量突破""Donchian上轨突破"
# 两个策略时，都是这条测试先报的名字不认识。
#
# 注释承诺了不存在的机制，比没有这个机制更糟 —— 维护者会以为
# "已经查过映射表了"，于是跳过真正的检查。

# 这些信号依赖**价格/成交量**（close、volume），而 `v_indicators_daily` 里
# 没有任何价格列 —— 它只有指标。`market.v_daily_qfq` 才有点位，两个库是独立
# 的 DuckDB 文件且都是 read_only（不能 ATTACH），所以需要单独查一张表再按
# (thscode, date) 合并，见 build_price_snapshot_sql / merge_price_rows。
#
# 登记在这里而不是留个空集，是为了让 STRATEGY_RULES 引用它们时：测试直接红，
# 接口在 `unsupported_signals` 里如实回报，而不是静默地永远匹配不到 ——
# `anomaly` 策略当初就是这么"死"掉的。
#
# 原本登记了「放量突破」「Donchian上轨突破」两项；价量维度接进来后已清空。
# 保留这个集合作为闸门：新加形态信号时，如果它依赖的字段还没并进行，
# 就该先登记进来让接口如实回报，而不是写进策略后静默匹配不到。
SCREEN_UNSUPPORTED: Dict[str, str] = {}

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
    """规则里引用了全市场扫描**取不到数据**的形态信号。

    目前恒返回空：价量维度接进来后 SCREEN_UNSUPPORTED 已清空，所有信号
    都能算。保留这个闸门是为了将来：新增一个依赖尚未并入的字段的信号时，
    先登记进来，接口就会在 `unsupported_signals` 里如实回报，而不是让那条
    策略静默地永远选不出票 —— `anomaly` 当初就是这么"死"掉的。

    注意它管的是**数据取不到**，不是**信号名写错**。写错由单元测试
    test_every_rule_signal_is_reachable_from_real_indicator_values 拦。
    """
    return [s for s in rule["match_signals"] if s in SCREEN_UNSUPPORTED]
