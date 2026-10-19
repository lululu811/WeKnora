"""
三大交易系统状态机与抄底五条件评估器单元测试
"""

import pytest
from zettaranc.systems import evaluate_bottom_fishing_conditions, classify_trading_system


def _generate_bars(n: int, trend: str = "down", base_price: float = 20.0):
    bars = []
    price = base_price
    for i in range(n):
        if trend == "down":
            price *= 0.985  # 持续下跌
        elif trend == "up":
            price *= 1.015  # 持续上涨
        vol = 50000.0 + (i % 5) * 5000.0
        bars.append({
            "date": f"2026-03-{i+1:02d}" if i < 28 else f"2026-04-{i-27:02d}",
            "open": round(price * 1.005, 2),
            "high": round(price * 1.01, 2),
            "low": round(price * 0.99, 2),
            "close": round(price, 2),
            "volume": vol,
        })
    return bars


def test_bottom_fishing_conditions():
    # 构造持续暴跌且带有恐慌放量阴线的场景
    bars = _generate_bars(25, trend="down", base_price=30.0)
    # 在最后几天制造放量阴线
    bars[-3]["volume"] = 250000.0
    bars[-3]["open"] = bars[-3]["close"] * 1.04  # 大阴线

    res = evaluate_bottom_fishing_conditions(bars)
    assert "conditions" in res
    assert "satisfied_count" in res
    assert res["total_conditions"] == 5
    assert "drop_exhaustion" in res["conditions"]
    assert "ma_divergence" in res["conditions"]
    assert "panic_volume_bar" in res["conditions"]
    # 暴跌 25 天，drop_exhaustion 必然满足
    assert res["conditions"]["drop_exhaustion"]["satisfied"] is True
    # 放量阴线也应满足
    assert res["conditions"]["panic_volume_bar"]["satisfied"] is True


def test_classify_trend_system():
    # 模拟处于多头排列、周线双上五的强势股票
    bars = _generate_bars(35, trend="up", base_price=10.0)
    # 构造 mock 的均线与月线结果
    mock_ma518 = {
        "cross_state": "GOLDEN_CROSS",
        "golden_cross_times": 3,
        "ma5": 16.0,
        "ma18": 14.5,
        "spread_pct": 10.3,
    }
    mock_gate = {
        "gate_open": True,
        "gate_status": "DOUBLE_PASS",
        "max_allowed_position": 1.0,
        "ma60m_context": {"above_ma60m": True},
    }
    mock_weekly = {
        "is_weekly_dual_up5": True,
    }

    res = classify_trading_system(
        bars,
        ma518_result=mock_ma518,
        monthly_gate=mock_gate,
        weekly_dual=mock_weekly,
    )
    assert res["system"] == "TREND"
    assert res["recommended_position"] >= 0.6
    assert "锁仓吃满主升" in res["discipline"]


def test_classify_rebound_system():
    # 模拟大级别死叉超跌，但满足抄底条件
    bars = _generate_bars(25, trend="down", base_price=25.0)
    bars[-2]["volume"] = 300000.0  # 恐慌盘

    mock_ma518 = {
        "cross_state": "DEAD_CROSS",
        "spread_pct": -4.2,  # 负乖离大
        "dead_cross_warning": False,
    }
    mock_gate = {
        "gate_open": False,
        "gate_status": "DOUBLE_LOST",
        "max_allowed_position": 0.1,
    }
    mock_weekly = {
        "is_weekly_dual_up5": False,
    }

    res = classify_trading_system(
        bars,
        ma518_result=mock_ma518,
        monthly_gate=mock_gate,
        weekly_dual=mock_weekly,
    )
    # 应定性为反弹交易系统或防御系统
    assert res["system"] in ["REBOUND", "DEFENSIVE_HOLD_CASH"]
    assert res["recommended_position"] <= 0.20
