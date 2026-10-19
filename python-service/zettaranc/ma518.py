"""
518 均线系统与交叉次数状态机

来源：《重剑无锋交易框架技术手册》3.2, 3.5, 7.5 节：
- 518 金叉 (5日与18日均线金叉)：体系核心择时指标，攻击波次的点火信号
- 金叉次数语义：
  - 第 1 次金叉 = 做反弹（需在底背离处左侧建仓，追高必套）
  - 第 2 次金叉 = 做突破（伴随均线粘合，是有劲的启动点）
  - 第 3 次金叉 = 做跃迁（加速主升行情）
- 518 死叉五要点：
  1. 逃顶位（放量滞涨+实体变小）
  2. 套牢量铁律（无套牢量不下跌）
  3. 回光返照（死叉后的弱反=下跌中继）
  4. 左侧放量阴线博反抽
  5. 30/15 分钟底背离博底
- 死叉铁律：
  - 死叉之初绝不抄（后边仓位补不动，开口刚变大的双上五必打折扣）
  - 518 死叉之下只做反抽、不做过夜趋势
"""

from typing import Any, Dict, List, Optional
from enum import Enum


class CrossType(str, Enum):
    GOLDEN = "GOLDEN"  # 金叉
    DEATH = "DEATH"    # 死叉
    NONE = "NONE"


def calc_sma(values: List[float], period: int) -> List[Optional[float]]:
    """简单移动平均 (SMA)"""
    res: List[Optional[float]] = []
    for i in range(len(values)):
        if i < period - 1:
            res.append(None)
        else:
            window = values[i - period + 1 : i + 1]
            res.append(sum(window) / period)
    return res


def analyze_518_series(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对完整按时间升序排序的 Bar 序列计算 518 均线状态与金叉次数追踪。
    """
    if not bars:
        return []

    closes = [float(b["close"]) for b in bars]
    ma5_series = calc_sma(closes, 5)
    ma18_series = calc_sma(closes, 18)

    results: List[Dict[str, Any]] = []
    golden_cross_count = 0
    in_golden_state = False

    for i in range(len(bars)):
        bar = bars[i]
        date_str = str(bar.get("date", ""))
        ma5_curr = ma5_series[i]
        ma18_curr = ma18_series[i]

        if ma5_curr is None or ma18_curr is None:
            results.append({
                "date": date_str,
                "ma5": None,
                "ma18": None,
                "spread_pct": None,
                "is_golden": None,
                "cross_event": CrossType.NONE.value,
                "cross_count": 0,
                "phase_semantic": "数据不足",
                "risk_warning": None,
            })
            continue

        spread_pct = ((ma5_curr - ma18_curr) / ma18_curr) * 100.0
        is_golden = ma5_curr >= ma18_curr

        ma5_prev = ma5_series[i - 1]
        ma18_prev = ma18_series[i - 1]

        cross_event = CrossType.NONE.value
        risk_warning = None

        if ma5_prev is not None and ma18_prev is not None:
            # 检测金叉：前一根 MA5 <= MA18，当前 MA5 > MA18
            if ma5_prev <= ma18_prev and ma5_curr > ma18_curr:
                cross_event = CrossType.GOLDEN.value
                golden_cross_count += 1
                in_golden_state = True

            # 检测死叉：前一根 MA5 >= MA18，当前 MA5 < MA18
            elif ma5_prev >= ma18_prev and ma5_curr < ma18_curr:
                cross_event = CrossType.DEATH.value
                in_golden_state = False
                golden_cross_count = 0  # 死叉重置金叉计数器

        # 语义定性
        if is_golden:
            if golden_cross_count == 1:
                phase_semantic = "一次金叉 (做反弹段)"
            elif golden_cross_count == 2:
                phase_semantic = "二次金叉 (做突破段)"
            elif golden_cross_count >= 3:
                phase_semantic = f"三次及以上金叉 ({golden_cross_count}次金叉，主升跃迁段)"
            else:
                phase_semantic = "518多头排列"
        else:
            phase_semantic = "518死叉空头受制"
            if spread_pct < -0.5:
                risk_warning = "死叉开口放大，死叉之初严禁抄底补仓；死叉之下不做过夜趋势"

        results.append({
            "date": date_str,
            "ma5": round(ma5_curr, 3),
            "ma18": round(ma18_curr, 3),
            "spread_pct": round(spread_pct, 2),
            "is_golden": is_golden,
            "cross_event": cross_event,
            "cross_count": golden_cross_count,
            "phase_semantic": phase_semantic,
            "risk_warning": risk_warning,
        })

    return results


def analyze_ma518(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析 518 均线系统，返回最新状态与语义定性。
    """
    if not bars:
        return {"cross_state": "NONE", "spread_pct": 0.0, "golden_cross_times": 0}
    series = analyze_518_series(bars)
    if not series:
        return {"cross_state": "NONE", "spread_pct": 0.0, "golden_cross_times": 0}
    latest = series[-1]
    is_golden = latest.get("is_golden")
    cross_state = "GOLDEN_CROSS" if is_golden else "DEAD_CROSS"
    dead_warning = (latest.get("risk_warning") is not None)
    return {
        "date": latest.get("date"),
        "ma5": latest.get("ma5"),
        "ma18": latest.get("ma18"),
        "spread_pct": latest.get("spread_pct", 0.0),
        "cross_state": cross_state,
        "is_golden": is_golden,
        "golden_cross_times": latest.get("cross_count", 0),
        "cross_event": latest.get("cross_event"),
        "phase_semantic": latest.get("phase_semantic"),
        "dead_cross_warning": dead_warning,
        "risk_warning": latest.get("risk_warning"),
        "series": series,
    }
