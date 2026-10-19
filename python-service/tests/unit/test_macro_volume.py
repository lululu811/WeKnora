"""
宏观量价分析模块单元测试
"""

import pytest
from zettaranc.macro_volume import analyze_macro_volume


def test_analyze_macro_volume_insufficient():
    res = analyze_macro_volume([])
    assert res["status"] == "insufficient_data"


def test_analyze_macro_volume_bottom_exceeds_top():
    # 构造40根日线，前半段高点放量1000，后半段更低价格但放量2000
    bars = []
    # 0-19: 价格较高(15.0)，量 1000
    for i in range(20):
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": 15.0,
            "high": 15.5,
            "low": 14.5,
            "close": 15.0,
            "volume": 1000.0,
        })
    # 20-39: 价格下行至(8.0)，底部放巨量 2500 (底量超顶量)
    for i in range(20):
        bars.append({
            "date": f"2026-02-{i+1:02d}",
            "open": 8.0,
            "high": 8.5,
            "low": 7.5,
            "close": 8.0,
            "volume": 2500.0 if i == 15 else 800.0,
        })

    res = analyze_macro_volume(bars)
    assert res["bottom_exceeds_top"] is True
    assert res["bottom_over_top_ratio"] >= 2.0
    assert "底量充沛" in res["summary"]
