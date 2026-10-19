"""
神奇九转单元测试
"""

import pytest
from zettaranc.td_setup import calc_td_sequence, detect_td_exhaustion


def test_td_low_9_exhaustion():
    # 构造连续下跌的 15 根 K 线，使得连续 9 根 close < close[i-4]
    bars = []
    # 4 根基准价格 20.0
    for i in range(4):
        bars.append({"date": f"2026-03-{i+1:02d}", "close": 20.0})
    # 接下来 9 根，价格一路下跌 19.0 -> 10.0，全部低于 4 根前
    for i in range(4, 13):
        bars.append({"date": f"2026-03-{i+1:02d}", "close": 20.0 - float(i)})

    seq = calc_td_sequence(bars)
    assert len(seq) == 13
    assert seq[-1]["buy_count"] == 9
    assert seq[-1]["signal_tag"] == "LOW_9"

    exhaustion = detect_td_exhaustion(bars)
    assert exhaustion["status"] == "LOW_9"
    assert exhaustion["type"] == "bullish_exhaustion"
    assert exhaustion["action"] == "WATCH_REVERSAL"


def test_td_high_9_exhaustion():
    # 构造连续上涨的 15 根 K 线，使得连续 9 根 close > close[i-4]
    bars = []
    for i in range(4):
        bars.append({"date": f"2026-04-{i+1:02d}", "close": 10.0})
    for i in range(4, 13):
        bars.append({"date": f"2026-04-{i+1:02d}", "close": 10.0 + float(i)})

    seq = calc_td_sequence(bars)
    assert seq[-1]["sell_count"] == 9
    assert seq[-1]["signal_tag"] == "HIGH_9"

    exhaustion = detect_td_exhaustion(bars)
    assert exhaustion["status"] == "HIGH_9"
    assert exhaustion["type"] == "bearish_exhaustion"
    assert exhaustion["action"] == "TAKE_PROFIT_DEFENSE"
