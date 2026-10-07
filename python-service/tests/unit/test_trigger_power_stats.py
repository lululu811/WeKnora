"""
触发力评估脚本里最容易算错、且错了**看不出来**的那部分。

2026-10-07 这段代码返工过三次，每次都是同一条根因的不同表现：
把「按触发加权的总均值」和「按日等权的簇均值」当成同一个量用。

  1. 点估计 +0.33%，区间 [-0.04, +0.28] —— 区间不含自己的点估计
  2. 修成区间围绕日频簇均值后仍然对不上同一个 mean
  3. 聚类稳健函数忽略传入变量、永远返回收益率均值，于是"胜率"那行
     显示的是 +0.30% 而不是 46.95%

每一次都产出了一张看起来极其确定的表。所以这里用**可手算的构造数据**把
四条性质钉死：点估计口径、点估计落区间内、无相关时退化到朴素区间、
完美正相关时如实报告零宽度。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from scripts.eval_trigger_power import Z95, Stats  # noqa: E402


def _stats(values, cluster_size=1):
    """把 values 按 cluster_size 切成簇喂进去，模拟"同一天的触发共振"。"""
    s = Stats()
    for i, v in enumerate(values):
        s.add(v, date=i // cluster_size)
    return s


class TestClusterRobustEstimand:
    """点估计与区间必须是同一个估计量。"""

    def test_point_estimate_equals_naive_mean(self):
        import random
        rnd = random.Random(7)
        vals = [rnd.gauss(0.01, 0.05) for _ in range(500)]
        s = _stats(vals, cluster_size=10)

        mu, lo, hi, k = s._cluster_robust(vals)
        assert math.isclose(mu, sum(vals) / len(vals), rel_tol=1e-12), \
            "聚类稳健的点估计必须就是朴素均值，不能是日频簇均值的均值"
        assert lo <= mu <= hi, "点估计必须落在自己的区间内"

    def test_weighted_and_unweighted_means_differ_under_clustering(self):
        """
        反向锁定：一旦簇大小不均匀，加权均值与等权簇均值就不相等。

        没有这条，读者会以为"反正两个都差不多"。实际上 2026-10-07 报出的
        正是这个差：大盘共振日触发密集，按日等权会把它们的影响抹掉。
        """
        s = Stats()
        # 第一天 100 个样本全 +0.10，其余天各 1 个样本 -0.01
        for _ in range(100):
            s.add(0.10, date=0)
        for d in range(1, 101):
            s.add(-0.01, date=d)

        weighted = s.mean()
        cluster_means = [sum(v) / len(v) for v in s.clusters.values()]
        unweighted = sum(cluster_means) / len(cluster_means)
        assert abs(weighted - unweighted) > 0.005, \
            "构造数据本该让两种均值明显不同，否则这条断言没有鉴别力"


class TestClusterRobustDegeneracy:
    def test_reduces_to_naive_when_observations_are_independent(self):
        """每根 bar 自成一簇（无跨股票共振）时，区间应 ≈ 朴素正态区间。"""
        import random
        rnd = random.Random(11)
        vals = [rnd.gauss(0.0, 0.02) for _ in range(400)]
        s = _stats(vals, cluster_size=1)

        mu, lo, hi, _ = s._cluster_robust(vals)
        n = len(vals)
        # 对照组用 **ddof=0**，和被测函数同一个估计量定义。用 ddof=1 会引入
        # sqrt((n-1)/n) 的系统性差异（这里 1.25e-3），那是自由度口径不同，
        # 不是被测函数的错 —— 放宽容差会把这个真实差别盖掉。
        var = sum((v - mu) ** 2 for v in vals) / n
        se = math.sqrt(var / n)
        # 比**区间宽度**而不是端点：lo 可能恰好接近 0，端点相对误差会爆掉。
        # 必须用模块里的 Z95，不能写 1.96 —— 后者比它大 1.8e-5，写死会得到
        # 一个 1.8e-5 的"偏差"，很容易被误读成浮点累加顺序差异而放宽容差，
        # 于是真的聚类失效也照样绿。
        assert (hi - lo) == pytest.approx(2 * Z95 * se, rel=1e-12)

    def test_collapses_when_whole_cluster_moves_together(self):
        """
        同一天所有样本完全同值时，簇内方差为 0 —— 区间宽度必须是 0。

        这条是 Wilson 做不到的：Wilson 在完全正相关下照样给一个窄区间，
        于是"10 万次触发"看起来像"10 万个独立证据"。聚类稳健如实报告
        "这段期间没有一个样本给出独立信息"。
        """
        s = Stats()
        for d in range(50):
            for _ in range(100):
                s.add(0.05, date=d)

        mu, lo, hi, k = s._cluster_robust(s.rets)
        assert math.isclose(mu, 0.05, rel_tol=1e-12)
        assert hi - lo < 1e-12, \
            f"完美正相关时区间宽度应为 0，实际 {hi - lo}"
        assert k == 50

    def test_correlation_inflates_interval_versus_naive(self):
        """
        有共振时区间必须明显比朴素的均值区间宽 —— 这正是换成它的理由。

        对照组必须是**均值的正态近似区间**，不能拿 Wilson：Wilson 是比例的
        区间，跟均值区间不是一个量纲。第一版测试就是这么写的，结果在拿
        「收益率均值的宽度」比「胜率的比例区间宽度」，量纲错配。
        """
        import random
        rnd = random.Random(3)
        s = Stats()
        for d in range(200):                    # 200 个交易日强共振
            day = rnd.gauss(0.01, 0.03)
            for _ in range(200):
                s.add(day + rnd.gauss(0, 0.001), date=d)

        _, lo, hi, _ = s._cluster_robust(s.rets)
        n = len(s.rets)
        mu = s.mean()
        var = sum((v - mu) ** 2 for v in s.rets) / (n - 1)
        naive_width = 2 * Z95 * math.sqrt(var / n)     # 假设独立
        assert (hi - lo) > 3 * naive_width, \
            f"聚类稳健宽度 {hi - lo:.6f} 应远大于朴素的 {naive_width:.6f}"

    def test_empty_and_single_observation(self):
        assert _stats([])._cluster_robust([])[:3] == (0.0, 0.0, 0.0)
        mu, lo, hi, k = _stats([0.02])._cluster_robust([0.02])
        assert (mu, lo, hi, k) == (0.02, 0.02, 0.02, 1), \
            "单样本也该如实报出它有 1 个簇"


class TestClusterRobustWinRate:
    def test_win_rate_point_estimate_is_the_actual_win_rate(self):
        """
        回归本体：曾经这个函数无论传什么进来都返回收益率均值，
        于是"胜率"那行打印的是 +0.30% 而不是 46.95%。
        """
        data = [0.1, -0.2, 0.3, -0.1, 0.5, -0.05, 0.2]
        s = _stats(data, cluster_size=2)
        indicator = [1.0 if r > 0 else 0.0 for r in data]

        mu, lo, hi, k = s._cluster_robust(indicator)
        assert math.isclose(mu, 4 / 7, rel_tol=1e-12)
        # 聚类稳健区间**不保证落在 [0,1]**：它不像 Wilson 那样做比例截断。
        # 这是为了换相关性调整付出的代价，显示时不该假装区间被截过。
        assert lo <= mu <= hi
        assert k > 0

    def test_all_wins_gives_upper_bound_one(self):
        s = _stats([0.01] * 30, cluster_size=3)
        mu, lo, hi, _ = s._cluster_robust([1.0] * 30)
        assert mu == 1.0 and lo == 1.0 and hi == 1.0, \
            "全部同值 → 簇内方差 0 → 区间宽度 0；曾经这里算出 [0.386, 1.0]，" \
            "因为函数拿收益而不是胜率指示变量去分组"


class TestMedianVersusMean:
    def test_median_stays_negative_when_mean_is_positive(self):
        """
        2026-10-07 的实测形状：金叉超额均值 +0.43%、中位 -0.14%。
        靠均值判断"这个信号有效"会得出相反结论。
        """
        rets = [-0.02] * 90 + [0.5] * 10      # 九成小亏、一成大赚
        s = _stats(rets, cluster_size=5)
        mu, lo, hi, _ = s._cluster_robust(rets)
        assert mu > 0, "构造数据应让均值为正"
        assert s.median() < 0, "中位数应为负 —— 典型一笔交易是亏的"
        # 只锁 median < 0 < mean 这条关系。构造数据按 5 个一簇随机分组后
        # 簇间高度异质，区间含 0 是合理的，不该在这里断言 lo > 0。


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
