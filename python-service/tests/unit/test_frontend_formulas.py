"""
frontend_formulas.py 的性质测试。

这些函数是工作台 TypeScript 实现的**逐字移植**，评估管线全靠它们算
MACD / KDJ 触发（因为实测库里的 momentum_macd_12_26_9_* 与工作台 MACD 的
跨柱位置只重合 60.7% —— 见模块头部）。一旦移植与 TS 漂移，评估算的就是
另一个信号，而报告读起来完全看不出来。

所以这里锁的不是"算得对不对"（那要跟真实 DuckDB fixture 比），而是
**移植必须保持的若干性质** —— 这些性质正是 TS 源码里那些容易被漏掉的
逐字细节。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from zettaranc.frontend_formulas import (  # noqa: E402
    FORMULAS, bbi, dema, ema, kdj, long_bbi, macd, r2, sma,
)


class TestRounding:
    def test_half_away_from_zero_not_bankers(self):
        """JS toFixed 是 half-away-from-zero，Python round() 是 banker's。"""
        # 0.125: JS 给 0.13；Python round(0.125, 2) 给 0.12（因为 2 是偶数）
        assert r2(0.125) == 0.13
        assert r2(0.135) == 0.14
        assert r2(-0.125) == -0.13
        assert round(0.125, 2) == 0.12, \
            "如果哪天 Python 的 round 变了，这条会提醒我们重新审视"

    def test_none_and_nan_pass_through(self):
        assert r2(None) is None
        assert r2(float("nan")) is None
        assert r2(float("inf")) is None


class TestEMAFamily:
    def test_ema_seeds_on_first_value(self):
        """indicators.ts:63 —— 第一个有效值直接作种子，不做前置平滑。"""
        out = ema([10.0] * 5, 3)
        assert out == [10.0] * 5

    def test_ema_skips_non_finite(self):
        out = ema([10.0, None, 12.0], 3)
        assert out[1] is None
        assert out[2] is not None

    def test_sma_is_rolling_and_matches_naive(self):
        closes = [float(i) for i in range(1, 21)]
        out = sma(closes, 5)
        assert out[:4] == [None] * 4
        assert out[4] == pytest.approx(3.0)
        assert out[19] == pytest.approx(18.0)
        # 与"每次重算窗口"逐点一致 —— 证明滚动和没写错
        for i in range(4, 20):
            assert out[i] == pytest.approx(sum(closes[i - 4:i + 1]) / 5, abs=1e-9)

    def test_dema_inner_feeds_rounded_values(self):
        """
        逐字细节：内层 EMA 每根就 toFixed(2)，第二遍吃的是**截断后**的值。

        与"全程双精度"的差异会随 bar 数放大 —— 这正是库侧（后端）与
        前端唯一的真实差别，也是跨栈断言必须用容差的原因。
        """
        import random
        rnd = random.Random(5)
        closes = [rnd.uniform(5, 50) for _ in range(200)]
        rounded = dema(closes, 10)

        k = 2 / 11
        inner = ema(closes, 10)            # 已 toFixed(2)
        e = None
        full = []
        for v in inner:
            if v is None:
                full.append(None)
                continue
            e = v if e is None else v * k + e * (1 - k)
            full.append(r2(e))
        assert rounded == full

        # 对照：真·全精度 DEMA —— 先算**不取整**的内层 EMA 序列，再对内层
        # 序列做外层 EMA。（第一版这里写成了"对收盘价跑两遍 EMA"，那不是
        # DEMA，差出 6.8 会被误读成实现有 bug。）
        kk = 2 / 11
        e = closes[0]
        inner_raw = []
        for c in closes:
            e = c * kk + e * (1 - kk)
            inner_raw.append(e)
        e2 = inner_raw[0]
        raw = []
        for v in inner_raw:
            e2 = v * kk + e2 * (1 - kk)
            raw.append(r2(e2))
        diff = max(abs(a - b) for a, b in zip(rounded, raw))
        assert 0 < diff < 0.011, \
            f"取整纪律差异应当非零且在约一个显示单位内，实际 {diff}"


class TestBBI:
    def test_long_bbi_degrades_but_plain_bbi_does_not(self):
        """
        一处**有意**的语义分叉，两边行为相反，不能抹平：

        calcLongBBI（黄线）数据不足时降级平均；
        calcBBI（牵牛绳）任一条均线没成形就返回 null。
        """
        closes = [float(i) for i in range(1, 30)]      # 29 根 < 114
        yellow = long_bbi(closes, (14, 28, 57, 114))
        rope = bbi(closes, (3, 6, 12, 24))
        # 最长周期是 MA14，所以降级最早也只能从第 13 根（第 14 根）开始：
        # 在那之前四条均线全为 null，"用已有均线求均"没有东西可均。
        assert yellow[:13] == [None] * 13, "四条均线都没成形时应为 null"
        assert yellow[13] is not None, "MA14 一成形黄线就该有值（降级平均）"
        # 牵牛绳相反：必须四条均线全在。MA24 要到第 23 根才成形，
        # 所以在那之前一律 null —— 这正是两者的语义分叉所在。
        assert rope[:23] == [None] * 23, "MA24 成形之前牵牛绳应当一直是 null"
        assert rope[23] is not None
        # 降级只在**它自己的**四条均线没齐时起作用；齐了之后就是那四条的平均。
        # （第一版这里拿 long_bbi 去比 bbi —— 那是 14/28/57/114 对 3/6/12/24，
        #  两个不同的公式，本来就不该相等。）
        long_enough = [float(i) for i in range(1, 200)]
        y = long_bbi(long_enough)
        mas = [sma(long_enough, p) for p in (14, 28, 57, 114)]
        for i in (113, 114, 150, 198):
            expect = r2(sum(m[i] for m in mas) / 4)
            assert y[i] == pytest.approx(expect, abs=1e-9), \
                f"第 {i} 根：四条均线齐全时应退化为简单平均"


class TestMACD:
    def test_first_bar_is_identity_and_dea_equals_dif(self):
        """
        indicators.ts:183-187 —— 循环从 i=0 就递推，所以第 0 根是个恒等变换：
        es==el → dif[0]==0，dea[0]=dif[0]=0。
        """
        out = macd([100.0, 101.0, 102.0])
        assert out["dif"][0] == 0.0
        assert out["dea"][0] == 0.0
        assert out["hist"][0] == 0.0

    def test_hist_is_doubled(self):
        """hist = (dif - dea) * 2 —— 两边的历史约定，不能改成单倍。"""
        closes = [10.0 + (i % 7) - (i % 5) for i in range(60)]
        out = macd(closes)
        for i in range(len(closes)):
            if None in (out["dif"][i], out["dea"][i]):
                continue
            assert out["hist"][i] == pytest.approx(
                (out["dif"][i] - out["dea"][i]) * 2, abs=0.02)

    def test_constant_input_collapses(self):
        """恒定价格下 EMA 都等于该价格，dif 全 0，不会有跨柱。"""
        out = macd([42.0] * 30)
        assert all(abs(d) < 1e-9 for d in out["dif"])

    def test_keys_match_registry(self):
        assert set(macd([1.0, 2.0])) == {"dif", "dea", "hist"}


class TestKDJ:
    def test_seed_is_fifty_not_close(self):
        """indicators.ts:207 —— k / d 的种子都是 50，不是收盘价。"""
        out = kdj([100.0] * 12, [100.0] * 12, [100.0] * 12)
        # high==low → RSV 取 50 → k 停在 50，d 也停在 50，j = 3*50-2*50 = 50
        assert all(abs(v - 50.0) < 1e-9 for v in out["k"])
        assert all(abs(v - 50.0) < 1e-9 for v in out["d"])
        assert all(abs(v - 50.0) < 1e-9 for v in out["j"])

    def test_flat_market_does_not_divide_by_zero(self):
        """high==low 时 RSV 取 50 而不是 0 —— 除零保护也是口径的一部分。"""
        out = kdj([5.0] * 9, [5.0] * 9, [5.0] * 9)
        assert all(v is not None for v in out["k"])

    def test_uses_rolling_window_not_cumulative(self):
        """RSV 用滚动 n 根的最高/最低，不是"上市以来"。"""
        closes = [float(i) for i in range(1, 40)]
        lows = [float(i) - 1 for i in range(1, 40)]
        highs = [float(i) + 1 for i in range(1, 40)]
        out = kdj(closes, lows, highs, n=9)
        # 后段严格递增时，每根新高都应让 k 上升；用累计最高也不会差，
        # 所以改用"先冲高再回落"的序列：累计口径会把旧的极值一直带着。
        closes2 = [10.0] * 20 + [5.0] * 20
        lows2 = [9.0] * 20 + [4.0] * 20
        highs2 = [11.0] * 20 + [6.0] * 20
        out2 = kdj(closes2, lows2, highs2, n=9)
        assert out2["k"][-1] < out2["k"][19], \
            "窗口滚出旧高值后 k 应该回落；累计口径不会回落"

    def test_keys_match_registry(self):
        assert set(kdj([1.0, 2.0], [0.5, 1.5], [1.5, 2.5])) == {"k", "d", "j"}


class TestFormulaRegistry:
    def test_registry_matches_go_allowlist(self):
        """
        Go 侧 internal/indicators/meta.go 的 FrontendFormulaFields 与这里的
        FORMULAS 必须逐条一致。Go 的测试会检查本文件里看得到同名条目，
        这里反向检查一次，两边任一边漏改都会被抓住。
        """
        expect = {
            "dema": {"line"},
            "long_bbi": {"line"},
            "bbi": {"line"},
            "macd": {"dif", "dea", "hist"},
            "kdj": {"k", "d", "j"},
        }
        assert set(FORMULAS) == set(expect), \
            f"公式集合不一致：多 {set(FORMULAS)-set(expect)}，少 {set(expect)-set(FORMULAS)}"
        for name, fields in expect.items():
            assert set(FORMULAS[name]["fields"]) == fields, \
                f"{name} 的字段不一致"

    def test_every_formula_declares_its_needs(self):
        for name, spec in FORMULAS.items():
            assert callable(spec["fn"]), f"{name}.fn 不可调用"
            assert "needs" in spec and "fields" in spec, f"{name} 缺元信息"

    def test_registry_entries_are_callable_with_declared_needs(self):
        """每个公式都能用 FORMULAS 里声明的 needs 真的跑起来。"""
        ohlcv = {
            "close": [10.0 + (i % 5) for i in range(150)],
            "low": [9.0 + (i % 5) for i in range(150)],
            "high": [11.0 + (i % 5) for i in range(150)],
        }
        for name, spec in FORMULAS.items():
            kwargs = {}
            if "low" in spec["needs"]:
                kwargs["lows"] = ohlcv["low"]
            if "high" in spec["needs"]:
                kwargs["highs"] = ohlcv["high"]
            out = spec["fn"](ohlcv["close"], **kwargs)
            if isinstance(out, dict):
                assert set(out) == set(spec["fields"]), \
                    f"{name} 返回的键 {set(out)} 与注册字段 {set(spec['fields'])} 不符"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
