"""
蜡烛形态序列的契约。

这一层的存在理由是**不信任**指标库里的 `candles_cdl_*` 列：实测 61 列里有 12 列
被别的计算污染了（触发率 45%~72%、值域几十万种浮点数）。所以两条最关键的断言
是：值域校验必须能揪出污染列；方向必须由**符号**决定。
"""

import unittest

from zettaranc.candles import (
    CANDLE_PATTERNS,
    CANDLE_KEYS,
    COLUMN_MAP,
    build_candle_sql,
    detect_candle_series,
    summarize_candles,
    validate_indicator_columns,
)


class TestCandleTable(unittest.TestCase):

    def test_每项都有_key_列名_中文名_说明(self):
        for p in CANDLE_PATTERNS:
            self.assertTrue(p["key"].startswith("cdl_"), p)
            self.assertTrue(p["column"].startswith("candles_cdl"), p)
            self.assertTrue(p["name"], p)
            self.assertTrue(p["desc"], p)

    def test_key_不重复且列名不重复(self):
        self.assertEqual(len(CANDLE_KEYS), len(set(CANDLE_KEYS)))
        cols = [p["column"] for p in CANDLE_PATTERNS]
        self.assertEqual(len(cols), len(set(cols)))

    def test_说明只讲形状_不断言方向(self):
        """符号是 TA-Lib 结合前序趋势给的，可能和形态名的直觉相反。

        同一个 cdl_invertedhammer 在低位是 +100、在高位是 -100。说明里写死
        "看涨"就会和方向字段打架 —— 用户同时看到两条互相矛盾的信息。
        """
        for p in CANDLE_PATTERNS:
            for banned in ("看涨", "看跌", "底部", "顶部"):
                self.assertNotIn(banned, p["desc"], f"{p['name']} 的说明断言了方向: {p['desc']}")

    def test_sql_用绑定参数且不含用户输入(self):
        sql = build_candle_sql()
        self.assertIn("thscode = ?", sql)
        self.assertIn("v_indicators_daily", sql)
        self.assertIn("LIMIT ?", sql)
        for key in CANDLE_KEYS:
            self.assertIn(f"AS {key}", sql)


class TestValidateIndicatorColumns(unittest.TestCase):

    def test_正常值域通过(self):
        rows = [{"cdl_hammer": 0}, {"cdl_hammer": 100}, {"cdl_hammer": -100}, {"cdl_hammer": None}]
        self.assertEqual(validate_indicator_columns(rows), [])

    def test_揪出被污染的列(self):
        """真实的坏列长这样：cdl_3outside 的值是 6.67 / 33.33 / 93.33 这种。"""
        rows = [
            {"cdl_hammer": 0, "cdl_doji": 6.666666666666667},
            {"cdl_hammer": 100, "cdl_doji": 33.33333333333333},
        ]
        bad = validate_indicator_columns(rows)
        self.assertEqual(len(bad), 1)
        self.assertIn("cdl_doji", bad[0])

    def test_允许_80_这种较弱强度(self):
        rows = [{"cdl_engulfing": 80}, {"cdl_engulfing": -80}]
        self.assertEqual(validate_indicator_columns(rows), [])

    def test_非数值也算异常(self):
        rows = [{"cdl_doji": "abc"}]
        self.assertEqual(len(validate_indicator_columns(rows)), 1)


class TestDetectCandleSeries(unittest.TestCase):

    def test_方向由符号决定(self):
        rows = [
            {"date": "2026-01-02", "cdl_engulfing": 100},
            {"date": "2026-01-03", "cdl_engulfing": -100},
            {"date": "2026-01-04", "cdl_doji": 80},
            {"date": "2026-01-05", "cdl_doji": -200},
        ]
        out = detect_candle_series(rows)
        self.assertEqual([i["direction"] for i in out], ["bullish", "bearish", "bullish", "bearish"])
        self.assertEqual([i["strength"] for i in out], [100, 100, 80, 200])

    def test_零与缺失都不产出信号(self):
        rows = [{"date": "2026-01-02", "cdl_doji": 0, "cdl_hammer": None}]
        self.assertEqual(detect_candle_series(rows), [])

    def test_同一天命中多个形态全部保留(self):
        rows = [{"date": "2026-01-02", "cdl_doji": 100, "cdl_hammer": 100}]
        out = detect_candle_series(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual({i["type"] for i in out}, {"cdl_doji", "cdl_hammer"})

    def test_输出按日期升序(self):
        rows = [
            {"date": "2026-01-03", "cdl_doji": 100},
            {"date": "2026-01-01", "cdl_doji": 100},
            {"date": "2026-01-02", "cdl_doji": 100},
        ]
        self.assertEqual([i["date"] for i in detect_candle_series(rows)],
                         ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_days_按日期截尾而不是按条数(self):
        """同一天可能有多个形态，按条数截会把最后一天的形态切掉一半。"""
        rows = [
            {"date": "2026-01-01", "cdl_doji": 100},
            {"date": "2026-01-02", "cdl_doji": 100, "cdl_hammer": 100},
            {"date": "2026-01-03", "cdl_doji": 100, "cdl_hammer": 100},
        ]
        out = detect_candle_series(rows, days=2)
        self.assertEqual({i["date"] for i in out}, {"2026-01-02", "2026-01-03"})
        self.assertEqual(len(out), 4, "最后一天的两个形态都要在")

    def test_缺_date_的行跳过(self):
        self.assertEqual(detect_candle_series([{"cdl_doji": 100}]), [])

    def test_名与说明来自精选表(self):
        out = detect_candle_series([{"date": "2026-01-02", "cdl_hammer": 100}])
        self.assertEqual(out[0]["name"], "锤子线")
        self.assertTrue(out[0]["desc"])


class TestSummarize(unittest.TestCase):

    def test_聚合按名字计数并给出最新一天(self):
        series = [
            {"date": "2026-01-01", "name": "十字星", "direction": "bullish"},
            {"date": "2026-01-02", "name": "十字星", "direction": "bullish"},
            {"date": "2026-01-02", "name": "锤子线", "direction": "bullish"},
        ]
        s = summarize_candles(series)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_name"], {"十字星": 2, "锤子线": 1})
        self.assertEqual(s["latest_date"], "2026-01-02")
        self.assertEqual(len(s["latest"]), 2)

    def test_空序列不炸(self):
        s = summarize_candles([])
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["latest_date"])


if __name__ == "__main__":
    unittest.main()
