"""
确定性风控总闸门与 8 条负面禁做清单单元测试
"""

import pytest
from zettaranc.guardrail import evaluate_risk_guardrail


def test_guardrail_block_shrink_drop():
    # 构造缩量阴跌形态：价格下跌且量能萎缩
    bars = []
    p = 20.0
    v = 100000.0
    for i in range(10):
        p *= 0.985
        v *= 0.90  # 缩量
        bars.append({
            "date": f"2026-03-{i+1:02d}",
            "open": round(p * 1.01, 2),
            "high": round(p * 1.02, 2),
            "low": round(p * 0.99, 2),
            "close": round(p, 2),
            "volume": v,
        })

    mock_ma518 = {
        "cross_state": "DEAD_CROSS",
        "dead_cross_warning": True,
    }

    res = evaluate_risk_guardrail(bars, ma518_result=mock_ma518)
    # 应被阻断或提示 BLOCKER
    violation_codes = [v["code"] for v in res["violations"]]
    assert "RULE_SHRINK_DROP_NO_BOTTOM" in violation_codes
    assert res["allowed_to_buy"] is False
    assert res["max_recommended_position"] == 0.0


def test_guardrail_block_three_sync():
    bars = [
        {"date": f"2026-01-{i+1:02d}", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 50000.0}
        for i in range(15)
    ]
    mock_sun_moon = {
        "three_sync_warning": True,
        "three_sync_detail": {"reason": "两波价格新高+量堆放大+MACD柱递增"},
    }

    res = evaluate_risk_guardrail(bars, sun_moon_result=mock_sun_moon)
    violation_codes = [v["code"] for v in res["violations"]]
    assert "RULE_THREE_SYNC_EXHAUSTION" in violation_codes
    assert res["allowed_to_buy"] is False
    assert res["risk_level"] == "CRITICAL"


def test_guardrail_monthly_gate_restriction():
    bars = [
        {"date": f"2026-01-{i+1:02d}", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 50000.0}
        for i in range(15)
    ]
    mock_monthly_gate = {
        "gate_open": False,
        "gate_status": "DOUBLE_LOST",
        "max_allowed_position": 0.1,
    }
    mock_ma518 = {
        "cross_state": "GOLDEN_CROSS",
        "dead_cross_warning": False,
    }

    res = evaluate_risk_guardrail(bars, monthly_gate=mock_monthly_gate, ma518_result=mock_ma518)
    violation_codes = [v["code"] for v in res["violations"]]
    assert "RULE_MONTHLY_GATE_CLOSED" in violation_codes
    # 限制仓位不超过 10%
    assert res["max_recommended_position"] <= 0.10
    assert res["single_loss_max_pct"] == 2.0
    assert "三天白玩" in res["three_days_exit_rule"]
