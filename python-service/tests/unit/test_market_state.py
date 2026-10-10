"""
市场状态评估（`zettaranc/market_state.py`）的单元测试。

不打 DuckDB —— 这个模块是纯计算，价值全在**口径与数值行为**，用构造的 K 线
就能覆盖。真正打真库的覆盖放在 `tests/e2e/`。

这些用例锁的是三个真实踩过的 bug，每个 bug 都是"不报错、结果恒定、不易察觉"
的类型，所以专门留了回归断言：

1. `_scale(lo > hi)` 曾让波动维度**恒定 50**，且不抛任何异常
2. `score_volume` 曾用「全市场成交额 ÷ 指数成交额」做分母，量能分恒定中性
3. trend 用 120 日区间位置，在 20 日窗口上均值回归，IC = -0.20
   → 已拆成 trend（长周期）+ short_term_heat（逆向）
"""

import math

import pytest

from zettaranc.market_state import (
    DIMENSION_WEIGHTS,
    NEUTRAL_SCORE,
    REGIME_STRONG,
    REGIME_WEAK,
    _scale,
    classify_regime,
    compute_market_state,
    exposure_from_composite,
    score_breadth,
    score_trend,
    score_trend_extended,
    score_volatility,
    score_volume,
)


# ──────────────────── 构造工具 ────────────────────


def make_rows(closes_asc, turnover=None):
    """
    按 zettaranc 契约造行：**倒序，rows[0] 最新**。

    参数传的是**时间正序**的收盘价序列（closes_asc[0] 最早），
    内部会反转 —— 测试里读起来直观（ramp_up 就是一路涨），但传给模块的
    行序符合契约。踩过这个坑：直接把升序序列当 rows 传，趋势方向整个反过来。
    """
    closes_asc = list(closes_asc)
    n = len(closes_asc)
    turn = list(turnover) if turnover else [1e11] * n
    return [
        {
            "trade_date": f"2026-01-{i:02d}",
            "close": closes_asc[n - 1 - i],
            "turnover": turn[n - 1 - i],
        }
        for i in range(n)
    ]


def ramp_up(n=150, start=3000.0, step=5.0):
    return [start + step * i for i in range(n)]


def ramp_down(n=150, start=3000.0, step=-5.0):
    return [start + step * i for i in range(n)]


def zigzag(n=150, base=3000.0, amp=200.0):
    return [base + amp * math.sin(i / 3.0) for i in range(n)]


# ──────────────────── _scale ────────────────────


class TestScale:
    def test_forward_mapping(self):
        assert _scale(0.5, 0.0, 1.0) == pytest.approx(50.0)
        assert _scale(1.0, 0.0, 1.0) == pytest.approx(100.0)
        assert _scale(0.0, 0.0, 1.0) == pytest.approx(0.0)

    def test_reverse_mapping_lo_greater_than_hi(self):
        """lo > hi 必须做反向映射，而不是把一切截断到同一端。

        回归用例：早期实现写 `if hi <= lo: return NEUTRAL`，导致
        `_scale(annual_vol, 0.35, 0.10)` 恒定返回 50.0，波动维度整个失效
        且无任何报错。
        """
        assert _scale(0.10, 0.35, 0.10) == pytest.approx(100.0)  # 越低越"稳"
        assert _scale(0.35, 0.35, 0.10) == pytest.approx(0.0)
        assert _scale(0.225, 0.35, 0.10) == pytest.approx(50.0)

    def test_degenerate_range_is_neutral(self):
        assert _scale(5.0, 1.0, 1.0) == NEUTRAL_SCORE

    def test_out_of_range_is_clamped(self):
        assert _scale(99.0, 0.0, 1.0) == 100.0
        assert _scale(-99.0, 0.0, 1.0) == 0.0

    def test_reverse_mapping_never_constant(self):
        """反向区间下，至少要能产出区分度，否则说明实现退化了。"""
        vals = [_scale(v, 0.35, 0.10) for v in (0.10, 0.20, 0.30, 0.40)]
        assert len(set(vals)) == 4


# ──────────────────── 各维度 ────────────────────


class TestTrend:
    def test_uptrend_scores_high(self):
        assert score_trend(make_rows(ramp_up()))["score"] > 60

    def test_downtrend_scores_low(self):
        assert score_trend(make_rows(ramp_down()))["score"] < 40

    def test_short_series_is_neutral_not_crash(self):
        assert score_trend(make_rows([3000.0, 3010.0, 3020.0]))["score"] == NEUTRAL_SCORE

    def test_constant_price_is_neutral(self):
        assert score_trend(make_rows([3000.0] * 150))["score"] == NEUTRAL_SCORE


class TestVolatility:
    def test_calm_market_scores_stable_high(self):
        calm = [3000.0 + (i % 3) for i in range(150)]
        assert score_volatility(make_rows(calm))["score"] > 60

    def test_wild_market_scores_unstable_low(self):
        wild = [3000.0 * (1 + 0.05 * math.sin(i)) for i in range(150)]
        assert score_volatility(make_rows(wild))["score"] < 40

    def test_score_actually_varies(self):
        """回归用例：这个维度曾恒定 50。跨不同波动必须产出不同分数。"""
        calm = [3000.0 + (i % 3) for i in range(150)]
        wild = [3000.0 * (1 + 0.05 * math.sin(i)) for i in range(150)]
        assert score_volatility(make_rows(calm))["score"] != score_volatility(make_rows(wild))["score"]

    def test_flat_market_reports_zero_vol(self):
        r = score_volatility(make_rows([3000.0] * 150))
        assert r["annual_vol"] == pytest.approx(0.0)


class TestBreadth:
    def test_strong_breadth_scores_high(self):
        b = {"pct_above_ma60": 0.85, "pct_new_high_120": 0.2}
        assert score_breadth(b)["score"] > 60

    def test_weak_breadth_scores_low(self):
        b = {"pct_above_ma60": 0.15, "pct_new_high_120": 0.01}
        assert score_breadth(b)["score"] < 40

    def test_missing_breadth_is_neutral(self):
        assert score_breadth(None)["score"] == NEUTRAL_SCORE
        assert score_breadth({})["score"] == NEUTRAL_SCORE

    def test_missing_column_is_neutral(self):
        assert score_breadth({"pct_new_high_120": 0.1})["score"] == NEUTRAL_SCORE


class TestVolume:
    def test_uses_same_units_for_numerator_and_denominator(self):
        """
        回归用例：早期实现分子用全市场 total_turnover、分母用指数 turnover，
        两者差三个数量级，量能分恒定中性。现在分子分母必须同为全市场口径。
        """
        rows = make_rows(ramp_up(150), turnover=[1e11] * 150)  # 指数成交额（陷阱值）
        hist = [{"total_turnover": 1e12} for _ in range(60)]  # 全市场均额
        today = {"total_turnover": 2.0e12, "pct_above_ma60": 0.5}  # 2 倍放量

        r = score_volume(rows, today, hist)
        assert r["ratio"] == pytest.approx(2.0, rel=0.01)
        assert r["score"] == 100.0  # 2 倍远超 1.4 上限 → 满分
        assert r["source"] == "全市场"

    def test_volume_shrinks_scores_low(self):
        rows = make_rows(ramp_up(150))
        hist = [{"total_turnover": 2e12} for _ in range(60)]
        today = {"total_turnover": 1.0e12}  # 只有均额一半
        r = score_volume(rows, today, hist)
        assert r["ratio"] == pytest.approx(0.5, rel=0.01)
        assert r["score"] == 0.0

    def test_volume_varies_with_ratio(self):
        """回归用例：曾恒定 50。不同倍数必须给出不同分数。"""
        rows = make_rows(ramp_up(150))
        hist = [{"total_turnover": 1e12} for _ in range(60)]
        low = score_volume(rows, {"total_turnover": 0.8e12}, hist)
        high = score_volume(rows, {"total_turnover": 1.3e12}, hist)
        assert low["score"] != high["score"]

    def test_fallback_uses_index_and_flags_it(self):
        rows = make_rows(ramp_up(150), turnover=[1e11] * 150)
        r = score_volume(rows, {"total_turnover": 2e12}, None)
        assert r["source"].startswith("指数")
        assert "口径不一致" in r["source"]

    def test_missing_data_is_neutral(self):
        assert score_volume(make_rows(ramp_up(150)), None)["score"] == NEUTRAL_SCORE
        assert score_volume(make_rows(ramp_up(150)), {})["score"] == NEUTRAL_SCORE


class TestShortTermHeat:
    def test_is_inverted_hot_market_scores_low(self):
        """短期越热 → 分数越低（逆向使用）。"""
        hot = ramp_up(150, step=15.0)  # 快速拉升 = 短期过热
        cold = ramp_down(150, step=-2.0)
        assert score_trend_extended(make_rows(hot))["score"] < score_trend_extended(make_rows(cold))["score"]

    def test_marks_that_it_is_negated(self):
        r = score_trend_extended(make_rows(ramp_up(150, step=15.0)))
        assert "取负号" in r["reason"]

    def test_uses_breadth_when_available(self):
        rows = make_rows(ramp_up(150, step=3.0))
        without = score_trend_extended(rows)
        with_width = score_trend_extended(rows, {"pct_above_ma20": 0.9})
        assert with_width["heat"] != without["heat"]
        assert "指数+宽度" in with_width["reason"]
        assert "仅指数" in without["reason"]

    def test_short_series_is_neutral(self):
        assert score_trend_extended(make_rows([3000.0, 3010.0, 3020.0]))["score"] == NEUTRAL_SCORE


class TestClassifyRegime:
    def test_thresholds(self):
        assert classify_regime(85.0) == "strong"
        assert classify_regime(REGIME_STRONG) == "strong"
        assert classify_regime(50.0) == "neutral"
        assert classify_regime(REGIME_WEAK) == "weak"
        assert classify_regime(10.0) == "weak"

    def test_boundaries_are_inclusive(self):
        assert classify_regime(REGIME_STRONG - 0.01) == "neutral"
        assert classify_regime(REGIME_WEAK + 0.01) == "neutral"


class TestWeights:
    def test_weights_sum_to_one(self):
        assert sum(DIMENSION_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_all_weights_non_negative(self):
        assert all(w >= 0 for w in DIMENSION_WEIGHTS.values())

    def test_volatility_is_zero_after_oos_flip(self):
        """
        波动维度逐年 IC 为 +0.393/+0.050/+0.012/-0.107/-0.192，方向最不稳定，
        权重归零。这条断言锁住回测结论，改权重必须先重跑回测。
        """
        assert DIMENSION_WEIGHTS["volatility"] == 0.0

    def test_short_term_heat_has_highest_weight(self):
        """唯一跨窗口/跨年份方向稳定的维度，权重必须最高。"""
        assert DIMENSION_WEIGHTS["short_term_heat"] == max(DIMENSION_WEIGHTS.values())


# ──────────────────── 不可交易契约 ────────────────────


class TestExposure:
    """
    暴露度参考（不是方向信号）。

    回测：`scripts/backtest_adaptive.py` —— 基准 +1.13%/回撤 -29.73%，
    方向自适应 +9.47%/回撤 -16.96%；但 2024 年把 +12.82% 压到 +0.01%。
    所以它只配叫"暴露参考"。
    """

    def test_exposure_hint_present_with_three_modes(self):
        s = compute_market_state(make_rows(ramp_up(150)), None, None)
        hint = s["exposure_hint"]
        for k in ("neutral", "follow", "contrarian"):
            assert hint[k] in (0.0, 0.5, 1.0)

    def test_exposure_note_states_the_2024_tradeoff(self):
        s = compute_market_state(make_rows(ramp_up(150)), None, None)
        note = s["exposure_hint"]["note"]
        assert "非方向信号" in note
        assert "2024" in note  # 必须写明牛市跑输这个代价

    def test_follow_and_contrarian_are_mirrored(self):
        assert exposure_from_composite(80.0, "follow") == 1.0
        assert exposure_from_composite(80.0, "contrarian") == 0.0
        assert exposure_from_composite(20.0, "follow") == 0.0
        assert exposure_from_composite(20.0, "contrarian") == 1.0

    def test_neutral_is_always_half(self):
        for score in (0.0, 25.0, 50.0, 75.0, 100.0):
            assert exposure_from_composite(score, "neutral") == 0.5

    def test_mid_band_is_half_in_both_directions(self):
        assert exposure_from_composite(50.0, "follow") == 0.5
        assert exposure_from_composite(50.0, "contrarian") == 0.5

    def test_exposure_never_negative_or_over_one(self):
        for score in (0.0, 20.0, 40.0, 60.0, 80.0, 100.0):
            for d in ("follow", "contrarian", "neutral"):
                e = exposure_from_composite(score, d)
                assert 0.0 <= e <= 1.0


class TestNotTradable:
    """
    回测判定 composite 为随机游走，模块必须在返回值里自曝这一点。

    逐年 IC：-0.518 / +0.072 / -0.221 / +0.327 / -0.149（翻转 4 次）
    动态择时全区间 -7.77% vs 基准 +2.83%
    """

    def test_tradable_is_false(self):
        s = compute_market_state(make_rows(ramp_up(150)), None, None)
        assert s["tradable"] is False

    def test_tradable_note_explains_why(self):
        s = compute_market_state(make_rows(ramp_up(150)), None, None)
        note = s["tradable_note"]
        assert "反号" in note
        assert "方向信号" in note  # 不能作买/卖方向信号
        assert "2025" in note or "逐年" in note

    def test_short_term_signal_present(self):
        s = compute_market_state(make_rows(ramp_up(150)), None, None)
        sig = s["short_term_signal"]
        assert 0.0 <= sig["heat_score"] <= 100.0
        assert sig["regime"] in {"cold", "hot", "neutral"}
        assert sig["confidence"] == "weak"

    def test_short_term_signal_is_inverted_score(self):
        """
        heat_score 是**反向分**：高=冷、低=热。

        踩过的坑：曾把 score 直接当"热度"用，指数 20 日跌 5.5% 的冷市
        被输出成"偏热，短期回落风险大"，结论完全反了。所以 regime 用
        cold/hot 而不是 strong/weak，且额外输出 raw_heat 便于核对方向。
        """
        # 冷市：缓慢下跌
        cold = compute_market_state(make_rows(ramp_down(150, step=-1.0)), {"pct_above_ma20": 0.1}, None)
        assert cold["short_term_signal"]["regime"] == "cold"
        assert "偏冷" in cold["short_term_signal"]["interpretation"]
        assert cold["short_term_signal"]["raw_heat"] < 0.5

        # 热市：快速拉升
        hot = compute_market_state(make_rows(ramp_up(150, step=15.0)), {"pct_above_ma20": 0.95}, None)
        assert hot["short_term_signal"]["regime"] == "hot"
        assert "偏热" in hot["short_term_signal"]["interpretation"]
        assert hot["short_term_signal"]["raw_heat"] > 0.5

    def test_heat_score_and_raw_heat_are_opposite(self):
        s = compute_market_state(make_rows(ramp_up(150, step=10.0)), {"pct_above_ma20": 0.8}, None)
        sig = s["short_term_signal"]
        assert sig["heat_score"] + sig["raw_heat"] * 100 == pytest.approx(100.0, abs=0.01)

    def test_hot_market_heatmap_signal_is_low(self):
        """热度分方向：越热越低（与 composite 的混合逻辑无关）。"""
        hot = compute_market_state(make_rows(ramp_up(150, step=15.0)), {"pct_above_ma20": 0.95}, None)
        cold = compute_market_state(make_rows(ramp_down(150, step=-2.0)), {"pct_above_ma20": 0.1}, None)
        assert hot["short_term_signal"]["heat_score"] < cold["short_term_signal"]["heat_score"]


# ──────────────────── 综合 ────────────────────


class TestComputeMarketState:
    def _state(self, closes, breadth=None, hist=None):
        return compute_market_state(make_rows(closes), breadth, hist)

    def test_composite_within_bounds(self):
        s = self._state(ramp_up(150), {"pct_above_ma60": 0.85, "pct_above_ma20": 0.9, "total_turnover": 1e12}, [{"total_turnover": 1e12}] * 60)
        assert 0.0 <= s["composite"] <= 100.0

    def test_all_dimensions_present_with_weight(self):
        s = self._state(ramp_up(150))
        assert set(s["dimensions"]) == set(DIMENSION_WEIGHTS)
        for dim in s["dimensions"].values():
            assert "weight" in dim
            assert "score" in dim

    def test_missing_breadth_does_not_crash(self):
        s = self._state(ramp_up(150), None, None)
        assert 0.0 <= s["composite"] <= 100.0
        assert s["dimensions"]["breadth"]["score"] == NEUTRAL_SCORE

    def test_no_dimension_is_constant_across_regimes(self):
        """
        回归用例：volume / volatility 曾恒定 50，被 composite 静默吸收。
        强牛与强熊两段的分必须有区分度。
        """
        bull = self._state(ramp_up(150), {"pct_above_ma60": 0.85, "pct_above_ma20": 0.92, "total_turnover": 1.5e12}, [{"total_turnover": 1e12}] * 60)
        bear = self._state(ramp_down(150), {"pct_above_ma60": 0.12, "pct_above_ma20": 0.15, "total_turnover": 0.6e12}, [{"total_turnover": 1e12}] * 60)
        for name in DIMENSION_WEIGHTS:
            if DIMENSION_WEIGHTS[name] == 0:
                continue  # 权重归零的维度不影响 composite，但本身仍应有区分度
            assert bull["dimensions"][name]["score"] != bear["dimensions"][name]["score"], name

    def test_volatility_dimension_is_negated(self):
        s = self._state(ramp_up(150))
        v = s["dimensions"]["volatility"]
        assert v["score"] + v["raw_stability_score"] == pytest.approx(100.0, abs=0.01)

    def test_regime_field_present(self):
        s = self._state(ramp_up(150))
        assert s["regime"] in {"strong", "neutral", "weak"}

    def test_trend_and_short_term_heat_can_pull_opposite_ways(self):
        """
        强趋势市里，trend（顺势）和 short_term_heat（逆向）必然打架。

        短期热度分单独看是"越热越低"，但 composite 是五维加权 —— 强趋势下
        trend 拿满分，热度的逆向拉力未必盖得住。回测全区间 composite IC 为
        负、样本外转正，根源就是这两个方向的权重配比。

        所以这里**不**断言"热市 composite 一定更低"（那是对框架的过强假设），
        只锁定两个可观测事实：热度分本身方向正确，且 composite 会因权重而异。
        """
        hot_rows = ramp_up(150, step=15.0)
        cold_rows = ramp_down(150, step=-2.0)

        # 热度分本身：热市 < 冷市（方向正确，与 composite 无关）
        assert score_trend_extended(make_rows(hot_rows))["score"] < score_trend_extended(make_rows(cold_rows))["score"]

        # composite 会因维度权重不同而不同 —— 这里只锁定"权重影响结果"
        hot_state = self._state(hot_rows, {"pct_above_ma60": 0.9, "pct_above_ma20": 0.95, "total_turnover": 1.6e12}, [{"total_turnover": 1e12}] * 60)
        cold_state = self._state(cold_rows, {"pct_above_ma60": 0.1, "pct_above_ma20": 0.1, "total_turnover": 0.6e12}, [{"total_turnover": 1e12}] * 60)
        assert hot_state["composite"] != cold_state["composite"]

    def test_weight_sum_guard_raises(self, monkeypatch):
        """权重和不为 1 必须报错，不能静默算出一个假的 composite。"""
        import zettaranc.market_state as ms

        monkeypatch.setattr(ms, "DIMENSION_WEIGHTS", {"trend": 0.5, "breadth": 0.2})
        with pytest.raises(ValueError, match="权重"):
            compute_market_state(make_rows(ramp_up(150)), None, None)
