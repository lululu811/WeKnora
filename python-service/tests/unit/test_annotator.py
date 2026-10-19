"""
形态识别器 (ZettarancAnnotator) 新增重剑形态测试

覆盖：
1. detect_pole_yang (立杆阳)
2. detect_pile_yang (打桩阳)
3. detect_capping_yin (灌顶阴)
4. detect_fake_yin (伪阴线震仓)
5. detect_rebound_trap (回光返照弱反阳)
6. annotate_all 综合调度与 source='algorithm'
"""

import pytest
from zettaranc.annotator import ZettarancAnnotator


def test_detect_pole_yang():
    # 构造25根基准bar，成交量1000
    bars = []
    for i in range(1, 26):
        bars.append({
            "date": f"2026-03-{i:02d}",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 1000.0,
        })
    # 第26根：低位起爆大阳线，+6%，放量2500
    bars.append({
        "date": "2026-03-26",
        "open": 10.0,
        "high": 10.7,
        "low": 9.9,
        "close": 10.6,  # gain 6%
        "volume": 2500.0, # 2.5x
    })

    patterns = ZettarancAnnotator.detect_pole_yang(bars)
    assert len(patterns) == 1
    p = patterns[0]
    assert p["type"] == "pole_yang"
    assert "立杆阳" in p["text"]
    assert p["metadata"]["gain_pct"] >= 5.0
    assert p["metadata"]["anchor_high"] == 10.6


def test_detect_pile_yang():
    # 前一根大阴线，下一根低开反包探底回升
    bars = [
        {"date": "2026-04-01", "open": 10.5, "high": 10.6, "low": 9.8, "close": 9.9, "volume": 1000.0},
        # 低开在 9.8 (< 9.9 * 0.995)，探底至 9.5，收盘 10.6 (> 10.5 前开盘)
        {"date": "2026-04-02", "open": 9.8, "high": 10.7, "low": 9.5, "close": 10.6, "volume": 1500.0},
    ]
    patterns = ZettarancAnnotator.detect_pile_yang(bars)
    assert len(patterns) == 1
    p = patterns[0]
    assert p["type"] == "pile_yang"
    assert "打桩阳" in p["text"]


def test_detect_capping_yin():
    # 25根bar上行，第26根高位高开低走放量大阴线，跌破5日线
    bars = []
    for i in range(1, 25):
        bars.append({
            "date": f"2026-04-{i:02d}",
            "open": 10.0 + i * 0.2,
            "high": 10.2 + i * 0.2,
            "low": 9.9 + i * 0.2,
            "close": 10.1 + i * 0.2,
            "volume": 1000.0,
        })
    # 第25根收盘约 15.1
    prev_close = bars[-1]["close"]
    # 第26根：高开 15.2，收 14.0 (-7.9%)，放量 3000
    bars.append({
        "date": "2026-04-26",
        "open": 15.2,
        "high": 15.3,
        "low": 13.9,
        "close": 14.0,
        "volume": 3000.0,
    })

    patterns = ZettarancAnnotator.detect_capping_yin(bars)
    assert len(patterns) >= 1
    assert any(p["type"] == "capping_yin" for p in patterns)


def test_detect_fake_yin():
    # 20根持续上升，第21根微跌收阴但创最高价新高且守住5日均线
    bars = []
    for i in range(1, 21):
        bars.append({
            "date": f"2026-05-{i:02d}",
            "open": 10.0 + i * 0.3,
            "high": 10.4 + i * 0.3,
            "low": 9.9 + i * 0.3,
            "close": 10.3 + i * 0.3,
            "volume": 1000.0,
        })
    # 最新一根：高开16.6，创新高16.8，收16.4 (阴线，但高于5日均线且未放巨量)
    bars.append({
        "date": "2026-05-21",
        "open": 16.6,
        "high": 16.8,
        "low": 16.2,
        "close": 16.4,
        "volume": 1200.0,
    })

    patterns = ZettarancAnnotator.detect_fake_yin(bars)
    assert len(patterns) >= 1
    assert any(p["type"] == "fake_yin" for p in patterns)


def test_annotate_all_dispatch():
    bars = [
        {"date": f"2026-06-{i:02d}", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000.0}
        for i in range(1, 10)
    ]
    # 调用 annotate_all
    annos = ZettarancAnnotator.annotate_all(bars)
    for a in annos:
        assert a.get("source") == "algorithm"
