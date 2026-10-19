"""
神奇九转 (狄马克 TD Setup 序列) 计算与衰竭窗口检测

来源：《重剑无锋交易框架技术手册》3.7 节：
- 数数的前提 = 驱动浪有惯性（有效趋势行情才数九转；无惯性无效行情别动手）
- 高九 (Sell Setup 9): 连续 9 根 K 线收盘价高于 4 根前收盘价 -> 上涨动能衰竭窗口，逢高减仓防回落
- 低九 (Buy Setup 9): 连续 9 根 K 线收盘价低于 4 根前收盘价 -> 下跌动能衰竭窗口，低九之后只有"加速跌/止跌"两种状态
- 节奏法则：
  - 第 4-7 根不做决定（等一句话说完）
  - 第 6、9 根关键，第 7 根定生死
  - 九转 + 阶梯量组合使用
"""

from typing import Any, Dict, List, Optional


def calc_td_sequence(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对完整按时间升序排序的 Bar 序列计算 TD 九转计数。
    返回每个 bar 对应的:
    - td_buy_count: 0..9 (低九计数)
    - td_sell_count: 0..9 (高九计数)
    """
    if len(bars) < 5:
        return [{"buy_count": 0, "sell_count": 0} for _ in bars]

    closes = [float(b["close"]) for b in bars]
    n = len(bars)

    buy_counts = [0] * n
    sell_counts = [0] * n

    current_buy = 0
    current_sell = 0

    for i in range(4, n):
        c = closes[i]
        c_prev4 = closes[i - 4]

        # 买入准备（低九计数：close < close[i-4]）
        if c < c_prev4:
            current_buy += 1
            if current_buy > 9:
                current_buy = 1  # 超过 9 后重新计数或重置
        else:
            current_buy = 0

        # 卖出准备（高九计数：close > close[i-4]）
        if c > c_prev4:
            current_sell += 1
            if current_sell > 9:
                current_sell = 1
        else:
            current_sell = 0

        buy_counts[i] = current_buy
        sell_counts[i] = current_sell

    results: List[Dict[str, Any]] = []
    for i in range(n):
        b_cnt = buy_counts[i]
        s_cnt = sell_counts[i]

        signal_tag = None
        if b_cnt == 9:
            signal_tag = "LOW_9"   # 低九
        elif s_cnt == 9:
            signal_tag = "HIGH_9"  # 高九
        elif b_cnt in (6, 7):
            signal_tag = f"BUY_{b_cnt}"
        elif s_cnt in (6, 7):
            signal_tag = f"SELL_{s_cnt}"

        results.append({
            "date": str(bars[i].get("date", ""))[:10],
            "buy_count": b_cnt,
            "sell_count": s_cnt,
            "signal_tag": signal_tag,
        })

    return results


def detect_td_exhaustion(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    检查最新 Bar 是否处于九转衰竭或关键变盘窗口。
    """
    if len(bars) < 5:
        return {
            "status": "none",
            "desc": "数据不足，未形成有效九转序列",
        }

    seq = calc_td_sequence(bars)
    latest = seq[-1]
    b_cnt = latest["buy_count"]
    s_cnt = latest["sell_count"]

    if b_cnt == 9:
        return {
            "status": "LOW_9",
            "type": "bullish_exhaustion",
            "count": 9,
            "desc": "神奇九转出【低九】：下跌动能衰竭，进入变盘窗口（准备止跌或极端加速，关注右侧反包买点）",
            "action": "WATCH_REVERSAL",
        }
    elif s_cnt == 9:
        return {
            "status": "HIGH_9",
            "type": "bearish_exhaustion",
            "count": 9,
            "desc": "神奇九转出【高九】：上涨动能衰竭，处于高抛警戒区，防冲高回落",
            "action": "TAKE_PROFIT_DEFENSE",
        }
    elif b_cnt in (6, 7, 8):
        return {
            "status": f"BUY_COUNT_{b_cnt}",
            "type": "downward_driving",
            "count": b_cnt,
            "desc": f"空头驱动浪连续推进至第 {b_cnt} 根，第4-7根不做决定，等序列走完",
            "action": "WAIT",
        }
    elif s_cnt in (6, 7, 8):
        return {
            "status": f"SELL_COUNT_{s_cnt}",
            "type": "upward_driving",
            "count": s_cnt,
            "desc": f"多头驱动浪连续推进至第 {s_cnt} 根，第4-7根持股观望，等高九兑现",
            "action": "HOLD",
        }
    else:
        return {
            "status": "none",
            "count": max(b_cnt, s_cnt),
            "desc": "当前处于常态波动，未触发九转关键节点",
            "action": "NORMAL",
        }


def analyze_td_setup(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析 TD 神奇九转序列，返回最新衰竭状态与详细信息。
    """
    if len(bars) < 5:
        return {"td_type": None, "td_count": 0, "status": "none", "buy_count": 0, "sell_count": 0}
    seq = calc_td_sequence(bars)
    latest = seq[-1]
    exhaustion = detect_td_exhaustion(bars)
    b_cnt = latest["buy_count"]
    s_cnt = latest["sell_count"]
    if b_cnt > 0:
        td_type = "BUY_SETUP"
        td_count = b_cnt
    elif s_cnt > 0:
        td_type = "SELL_SETUP"
        td_count = s_cnt
    else:
        td_type = "NONE"
        td_count = 0

    return {
        "td_type": td_type,
        "td_count": td_count,
        "buy_count": b_cnt,
        "sell_count": s_cnt,
        "status": exhaustion.get("status"),
        "desc": exhaustion.get("desc"),
        "action": exhaustion.get("action"),
        "latest": latest,
    }
