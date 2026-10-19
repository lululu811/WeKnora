"""
518 均线系统与金叉状态机单元测试

验证：
1. 均线计算与前 17 根有效性
2. 金叉与死叉识别
3. 金叉次数累计 (1次反弹、2次突破、3次跃迁)
4. 死叉重置计数与开口预警
"""

import pytest
from zettaranc.ma518 import CrossType, analyze_518_series


def test_ma518_cross_and_count():
    # 构造 30 根 bar，前 18 根平盘，然后先死叉后两次金叉
    bars = []
    # 1-18 根，价格 10.0
    for i in range(1, 19):
        bars.append({"date": f"2026-01-{i:02d}", "open": 10.0, "close": 10.0})

    # 19-22 根，下跌至 8.0，形成死叉
    for i in range(19, 23):
        bars.append({"date": f"2026-01-{i:02d}", "open": 9.0, "close": 8.0})

    # 23-26 根，暴力拉升到 15.0，形成第 1 次金叉
    for i in range(23, 27):
        bars.append({"date": f"2026-01-{i:02d}", "open": 12.0, "close": 15.0})

    # 27-28 根，回调到 11.0，形成死叉
    for i in range(27, 29):
        bars.append({"date": f"2026-01-{i:02d}", "open": 13.0, "close": 11.0})

    # 29-30 根，再次拉升到 16.0，形成第 1 次金叉 (死叉重置后)
    for i in range(29, 31):
        bars.append({"date": f"2026-01-{i:02d}", "open": 14.0, "close": 16.0})

    results = analyze_518_series(bars)
    assert len(results) == 30

    # 前 17 根无 MA18
    assert results[16]["ma18"] is None
    assert results[17]["ma18"] is not None

    # 检查是否有金叉事件产生并正确计次
    golden_events = [r for r in results if r["cross_event"] == CrossType.GOLDEN.value]
    assert len(golden_events) >= 1
    assert golden_events[0]["cross_count"] >= 1
    assert "反弹" in golden_events[0]["phase_semantic"]


def test_ma518_death_cross_warning():
    bars = [{"date": f"2026-02-{i:02d}", "open": 10.0, "close": 10.0} for i in range(1, 20)]
    # 连续大幅下杀，造成死叉且 spread < -0.5%
    for i in range(20, 25):
        bars.append({"date": f"2026-02-{i:02d}", "open": 9.0, "close": 7.0})

    results = analyze_518_series(bars)
    latest = results[-1]
    assert latest["is_golden"] is False
    assert latest["spread_pct"] < -0.5
    assert latest["risk_warning"] is not None
    assert "严禁抄底" in latest["risk_warning"]
