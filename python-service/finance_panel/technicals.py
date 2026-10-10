"""技术指标读数 —— 让「谁在动」能说出**为什么**。

解决的问题
----------
`/api/finance/pulse` 只回答"这只票今天量价异常"（放量几倍、涨跌几个点），
但没说"异常发生在什么位置"。一只涨 4% 的票，在 250 日线上方 3% 和在下方 12%，
是两件完全不同的事 —— 前者可能是延续，后者是反弹。工作台要能分开这两种。

数据源
------
`indicators.duckdb` 的 `v_indicators_daily`：**264 列、1036 万行、5573 只标的、
2016-09-12 起**。实测按代码取最新日 6 只需要 0.2 秒（DuckDB 的 zone map 让
无索引的全表扫在这个查询形状下依然很快），所以这里不做预聚合、不建索引。

**两个后端，时间上劈成两段**（这是必须知道的事实，不是可选信息）：

    zettaranc_migrate   718 万行   2016-09-12 → 2024-04-30
    pandas_ta|talib     318 万行   2024-05-06 → 2026-10-09

同一个指标列在 2024-05-06 前后由**不同实现**算出。所以本模块只读**最新日**
（永远落在 `pandas_ta|talib` 段内），并在响应里回带 `backend` ——
一旦哪天要做长周期回看（比如"过去两年在均线下方的时间占比"），
那段区间跨了实现切换点，结论会被两种算法的差异污染，那是另一件事。

口径
----
* **只读最新日**，不做跨日序列比较 —— 序列在 K 线工作台那边已经有了。
* **派生判断在服务端做**（`above_sma20` / `ma_alignment` / `rsi_zone` …），
  与 `WatchPulse.vue` 头部写的"前端不重算任何指标"是同一条约定：
  同一个指标只允许有一个地方算。
* **空 ≠ 零**。某只票没有 250 日历史时 `sma250` 是 null，位置判断也是 null，
  不是 0、也不是 false —— "还没到 250 日"和"在 250 日线下方"是两件事。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: 取哪些指标。不是把 264 列都搬出来 —— 一屏能读的字数有限，
#: 挑的是能回答"它现在在什么位置"的那几个，各自带明确含义：
#:
#:   sma20 / sma60 / sma250   短 / 中 / 长期成本线，位置关系说明趋势结构
#:   rsi14                    涨了多少（超买 = 追高风险）
#:   macd_hist                动量是在加强还是在衰竭
#:   bbands 上/下轨           波动区间的位置（触上轨 = 短期过热）
_FIELDS = (
    "overlap_sma_20",
    "overlap_sma_60",
    "overlap_sma_250",
    "momentum_rsi_14",
    "momentum_macd_12_26_9_hist",
    "volatility_bbands_20_2_0_upper",
    "volatility_bbands_20_2_0_lower",
)

_SELECT = ", ".join(_FIELDS)

#: RSI 分档。经典的 30 / 70 —— 这两个阈值本身有争议，但它们是行业惯例，
#: 换一套自己的阈值只会让读数无法与别人对照，所以沿用。
_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0

#: MACD 柱的"接近零"阈值。hist 是连续量，"放大/衰竭"需要一个死区，
#: 否则 0.0001 的变化也会被读成动量转向。
_MACD_DEADZONE = 0.05


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN


async def latest_technicals(
    src_indicators: Any,
    src_market: Any,
    thscodes: Sequence[str],
) -> Dict[str, Any]:
    """取一批标的的最新技术指标读数与派生判断。"""
    codes = [str(c).strip() for c in (thscodes or []) if str(c).strip()]
    if not codes:
        return {"ok": True, "as_of": None, "backend": None, "items": {}, "unavailable": []}

    unavailable: List[str] = []
    if src_indicators is None:
        unavailable.append("indicators")

    rows: List[Dict[str, Any]] = []
    as_of: Optional[Any] = None
    backend: Optional[str] = None
    if src_indicators is not None:
        try:
            ph = ",".join("(?)" for _ in codes)
            rows = await src_indicators.execute(
                f"""
                SELECT thscode, date, backend, {_SELECT}
                FROM v_indicators_daily
                WHERE thscode IN ({ph}) AND date = (SELECT max(date) FROM v_indicators_daily)
                ORDER BY thscode
                """,
                codes,
            )
        except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
            logger.warning("技术指标查询失败：%s", exc)
            unavailable.append("indicators")

    if rows:
        as_of = rows[0].get("date")
        backend = rows[0].get("backend")

    # 收盘价在 market 库（另一个 DuckDB），跨库不能 join，所以单独取。
    #
    # `v_daily` 的日期列叫 **date** 不是 `trade_date`（`v_index_daily` / `v_index_universe`
    # 那套才叫 trade_date），并且有 `interval` 列 —— 不加 `interval = '1d'` 过滤的话，
    # 一旦库里混入分钟级行，"最新日"会取到当天最后一根分钟线而不是收盘价。
    closes: Dict[str, float] = {}
    if src_market is not None and rows:
        try:
            ph = ",".join("(?)" for _ in codes)
            crows = await src_market.execute(
                f"""
                SELECT thscode, close FROM v_daily
                WHERE thscode IN ({ph}) AND interval = '1d'
                  AND date = (SELECT max(date) FROM v_daily WHERE interval = '1d')
                """,
                codes,
            )
            for r in crows:
                c = _f(r.get("close"))
                if c is not None:
                    closes[str(r.get("thscode"))] = c
        except Exception as exc:  # noqa: BLE001
            logger.warning("收盘价查询失败：%s", exc)
            unavailable.append("market")

    items: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        code = str(r.get("thscode"))
        close = closes.get(code)
        sma20 = _f(r.get("overlap_sma_20"))
        sma60 = _f(r.get("overlap_sma_60"))
        sma250 = _f(r.get("overlap_sma_250"))
        rsi = _f(r.get("momentum_rsi_14"))
        macd = _f(r.get("momentum_macd_12_26_9_hist"))
        bb_up = _f(r.get("volatility_bbands_20_2_0_upper"))
        bb_low = _f(r.get("volatility_bbands_20_2_0_lower"))

        items[code] = {
            "thscode": code,
            "date": r.get("date"),
            "close": close,
            "sma20": sma20,
            "sma60": sma60,
            "sma250": sma250,
            "rsi14": rsi,
            "macd_hist": macd,
            "bb_upper": bb_up,
            "bb_lower": bb_low,
            # 以下是派生判断，见模块头"派生判断在服务端做"
            "above_sma20": _cmp(close, sma20),
            "above_sma60": _cmp(close, sma60),
            "above_sma250": _cmp(close, sma250),
            "ma_alignment": _ma_alignment(close, sma20, sma60, sma250),
            "rsi_zone": _rsi_zone(rsi),
            "macd_state": _macd_state(macd),
            # 布林带位置：0 = 贴下轨，1 = 贴上轨。
            # **可能超出 [0,1]** —— 收盘价跌破下轨时是负数、突破上轨时大于 1，
            # 而这两种正是最值得看的情况（超跌 / 过热），夹到区间里就丢了。
            # 缺任一端（还没算布林带）才是 null，不用 0.5 顶替 ——
            # 那是"刚好在中间"，与"算不出来"不同。
            "bb_position": _bb_position(close, bb_low, bb_up),
        }

    # 请求了但没回来的（库里没这只票 / 当日没算）要显式列出，
    # 否则调用方分不清"没指标"和"没这只票"。
    missing = [c for c in codes if c not in items]

    return {
        "ok": True,
        "as_of": as_of,
        "backend": backend,
        "items": items,
        "missing": missing,
        "unavailable": unavailable,
        "note": "只取最新日；指标由 indicators.duckdb 计算，跨 2024-05-06 存在后端切换",
    }


def _cmp(a: Optional[float], b: Optional[float]) -> Optional[bool]:
    """a 是否在 b 上方。任一为 None → None（不是 False）。"""
    if a is None or b is None:
        return None
    return a > b


def _ma_alignment(
    close: Optional[float], sma20: Optional[float], sma60: Optional[float], sma250: Optional[float]
) -> Optional[str]:
    """均线排列：bull 多头 / bear 空头 / mixed 纠缠。

    三条线全在且方向一致才给结论；缺 250 日线（次新股）就只按已有的判，
    判不出来就是 mixed —— **不猜**。
    """
    if close is None or sma20 is None or sma60 is None:
        return None
    levels = [x for x in (sma20, sma60, sma250) if x is not None]
    above = [close > x for x in levels]
    if all(above):
        return "bull"
    if not any(above):
        return "bear"
    # 中间有交叉 → 纠缠
    return "mixed"


def _rsi_zone(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if v >= _RSI_OVERBOUGHT:
        return "overbought"
    if v <= _RSI_OVERSOLD:
        return "oversold"
    return "neutral"


def _macd_state(v: Optional[float]) -> Optional[str]:
    """只看**当前** hist 的符号与幅度。

    "strengthening / weakening" 需要连着两天的 hist 才能判断，而本模块只取
    最新日 —— 拿不到就不编，那种判断留给 K 线工作台的序列图。
    """
    if v is None:
        return None
    if abs(v) < _MACD_DEADZONE:
        return "flat"
    return "positive" if v > 0 else "negative"


def _bb_position(close: Optional[float], low: Optional[float], high: Optional[float]) -> Optional[float]:
    if close is None or low is None or high is None or high <= low:
        return None
    return round((close - low) / (high - low), 4)
