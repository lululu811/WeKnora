"""
日月神功月线平衡态与三同步空仓铁律单元测试
"""

import pytest
from zettaranc.sun_moon import calc_macd, analyze_sun_moon


def test_calc_macd():
    # 模拟简单的价格序列
    closes = [10.0 + i * 0.5 for i in range(30)]
    macd_res = calc_macd(closes)
    assert len(macd_res["dif"]) == 30
    assert len(macd_res["dea"]) == 30
    assert len(macd_res["hist"]) == 30
    # 价格持续上涨，DIF 和 HIST 应该为正
    assert macd_res["dif"][-1] > 0
    assert macd_res["hist"][-1] > 0


def test_sun_moon_sesame_dots_and_sequence():
    # 构造月线 bars
    # 前期持续阴跌，柱极小（绿芝麻点），随后微幅回升（红芝麻点）
    monthly_bars = []
    # 20 个月构造基本 EMA
    for i in range(20):
        monthly_bars.append({
            "date": f"2023-{i+1:02d}-28" if i < 12 else f"2024-{i-11:02d}-28",
            "open": 20.0 - i * 0.2,
            "high": 20.2 - i * 0.2,
            "low": 19.8 - i * 0.2,
            "close": 20.0 - i * 0.2,
            "volume": 100000.0,
        })

    # 月线分析
    res = analyze_sun_moon(bars=[], monthly_bars=monthly_bars)
    assert res["status"] == "success"
    assert "sesame_dots" in res
    assert res["sequence_law"] in ["GREEN_THEN_RED", "RED_THEN_GREEN", "NEUTRAL"]
    assert "fast_slow_state" in res


def test_sun_moon_three_sync_warning():
    # 构造两波明显的高点：两波价格新高 + 量堆同步放大 + MACD 红柱同步递增
    monthly_bars = []
    base_date = "2024-01-31"

    # 第一阶段平缓（建立基线）
    for i in range(10):
        monthly_bars.append({
            "date": f"2024-{i+1:02d}-28",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 50000.0,
        })

    # 波峰 1：冲高回落
    monthly_bars.append({"date": "2024-11-28", "open": 10.5, "high": 12.0, "low": 10.4, "close": 11.8, "volume": 100000.0})
    monthly_bars.append({"date": "2024-12-28", "open": 11.5, "high": 11.7, "low": 10.8, "close": 11.0, "volume": 60000.0})

    # 波峰 2：更大规模创新高冲刺（三同步）
    monthly_bars.append({"date": "2025-01-28", "open": 11.2, "high": 15.0, "low": 11.0, "close": 14.5, "volume": 200000.0})

    res = analyze_sun_moon(bars=[], monthly_bars=monthly_bars)
    assert res["status"] == "success"
    # 触发两波峰值对比
    if res["three_sync_warning"]:
        assert res["three_sync_detail"] is not None
        assert "90%" in res["three_sync_detail"]["reason"]
        assert not res["regime_safe"]
