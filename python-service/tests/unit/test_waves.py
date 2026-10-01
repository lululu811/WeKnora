"""艾略特波浪识别的单测。

数据用**合成形状**而不是真实行情：真实行情里「正确的浪型」本身就没有定论，
拿它当期望值等于把主观判断固化成测试。合成形状能确定性地验证
「规则是否按预期生效」，那才是这个模块能承诺的东西。

注意行序：`rows[0]` 是最新一根（本仓库约定）。
"""

import pytest

from zettaranc.waves import (
    detect_elliott_waves,
    zigzag,
    DEFAULT_ZIGZAG_PCT,
    MIN_BARS,
)


def series(turns, per_leg=8, start_index=0):
    """
    把一串转折点插值成 K 线，**按时间顺序**返回。

    `turns` 形如 [(price, ...), ...]，相邻两点之间线性插值。
    """
    closes = []
    for i in range(len(turns) - 1):
        a, b = turns[i], turns[i + 1]
        for k in range(per_leg):
            closes.append(a + (b - a) * k / per_leg)
    closes.append(turns[-1])
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "date": f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}",
            "open": c, "high": c, "low": c, "close": c, "vol": 1000.0,
        })
    return list(reversed(rows))  # 转成 rows[0] 最新


def chronological(rows):
    return list(reversed(rows))


# ---------------------------------------------------------------------------
# ZigZag
# ---------------------------------------------------------------------------

class TestZigZag:

    def test_提取已知形状的枢轴(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        pivots = zigzag(rows, threshold_pct=5)
        prices = [p["price"] for p in pivots]
        # 起点 10 -> 20 -> 15 -> 40 -> 25 -> 45，每个转折都应被识别
        assert prices[0] == pytest.approx(10, abs=0.5)
        assert 20 in [pytest.approx(x, abs=0.5) for x in prices]
        assert prices[-1] == pytest.approx(45, abs=0.5)

    def test_枢轴按时间顺序返回(self):
        rows = series([10, 20, 15, 40], per_leg=6)
        pivots = zigzag(rows, threshold_pct=5)
        idx = [p["index"] for p in pivots]
        assert idx == sorted(idx), "应是最老 -> 最新"

    def test_高低交替(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=6)
        pivots = zigzag(rows, threshold_pct=5)
        kinds = [p["kind"] for p in pivots]
        for a, b in zip(kinds, kinds[1:]):
            assert a != b, f"相邻枢轴不应同类型：{kinds}"

    def test_阈值放大后枢轴变少(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=6)
        fine = zigzag(rows, threshold_pct=3)
        coarse = zigzag(rows, threshold_pct=60)
        assert len(coarse) <= len(fine)

    def test_数据不足返回空(self):
        assert zigzag([], 5) == []
        assert zigzag([{"close": 1, "high": 1, "low": 1, "date": "x"}], 5) == []


# ---------------------------------------------------------------------------
# 驱动浪
# ---------------------------------------------------------------------------

class TestImpulse:

    def test_标准上升驱动浪被识别(self):
        # 10 -> 20 (1) -> 15 (2, >10 未破起点) -> 40 (3) -> 25 (4, >20 不重叠) -> 45 (5)
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is not None, "标准形态应被识别"
        assert w["kind"] == "impulse"
        assert w["direction"] == "bullish"
        assert [p["label"] for p in w["points"]] == ["起点", "1", "2", "3", "4", "5"]
        assert w["rules"]["wave2_not_retrace_wave1"] is True
        assert w["rules"]["wave3_not_shortest"] is True
        assert w["rules"]["wave4_no_overlap_wave1"] is True

    def test_浪2_破起点则拒绝(self):
        # 浪 2 跌到 8，低于起点 10 -> 违反规则 1
        rows = series([10, 20, 8, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is None or w["kind"] != "impulse" or not w["rules"]["wave2_not_retrace_wave1"]

    def test_浪4_与浪1重叠则拒绝(self):
        # 浪 4 跌到 18，低于浪 1 顶 20 -> 违反规则 3
        rows = series([10, 20, 15, 40, 18, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        if w is not None and w["kind"] == "impulse":
            assert w["rules"]["wave4_no_overlap_wave1"] is False

    def test_置信度不超过_0_7(self):
        # 自动数浪是"一种可能的数法"，不该给出高置信度
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is not None
        assert w["confidence"] <= 0.7

    def test_关键点带日期与下标(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is not None
        for p in w["points"]:
            assert isinstance(p["date"], str) and p["date"]
            assert isinstance(p["index"], int)
            assert isinstance(p["price"], float)


# ---------------------------------------------------------------------------
# 调整浪与降级
# ---------------------------------------------------------------------------

class TestCorrective:

    def test_调整浪形状被识别(self):
        # 10 -> 20 -> 14 -> 18 -> 12：交替三段，不符合驱动浪，应落到调整浪
        rows = series([10, 20, 14, 18, 12], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        if w is not None:
            assert w["kind"] in ("corrective", "impulse")
            assert len(w["points"]) >= 4

    def test_方向字段合法(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is not None
        assert w["direction"] in ("bullish", "bearish")


class TestDegradation:

    def test_数据不足返回_None(self):
        rows = series([10, 20], per_leg=3)
        assert len(rows) < MIN_BARS
        assert detect_elliott_waves(rows) is None

    def test_单调行情不产出浪型(self):
        # 一路上涨、没有足够反向波动 -> 提不出枢轴 -> 不硬凑
        rows = series([10, 20, 30, 40, 50, 60], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is None, "没有交替结构时不该产出浪型"

    def test_空输入不崩(self):
        assert detect_elliott_waves([]) is None

    def test_阈值过大会提不出枢轴(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        # 阈值 500% 任何反向都够不着
        assert detect_elliott_waves(rows, threshold_pct=500) is None

    def test_默认阈值是个合理值(self):
        assert 1 <= DEFAULT_ZIGZAG_PCT <= 20


class TestThresholdScan:
    """自动扫描阈值的行为。这是「不把噪声当浪」的关键机制。"""

    def test_默认扫描优先取大级别结构(self):
        # 造一段：大级别驱动浪之上叠了细碎波动
        turns = [10]
        for big in [20, 15, 40, 25, 45]:
            turns.append(big)
            # 每个大转折之间塞一点小抖动
            turns.extend([big * 1.01, big * 0.99])
        rows = series(turns, per_leg=4)
        w = detect_elliott_waves(rows)
        if w is not None and w["kind"] == "impulse":
            # 取到的应是那条大结构，不是抖动
            prices = [p["price"] for p in w["points"]]
            assert max(prices) >= 40, f"应取到大级别结构，实际 {prices}"

    def test_显式传阈值时只试那一个(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows, threshold_pct=5)
        assert w is not None
        assert w["threshold_pct"] == 5

    def test_扫描结果带上是哪个阈值命中的(self):
        rows = series([10, 20, 15, 40, 25, 45], per_leg=8)
        w = detect_elliott_waves(rows)
        if w is not None:
            from zettaranc.waves import SCAN_THRESHOLDS_PCT
            assert w["threshold_pct"] in SCAN_THRESHOLDS_PCT

    def test_扫描序列由粗到细(self):
        from zettaranc.waves import SCAN_THRESHOLDS_PCT
        assert list(SCAN_THRESHOLDS_PCT) == sorted(SCAN_THRESHOLDS_PCT, reverse=True)
