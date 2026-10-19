"""
内存多周期聚合与月线总闸门单元测试
"""

import pytest
from zettaranc.resampler import (
    evaluate_monthly_gate,
    evaluate_weekly_dual_up5,
    resample_bars,
)


def test_resample_week_and_month():
    # 构造2026年1月全部交易日的日线
    bars = []
    # 假设1月有20个交易日 (从 2026-01-05 到 2026-01-30)
    for day in range(5, 26):
        bars.append({
            "date": f"2026-01-{day:02d}",
            "open": 10.0,
            "high": 12.0,
            "low": 9.5,
            "close": 11.0,
            "volume": 1000.0,
            "turnover": 10000.0,
        })

    # 周线聚合
    weeks = resample_bars(bars, freq="week")
    assert len(weeks) >= 3
    # 检查周线高低开收
    first_week = weeks[0]
    assert first_week["open"] == 10.0
    assert first_week["high"] == 12.0
    assert first_week["low"] == 9.5
    assert first_week["close"] == 11.0
    assert first_week["volume"] > 1000.0

    # 月线聚合
    months = resample_bars(bars, freq="month")
    assert len(months) == 1
    m = months[0]
    assert m["open"] == 10.0
    assert m["high"] == 12.0
    assert m["close"] == 11.0
    assert m["bar_count"] == len(bars)


def test_monthly_gate_evaluation():
    # 构造跨越 10 个月的日线
    bars = []
    for m in range(1, 11):
        for d in [10, 20]:
            bars.append({
                "date": f"2025-{m:02d}-{d:02d}",
                "open": 10.0 + m * 0.5,
                "high": 11.0 + m * 0.5,
                "low": 9.5 + m * 0.5,
                "close": 10.5 + m * 0.5,
                "volume": 10000.0,
            })

    gate = evaluate_monthly_gate(bars)
    assert gate["gate_open"] is True
    assert gate["gate_status"] == "DOUBLE_PASS"
    assert gate["max_allowed_position"] == 1.0


def test_weekly_dual_up5():
    # 构造跨越 8 周的日线
    bars = []
    for w in range(1, 9):
        for d in range(1, 6):
            bars.append({
                "date": f"2026-01-{(w-1)*5 + d:02d}", # 简化日期模拟
                "open": 10.0 + w * 0.2,
                "high": 11.0 + w * 0.2,
                "low": 9.8 + w * 0.2,
                "close": 10.8 + w * 0.2,
                "volume": 5000.0 * w,
            })

    res = evaluate_weekly_dual_up5(bars)
    assert res["is_weekly_dual_up5"] is True
    assert "周线双上五确立" in res["desc"]
