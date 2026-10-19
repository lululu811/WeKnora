"""
双上五四态状态机单元测试

验证：
1. 双上五 (BOTH_UP_5): 量上 5 日均量且价上 5 日均线
2. 阴量上五 (YIN_VOL_UP_5): 阴线但量上 5 日均量
3. 量上五(价未上) (VOL_UP_PRICE_DOWN_5): 阳线/平盘，量上 5 日均量，价未上 5 日均线
4. 价上五(量未上) (PRICE_UP_VOL_DOWN_5): 价上 5 日均线，量未上 5 日均量
5. 双下五 (BOTH_DOWN_5): 价破 5 日均线且量破 5 日均量
6. 序列计算与前 4 根 None 守护
"""

import pytest
from zettaranc.micro_volume import (
    DualAbove5State,
    calc_dual_above_5_series,
    classify_dual_above_5,
)


def test_classify_both_up_5():
    res = classify_dual_above_5(
        close=10.5,
        open_=10.0,
        volume=1200.0,
        ma5=10.0,
        vol_ma5=1000.0,
        ma18=9.5,
    )
    assert res["state"] == DualAbove5State.BOTH_UP_5.value
    assert res["action"] == "BUY_OR_HOLD"
    assert res["price_up"] is True
    assert res["vol_up"] is True
    assert res["is_518_golden"] is True
    assert res["confidence"] == 0.9


def test_classify_yin_vol_up_5():
    res = classify_dual_above_5(
        close=9.8,
        open_=10.2,
        volume=1500.0,
        ma5=10.0,
        vol_ma5=1000.0,
    )
    assert res["state"] == DualAbove5State.YIN_VOL_UP_5.value
    assert res["action"] == "PROBE_BUY_LEFT"
    assert res["is_yin"] is True
    assert res["vol_up"] is True
    assert res["price_up"] is False


def test_classify_vol_up_price_down_5():
    res = classify_dual_above_5(
        close=9.9,
        open_=9.7,
        volume=1300.0,
        ma5=10.0,
        vol_ma5=1000.0,
    )
    assert res["state"] == DualAbove5State.VOL_UP_PRICE_DOWN_5.value
    assert res["action"] == "HOLD_WATCH_BULLISH"
    assert res["is_yin"] is False
    assert res["vol_up"] is True
    assert res["price_up"] is False


def test_classify_price_up_vol_down_5():
    res = classify_dual_above_5(
        close=10.5,
        open_=10.2,
        volume=800.0,
        ma5=10.0,
        vol_ma5=1000.0,
    )
    assert res["state"] == DualAbove5State.PRICE_UP_VOL_DOWN_5.value
    assert res["action"] == "ALERT_WEAK"
    assert res["price_up"] is True
    assert res["vol_up"] is False


def test_classify_both_down_5():
    res = classify_dual_above_5(
        close=9.5,
        open_=9.8,
        volume=700.0,
        ma5=10.0,
        vol_ma5=1000.0,
    )
    assert res["state"] == DualAbove5State.BOTH_DOWN_5.value
    assert res["action"] == "EXIT_DEFENSE"
    assert res["price_up"] is False
    assert res["vol_up"] is False


def test_series_calc_guard_first_four_bars():
    bars = [
        {"date": f"2026-05-{i:02d}", "open": 10.0, "close": 10.0 + i, "volume": 1000.0}
        for i in range(1, 10)
    ]
    series = calc_dual_above_5_series(bars)
    assert len(series) == 9
    for i in range(4):
        assert series[i]["state"] is None
        assert series[i]["action"] == "WAIT"

    # 第 5 根 (index 4) 起有有效状态
    assert series[4]["state"] is not None
    assert series[4]["ma5"] is not None
    assert series[4]["vol_ma5"] is not None
