"""
指标背离的契约。

这一类的误报门槛极低：只要"价格新高 + 指标走低"就成立，不做约束的话几乎每两根
枢轴都能报一个。所以下面守的主要是**约束**：枢轴间距、指标缺失、以及"平高不算
新高"这几条 —— 少了哪一条，用户看到的就是满屏连线。
"""

import unittest

from zettaranc.divergence import (
    MIN_PIVOT_GAP,
    detect_divergence,
)


def row(date, high, low):
    return {"date": date, "high": high, "low": low}


def ind(date, dif, rsi14):
    return {"date": date, "dif": dif, "rsi14": rsi14}


def build(price_highs, price_lows, difs, rsi=50.0, start=1):
    """按给定的高低点造行；rows 按**倒序**（最新在前）。"""
    n = len(price_highs)
    dates = [f"2026-01-{start + i:02d}" for i in range(n)]
    rows = [row(dates[i], price_highs[i], price_lows[i]) for i in range(n)]
    inds = [ind(dates[i], difs[i], rsi) for i in range(n)]
    # 倒序：rows[0] 最新
    return rows[::-1], inds[::-1]


class TestDivergenceGuards(unittest.TestCase):

    def test_数据不足返回空(self):
        rows = [row(f"2026-01-{i:02d}", 10 + i, 9 + i) for i in range(5)]
        inds = [ind(r["date"], 0.1, 50) for r in rows]
        self.assertEqual(detect_divergence(rows, inds), [])

    def test_指标全缺返回空_不当零用(self):
        """指标缺失时**不能**当 0：那会把"没有数据"读成"指标等于 0"。"""
        highs = [10, 11, 10, 12, 10, 13, 10, 14, 10, 15, 10, 16, 10, 17, 10, 18, 10, 19, 10, 20, 10, 21, 10, 22, 10, 23, 10, 24, 10, 25]
        lows = [h - 1 for h in highs]
        rows, inds = build(highs, lows, [0.0] * len(highs))
        for i in inds:
            i["dif"] = None
            i["rsi14"] = None
        self.assertEqual(detect_divergence(rows, inds), [])

    def test_指标日期对不上时该枢轴被跳过(self):
        highs = [10, 11, 10, 12, 10, 13, 10, 14, 10, 15, 10, 16, 10, 17, 10, 18, 10, 19, 10, 20, 10, 21, 10, 22, 10, 23, 10, 24, 10, 25]
        lows = [h - 1 for h in highs]
        rows, _ = build(highs, lows, [0.0] * len(highs))
        # 指标行日期全部错开
        inds = [ind(f"2027-01-{i:02d}", 1.0, 50.0) for i in range(len(highs))]
        self.assertEqual(detect_divergence(rows, inds), [])


class TestDivergenceDetection(unittest.TestCase):
    """
    造一段**上升的高点 + 下行的指标**，且枢轴间距足够（> MIN_PIVOT_GAP）。
    """

    @staticmethod
    def _zigzag_rising_highs():
        """
        3 个周期、每周期 21 根，尖峰落在周期正中。

        周期必须**明显大于** SWING_WINDOW(8)：否则尖峰在 ±8 窗口里不构成严格
        极值，find_swings 一个枢轴都认不出来，测试会退化成空断言。
        每个周期的高点比上一个高 3，指标则一路下行 -> 顶背离。
        """
        # 周期取**奇数**：t=0.5 才会正好落在格点上，尖峰是单点严格极值。
        # 偶数周期会让尖峰变成两个并列的值，find_swings 的严格判定把两者都排除。
        period, cycles = 21, 3
        highs, lows, difs = [], [], []
        for k in range(cycles):
            trough_h, peak_h = 10 + 3 * k, 12 + 3 * k
            for j in range(period):
                tri = 1 - abs(2 * (j / (period - 1)) - 1)   # 0 -> 1 -> 0
                h = trough_h + (peak_h - trough_h) * tri
                highs.append(h)
                lows.append(h - 1.5)
                difs.append(2.0 - 0.02 * (k * period + j))
        return build(highs, lows, difs)

    def test_价格新高而指标走低判为顶背离(self):
        rows, inds = self._zigzag_rising_highs()
        res = detect_divergence(rows, inds)
        tops = [d for d in res if d["direction"] == "bearish"]
        self.assertTrue(tops, f"应识别出顶背离，实际: {[(d['name'], d['direction']) for d in res]}")
        d = tops[0]
        self.assertEqual(d["type"], "divergence")
        self.assertEqual(len(d["points"]), 2)
        self.assertEqual(len(d["lines"]), 1)
        # 后高 > 前高
        self.assertGreater(d["points"][1]["price"], d["points"][0]["price"])

    def test_每条背离都带可绘制坐标(self):
        rows, inds = self._zigzag_rising_highs()
        for d in detect_divergence(rows, inds):
            for p in d["points"]:
                self.assertTrue(p["date"])
                self.assertIsInstance(p["index"], int)
                self.assertGreater(p["price"], 0)
            for ln in d["lines"]:
                self.assertEqual(len(ln["points"]), 2)

    def test_枢轴间距不足时不报(self):
        """相邻两根的"新高 + 指标走低"遍地都是，那不叫背离。"""
        # 造一串密集的高点（间距 2 根 < MIN_PIVOT_GAP）
        highs, lows, difs = [], [], []
        for i in range(30):
            highs.append(10 + i * 0.1 + (1 if i % 2 == 0 else 0))
            lows.append(10 + i * 0.1 - (1 if i % 2 else 0))
            difs.append(1.0 - i * 0.02)
        rows, inds = build(highs, lows, difs)
        self.assertGreaterEqual(MIN_PIVOT_GAP, 5, "这条测试的前提是间距门槛 >= 5")
        for d in detect_divergence(rows, inds):
            a, b = d["points"]
            self.assertGreaterEqual(abs(a["index"] - b["index"]), MIN_PIVOT_GAP)

    def test_价格与指标同向时不报背离(self):
        highs, lows, difs = [], [], []
        for i in range(30):
            base = 10 + i * 0.5
            phase = i % 8
            up = phase < 4
            highs.append(base + (0.6 if up else -0.1))
            lows.append(base - (0.6 if not up else 0.1))
            difs.append(0.5 + i * 0.05)   # 与价格同向
        rows, inds = build(highs, lows, difs)
        res = detect_divergence(rows, inds)
        self.assertEqual([d for d in res if d["direction"] == "bearish"], [],
                         "同向不该被判为顶背离")


if __name__ == "__main__":
    unittest.main()
