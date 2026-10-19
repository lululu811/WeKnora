"""
微观量价节拍器 — 双上五四态状态机

来源：《重剑无锋交易框架技术手册》3.4 节（微观量价：双上五四态）
日级别的操作节拍器（5 日均线 × 5 日均量线构成四象限）：
1. 双上五 (BOTH_UP_5): 量上 5 日均量线 + 价上 5 日均线 -> 开仓（最好阶梯量），持股
2. 量上五 (VOL_UP_PRICE_DOWN_5): 量能站上均量线、价格未站上均线 -> 持有/警戒偏多（量比价重要，多头在承接）
   - 细分阴量上五 (YIN_VOL_UP_5): 阴量上五是先手（风险机会并存，左侧分批低吸，但缩量永不补）
3. 价上五 (PRICE_UP_VOL_DOWN_5): 价格站上均线、量能未跟上 -> 警戒（无量虚涨）
4. 双下五 (BOTH_DOWN_5): 量价齐破 -> 离场（主力弃庄）

铁律：
- 量比价重要：量能站上 5 日均量线多头就在承接，量破价未破可挺，量价齐破即主力弃庄。
- 阴量上五是先手：左侧阴量上五分批低吸，但缩量永不补。
- 双上五的三适用条件：518金叉、均线粘合、五段尾段。
"""

from typing import Any, Dict, List, Optional
from enum import Enum


class DualAbove5State(str, Enum):
    BOTH_UP_5 = "BOTH_UP_5"                       # 双上五
    YIN_VOL_UP_5 = "YIN_VOL_UP_5"                 # 阴量上五
    VOL_UP_PRICE_DOWN_5 = "VOL_UP_PRICE_DOWN_5"   # 量上五(价未上)
    PRICE_UP_VOL_DOWN_5 = "PRICE_UP_VOL_DOWN_5"   # 价上五(量未上)
    BOTH_DOWN_5 = "BOTH_DOWN_5"                   # 双下五


def calc_sma(values: List[float], period: int) -> List[Optional[float]]:
    """简单移动平均 (SMA)，前 period-1 根返回 None"""
    res: List[Optional[float]] = []
    for i in range(len(values)):
        if i < period - 1:
            res.append(None)
        else:
            window = values[i - period + 1 : i + 1]
            res.append(sum(window) / period)
    return res


def classify_dual_above_5(
    close: float,
    open_: float,
    volume: float,
    ma5: float,
    vol_ma5: float,
    ma18: Optional[float] = None,
) -> Dict[str, Any]:
    """
    对单个 Bar 进行双上五四态判定。

    参数:
        close: 收盘价
        open_: 开盘价
        volume: 成交量
        ma5: 5日价格均线
        vol_ma5: 5日均量线
        ma18: 18日均线 (可选，用于检验 518 金叉背景)
    """
    price_up = close >= ma5
    vol_up = volume >= vol_ma5
    is_yin = close < open_

    is_518_golden = (ma5 >= ma18) if ma18 is not None else None

    if price_up and vol_up:
        # 1. 双上五
        confidence = 0.9 if (is_518_golden is True) else 0.75
        desc = "量上5日均量线 + 价上5日均线（量价齐升）"
        if is_518_golden is True:
            desc += "，处于518金叉攻击波次"
        elif is_518_golden is False:
            desc += "，但处于518死叉压制下，信号打折"

        return {
            "state": DualAbove5State.BOTH_UP_5.value,
            "label": "双上五",
            "action": "BUY_OR_HOLD",
            "price_up": True,
            "vol_up": True,
            "is_yin": is_yin,
            "is_518_golden": is_518_golden,
            "confidence": confidence,
            "desc": desc,
        }

    elif (not price_up) and vol_up:
        # 2. 量上五 (价未上)
        if is_yin:
            # 阴量上五
            return {
                "state": DualAbove5State.YIN_VOL_UP_5.value,
                "label": "阴量上五",
                "action": "PROBE_BUY_LEFT",
                "price_up": False,
                "vol_up": True,
                "is_yin": True,
                "is_518_golden": is_518_golden,
                "confidence": 0.65,
                "desc": "阴量上五先手（多头承接放量，可分批低吸但缩量永不补仓）",
            }
        else:
            return {
                "state": DualAbove5State.VOL_UP_PRICE_DOWN_5.value,
                "label": "量上五(价未上)",
                "action": "HOLD_WATCH_BULLISH",
                "price_up": False,
                "vol_up": True,
                "is_yin": False,
                "is_518_golden": is_518_golden,
                "confidence": 0.65,
                "desc": "量能站上5日均量线、价格未站上5日均线（量比价重要，多头在承接，保持戒备偏多）",
            }

    elif price_up and (not vol_up):
        # 3. 价上五 (量未上)
        return {
            "state": DualAbove5State.PRICE_UP_VOL_DOWN_5.value,
            "label": "价上五(量未上)",
            "action": "ALERT_WEAK",
            "price_up": True,
            "vol_up": False,
            "is_yin": is_yin,
            "is_518_golden": is_518_golden,
            "confidence": 0.5,
            "desc": "价格站上5日均线但量能未跟上（无量反弹，防冲高回落，戒备不可追高）",
        }

    else:
        # 4. 双下五
        return {
            "state": DualAbove5State.BOTH_DOWN_5.value,
            "label": "双下五",
            "action": "EXIT_DEFENSE",
            "price_up": False,
            "vol_up": False,
            "is_yin": is_yin,
            "is_518_golden": is_518_golden,
            "confidence": 0.85,
            "desc": "量价齐破5日线与5日均量线（主力弃庄，离场防守）",
        }


def calc_dual_above_5_series(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对完整按时间升序排序的 Bar 序列计算双上五状态。
    若前 4 根数据不足，返回 state=None。
    """
    if not bars:
        return []

    closes = [float(b["close"]) for b in bars]
    opens = [float(b["open"]) for b in bars]
    vols = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]

    ma5_series = calc_sma(closes, 5)
    vol_ma5_series = calc_sma(vols, 5)
    ma18_series = calc_sma(closes, 18)

    results: List[Dict[str, Any]] = []
    for i in range(len(bars)):
        bar = bars[i]
        date_str = str(bar.get("date", ""))
        ma5_val = ma5_series[i]
        vol_ma5_val = vol_ma5_series[i]
        ma18_val = ma18_series[i]

        if ma5_val is None or vol_ma5_val is None:
            results.append({
                "date": date_str,
                "state": None,
                "label": "数据不足",
                "action": "WAIT",
                "price_up": None,
                "vol_up": None,
                "ma5": None,
                "vol_ma5": None,
                "ma18": ma18_val,
                "desc": "数据不足5根，无法计算5日均线与均量线",
            })
            continue

        c = closes[i]
        o = opens[i]
        v = vols[i]

        info = classify_dual_above_5(c, o, v, ma5_val, vol_ma5_val, ma18_val)
        info["date"] = date_str
        info["ma5"] = round(ma5_val, 3)
        info["vol_ma5"] = round(vol_ma5_val, 3)
        info["ma18"] = round(ma18_val, 3) if ma18_val is not None else None
        results.append(info)

    return results


def analyze_micro_volume(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析微观量价状态（双上五四态），返回最新状态。
    """
    if not bars:
        return {"state": None, "label": "无数据", "action": "WAIT", "desc": "无K线数据"}
    series = calc_dual_above_5_series(bars)
    if not series:
        return {"state": None, "label": "数据不足", "action": "WAIT", "desc": "数据不足"}
    latest = series[-1]
    latest["series_length"] = len(series)
    return latest
