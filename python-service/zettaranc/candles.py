"""
蜡烛形态 — 把指标库里预计算的 TA-Lib `candles_cdl_*` 列接成「按日期排列的形态序列」。

## 为什么需要这个模块（而不是直接读列）

指标库里那 61 列**不全是形态标志位**。实测（230 万根 K 线）：

  cdl_3linestrike   触发率 70.5%，值域有 687,148 种不同浮点数
  cdl_2crows        触发率 67.5%，值域有 1,553,635 种
  cdl_3outside      触发率 71.6%，值是 6.67 / 33.33 / 93.33 这种
  cdl_3blackcrows   触发率 45.2%

这些显然不是 CDL 的 0/±100 输出，而是别的计算覆盖进去了。**直接采信会在
70% 的 K 线上画出假形态** —— 比不画糟得多：用户看到的是满屏噪音，却以为是
真实识别结果。

所以这里做两件事：
  1. 只认**精选表**里的列（人工按值域 + 触发率筛过）；
  2. 运行时再校验一次值域（`validate_indicator_columns`），列被污染时**报错而
     不是静默产出垃圾**，因为数据管道出问题必须在日志里看得见。

## 值域

TA-Lib 的 CDL 系列返回整数编码：0 = 无，正数 = 看涨，负数 = 看跌；
绝对值 100 是标准强度，80 是较弱的确认，200 是强信号。
**方向一律取符号**，不把 80/100 的差异当成两回事 —— 那个差异在图上无法表达，
而且我们对它的确切含义没有把握。

注意符号是 TA-Lib **结合前序趋势**给出的判断，未必和形态名直觉一致：同一个
`cdl_invertedhammer` 在低位可能是 +100、在高位是 -100。所以上面的 `desc`
**只描述形状，不断言方向** —— 写了"看涨"而符号是负的，就是把两个来源打架的
信息同时摆给用户。
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 精选表：只列值域干净、且触发率在 0.01%~15% 之间的形态
#
# 括号里是实测触发率（230 万根 K 线，2025 年起）。加新形态前先跑一遍值域校验，
# 别只看名字好听 —— 上面那 6 个坏列里就有「三只乌鸦」这种一看就想加的名字。
# ---------------------------------------------------------------------------
CANDLE_PATTERNS: List[Dict[str, str]] = [
    # 单根
    {"key": "cdl_doji", "column": "candles_cdl_doji_0",             "name": "十字星",     "desc": "开盘≈收盘，多空平衡", "desc_en": "doji"},
    {"key": "cdl_longleggeddoji", "column": "candles_cdl_longleggeddoji_0",   "name": "长腿十字",   "desc": "长上下影的十字，变盘", "desc_en": "long-legged doji"},   # 4.1%
    {"key": "cdl_dragonflydoji", "column": "candles_cdl_dragonflydoji_0",    "name": "蜻蜓十字",   "desc": "长下影、无上影的十字", "desc_en": "dragonfly doji"},   # 9.9%
    {"key": "cdl_gravestonedoji", "column": "candles_cdl_gravestonedoji_0",   "name": "墓碑十字",   "desc": "长上影、无下影的十字", "desc_en": "gravestone doji"},  # 1.8%
    {"key": "cdl_hammer", "column": "candles_cdl_hammer_0",           "name": "锤子线",     "desc": "长下影小实体", "desc_en": "hammer"},             # 1.6%
    {"key": "cdl_hangingman", "column": "candles_cdl_hangingman_0",       "name": "上吊线",     "desc": "长下影小实体（出现在高位）", "desc_en": "hanging man"},       # 8.4%
    {"key": "cdl_invertedhammer", "column": "candles_cdl_invertedhammer_0",   "name": "倒锤子线",   "desc": "长上影小实体（出现在低位）", "desc_en": "inverted hammer"},  # 8.9%
    {"key": "cdl_shootingstar", "column": "candles_cdl_shootingstar_0",     "name": "流星线",     "desc": "长上影小实体", "desc_en": "shooting star"},     # 0.6%
    {"key": "cdl_spinningtop", "column": "candles_cdl_spinningtop_0",      "name": "纺锤线",     "desc": "小实体长影，多空胶着", "desc_en": "spinning top"},      # 6.2%
    {"key": "cdl_highwave", "column": "candles_cdl_highwave_0",         "name": "高浪线",     "desc": "影线极长，剧烈分歧", "desc_en": "high wave"},           # 3.7%
    {"key": "cdl_marubozu", "column": "candles_cdl_marubozu_0",         "name": "光头光脚",   "desc": "无影线的大阳/大阴", "desc_en": "marubozu"},             # 2.5%
    {"key": "cdl_closingmarubozu", "column": "candles_cdl_closingmarubozu_0",  "name": "收盘光头光脚", "desc": "收在极值，单边强势", "desc_en": "closing marubozu"},  # 3.7%
    {"key": "cdl_belthold", "column": "candles_cdl_belthold_0",         "name": "腰带线",     "desc": "开在极值的单边线", "desc_en": "belt hold"},            # 4.4%
    {"key": "cdl_longline", "column": "candles_cdl_longline_0",         "name": "长实体线",   "desc": "实体异常长，趋势延续", "desc_en": "long line"},         # 5.2%
    {"key": "cdl_shortline", "column": "candles_cdl_shortline_0",        "name": "短实体线",   "desc": "实体异常短，趋势停顿", "desc_en": "short line"},        # 4.9%
    # 两根
    {"key": "cdl_engulfing", "column": "candles_cdl_engulfing_0",        "name": "吞没形态",   "desc": "后一根实体包住前一根", "desc_en": "engulfing"},         # 3.1%
    {"key": "cdl_harami", "column": "candles_cdl_harami_0",           "name": "孕线",       "desc": "后一根实体被前一根包住", "desc_en": "harami"},         # 2.5%
    {"key": "cdl_piercing", "column": "candles_cdl_piercing_0",         "name": "曙光初现",   "desc": "阳线刺入前阴线实体的下半", "desc_en": "piercing line"}, # 3.5%
    {"key": "cdl_darkcloudcover", "column": "candles_cdl_darkcloudcover_0",   "name": "乌云盖顶",   "desc": "阴线切入前阳线实体的上半", "desc_en": "dark cloud cover"},  # 0.5%
    # 三根
    {"key": "cdl_morningstar", "column": "candles_cdl_morningstar_0",      "name": "早晨之星",   "desc": "三日反转，中间小实体", "desc_en": "morning star"},             # 9.4%
    {"key": "cdl_eveningstar", "column": "candles_cdl_eveningstar_0",      "name": "黄昏之星",   "desc": "三日反转，中间小实体", "desc_en": "evening star"},             # 0.3%
    {"key": "cdl_morningdojistar", "column": "candles_cdl_morningdojistar_0",  "name": "早晨十字星", "desc": "中间是十字的三日反转", "desc_en": "morning doji star"},  # 0.13%
    {"key": "cdl_eveningdojistar", "column": "candles_cdl_eveningdojistar_0",  "name": "黄昏十字星", "desc": "中间是十字的三日反转", "desc_en": "evening doji star"},  # 0.07%
    {"key": "cdl_3whitesoldiers", "column": "candles_cdl_3whitesoldiers_0",   "name": "红三兵",     "desc": "连续三根阳线", "desc_en": "three white soldiers"},  # 0.04%
]

PATTERN_BY_KEY: Dict[str, Dict[str, str]] = {p["key"]: p for p in CANDLE_PATTERNS}
CANDLE_KEYS: List[str] = [p["key"] for p in CANDLE_PATTERNS]

# TA-Lib 允许的强度值。出现别的值说明这一列不是 CDL 输出。
ALLOWED_MAGNITUDES = frozenset({80.0, 100.0, 200.0})


# DB 列名 -> key（建 SQL 用；行里的键名统一成 key，别把 `candles_cdl_x_0` 漏到 API 上）
COLUMN_MAP: Dict[str, str] = {p["key"]: p["column"] for p in CANDLE_PATTERNS}


def build_candle_sql() -> str:
    """
    蜡烛形态列的查询。字段名来自本模块的常量，不含任何用户输入；
    thscode / limit 走绑定参数。
    """
    select = ", ".join(f"{col} AS {key}" for key, col in COLUMN_MAP.items())
    return f"""
        SELECT CAST(date AS VARCHAR) AS date, {select}
        FROM v_indicators_daily
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT ?
    """


def validate_indicator_columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """
    校验精选列的取值是否只含 `0` 与允许的强度值。

    Returns:
        有问题的列名列表。**调用方必须处理这个返回值**（记日志并跳过该列），
        不能当成"没问题"直接忽略 —— 静默采信被污染的列正是这整个模块要防的事。
    """
    bad: List[str] = []
    for key in CANDLE_KEYS:
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            try:
                magnitude = abs(float(value))
            except (TypeError, ValueError):
                bad.append(f"{key}(非数值 {value!r})")
                break
            if magnitude != 0 and magnitude not in ALLOWED_MAGNITUDES:
                bad.append(f"{key}(非法取值 {value!r})")
                break
    return bad


def detect_candle_series(
    rows: Sequence[Dict[str, Any]],
    days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    把指标行转成按日期排列的蜡烛形态序列。

    Args:
        rows: 指标行，**必须含 date 与精选表的 cdl_* 列**。行序不限（这里自己排）。
        days: 只保留最近这么多个交易日（None = 全给）。

    Returns:
        `[{date, type, name, direction, strength, desc}]`，按日期**升序**。
        direction ∈ bullish / bearish / neutral，由取值的**符号**决定。
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        date = row.get("date")
        if not date:
            continue
        date = str(date)
        for key in CANDLE_KEYS:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value == 0:
                continue
            meta = PATTERN_BY_KEY[key]
            out.append({
                "date": date,
                "type": key,
                "name": meta["name"],
                "direction": "bullish" if value > 0 else "bearish",
                # 绝对值：100 是标准，80 是较弱确认，200 是强信号。
                # 前端只用来区分"强/弱"，不参与方向判断。
                "strength": int(abs(value)),
                "desc": meta["desc"],
            })

    out.sort(key=lambda x: x["date"])
    if days is not None and days > 0 and len(out) > 0:
        # 按**日期**截尾而不是按条数：同一天可能命中多个形态，
        # 按条数截会把最后一天的形态切掉一半。
        cutoff = sorted({item["date"] for item in out})[-days:]
        keep = set(cutoff)
        out = [item for item in out if item["date"] in keep]
    return out


def summarize_candles(series: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """按类型聚合，给「最近出现了什么」这类问题用。"""
    counts: Dict[str, int] = {}
    for item in series:
        counts[item["name"]] = counts.get(item["name"], 0) + 1
    latest = series[-1] if series else None
    return {
        "total": len(series),
        "by_name": counts,
        "latest_date": latest["date"] if latest else None,
        "latest": [i for i in series if latest and i["date"] == latest["date"]],
    }
