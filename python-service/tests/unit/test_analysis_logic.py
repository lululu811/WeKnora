"""Unit tests for the zettaranc analysis layer and the cache.

These pin the *shape* of the data contract:

* `fetch_market_data` returns rows ordered newest-first (`rows[0]` is the latest
  bar, per its own docstring), so every consumer that compares `rows[0]` against
  `rows[1]` is looking at today vs yesterday.
* `find_swings` walks `i` ascending, so its index list is ascending too. Ascending
  indices over descending rows means **list head = most recent swing**, tail =
  oldest. Getting this backwards silently inverts every trend verdict.
* A missing indicator is `None`, never `0`. `0` is a real RSI reading; `None`
  means "not computed", and must not be read as "oversold".
"""

import asyncio
import pytest
import subprocess
import sys
import time

from conftest import newest_first, zigzag

from datasources.cache import LRUCache, MultiLevelCache, stable_hash
from zettaranc.data_loader import (
    build_indicators_only_sql,
    build_indicators_sql,
    build_market_sql,
)
from zettaranc.pattern import detect_flag, detect_head_and_shoulders, detect_wedge
from zettaranc.signals import detect_signals, summarize_signals
from zettaranc.trend import analyze_dow_structure, analyze_ma_alignment, build_summary
from zettaranc.utils import find_swings


# ----------------------------------------------------------------------
# Green tests: the row/swing ordering contract and known-good detections.
# ----------------------------------------------------------------------

class TestRowOrderingContract:

    def test_find_swings_list_head_is_the_most_recent_swing(self):
        rows = newest_first(
            zigzag([(4, 12.0), (16, 14.0), (28, 16.0)],
                   [(10, 11.0), (22, 13.0), (34, 15.0)])
        )
        highs, _ = find_swings(rows, 5)
        assert highs == sorted(highs)
        prices = [rows[i]["high"] for i in highs]
        assert prices == sorted(prices, reverse=True), (
            "rows[0] is newest, so a smaller index must carry a later (higher, "
            "in this fixture) swing high"
        )

    def test_macd_golden_cross_is_detected_on_the_latest_bar(self):
        rows = newest_first([10.0, 10.0, 10.0])
        rows[0].update(dif=0.5, dea=0.2)   # today: dif above dea
        rows[1].update(dif=0.1, dea=0.3)   # yesterday: dif below dea
        assert "MACD金叉" in [s["name"] for s in detect_signals(rows)]

    def test_oversold_rsi_on_real_data_is_a_buy_signal(self):
        rows = newest_first([10.0, 10.0, 10.0])
        rows[0].update(rsi6=12.0)
        signal = next(s for s in detect_signals(rows) if s["name"] == "RSI6超卖")
        assert signal["signal"] == "bullish"
        assert signal["strength"] > 0

    def test_zero_is_a_real_reading_not_a_missing_value(self):
        """RSI6 = 0 really is oversold; only None means 'not computed'."""
        rows = newest_first([10.0, 10.0, 10.0])
        rows[0].update(rsi6=0.0)
        assert "RSI6超卖" in [s["name"] for s in detect_signals(rows)]


# ----------------------------------------------------------------------
# Fixed defects. These used to fail; they guard against regression.
# ----------------------------------------------------------------------

class TestTrendDirection:

    def test_higher_high_higher_low_is_an_uptrend(self):
        rows = newest_first(
            zigzag([(4, 12.0), (16, 14.0), (28, 16.0)],
                   [(10, 11.0), (22, 13.0), (34, 15.0)])
        )
        direction, desc = analyze_dow_structure(rows)
        assert direction == "uptrend", desc

    def test_lower_high_lower_low_is_a_downtrend(self):
        rows = newest_first(
            zigzag([(10, 20.0), (24, 18.0), (38, 16.0)],
                   [(2, 17.0), (17, 15.0), (31, 13.0), (44, 12.0)], n=48)
        )
        direction, desc = analyze_dow_structure(rows)
        assert direction == "downtrend", desc


class TestMaAlignment:

    def test_price_vs_ma20_is_reported_even_when_ma60_is_missing(self):
        row = {"ma5": 2.74, "ma10": 2.75, "ma20": 2.78,
               "ma60": 0.0, "ma120": 0.0, "ma250": 0.0, "close": 2.71}
        _, price_vs_ma20 = analyze_ma_alignment(row)
        assert price_vs_ma20 == "below"

    def test_price_above_ma20_is_reported(self):
        row = {"ma5": 2.80, "ma10": 2.78, "ma20": 2.78,
               "ma60": 0.0, "ma120": 0.0, "ma250": 0.0, "close": 2.90}
        _, price_vs_ma20 = analyze_ma_alignment(row)
        assert price_vs_ma20 == "above"


class TestTrendSummary:

    def test_neutral_verdict_does_not_carry_high_confidence(self):
        verdict, confidence, _ = build_summary(
            "consolidation", "mixed", "unknown", [], [], "弱趋势", "bearish",
            {"adx": 17.4, "di_plus": 0.8, "di_minus": 0.6},
        )
        assert verdict != "中性" or confidence <= 0.5, (
            f"verdict={verdict} confidence={confidence}"
        )

    def test_confidence_stays_in_range(self):
        for st_dir in ("bullish", "bearish", "unknown"):
            _, confidence, _ = build_summary(
                "uptrend", "bullish", "above",
                [{"type": "golden_cross", "desc": "x"}] * 5,
                [], "强趋势", st_dir, {"adx": 40.0, "di_plus": 1.0, "di_minus": 0.5},
            )
            assert 0.0 <= confidence <= 1.0


class TestMissingIndicatorData:

    def test_null_indicators_do_not_become_oversold_signals(self):
        rows = newest_first([10.0] * 10)
        for row in rows:
            row.update(rsi6=None, mfi=None, willr=None, cci=None, zscore=None)
        names = {s["name"] for s in detect_signals(rows)}
        assert not names & {"RSI6超卖", "MFI超卖", "Williams%R超买", "Williams%R超卖"}

    def test_all_null_indicators_do_not_produce_a_bullish_verdict(self):
        rows = newest_first([10.0] * 10)
        for row in rows:
            row.update(rsi6=None, mfi=None, willr=None, cci=None, zscore=None,
                       dif=None, dea=None, k=None, d=None)
        summary = summarize_signals(detect_signals(rows))
        assert summary["verdict"] == "中性"
        assert summary["buy_signals"] == 0
        assert summary["sell_signals"] == 0


class TestStrengthReflectsDataCompleteness:
    """strength = 规则基准分 × 数据完整度。

    这组规则写在"5 日均值"上，以前只喂 2 根 K 线也能算出 0.6 / 0.55 的
    strength，与喂满 5 根完全同分 —— 复盘统计分不出"数据残缺的票"。
    """

    @staticmethod
    def _bb_rows(widths):
        """widths 按 rows 的顺序给：widths[0] 是最新一根。"""
        rows = newest_first([10.0] * len(widths))
        for row, width in zip(rows, widths):
            row["bb_width"] = width
        return rows

    @staticmethod
    def _atr_rows(atrs):
        """atrs 按 rows 的顺序给：atrs[0] 是最新一根。"""
        rows = newest_first([10.0] * len(atrs))
        for row, atr in zip(rows, atrs):
            row["atr"] = atr
        return rows

    @staticmethod
    def _pick(signals, name):
        return next(s for s in signals if s["name"] == name)

    def test_short_window_scores_strictly_lower_than_full_window(self):
        short = self._pick(detect_signals(self._bb_rows([1.0, 5.0])), "布林带收口")
        full = self._pick(
            detect_signals(self._bb_rows([1.0, 5.0, 5.0, 5.0, 5.0])), "布林带收口")
        assert short["strength"] < full["strength"], (
            f"2 根 K 线算的 5 日收口 {short['strength']} 不该等于 "
            f"5 根的 {full['strength']}"
        )
        assert short["completeness"] < full["completeness"] == 1.0

    def test_atr_expansion_is_discounted_on_a_short_window(self):
        short = self._pick(detect_signals(self._atr_rows([3.0, 1.0])), "ATR扩张")
        full = self._pick(
            detect_signals(self._atr_rows([3.0, 1.0, 1.0, 1.0, 1.0])), "ATR扩张")
        assert short["strength"] < full["strength"]

    def test_complete_data_keeps_the_previous_strength(self):
        """完整数据上的 strength 必须与折扣前的基准分逐位相同 ——
        折扣只该影响残缺数据，不该悄悄改动策略语义。"""
        full = self._pick(
            detect_signals(self._bb_rows([1.0, 5.0, 5.0, 5.0, 5.0])), "布林带收口")
        assert full["strength"] == 0.6
        assert full["completeness"] == 1.0

    def test_single_bar_rules_are_never_discounted(self):
        """单根判据缺数据时根本不发信号，所以完整度恒为 1，基准分不变。"""
        rows = newest_first([10.0] * 10)
        rows[0].update(rsi6=12.0)
        signal = self._pick(detect_signals(rows), "RSI6超卖")
        assert signal["completeness"] == 1.0
        assert signal["strength"] == 0.7

    def test_null_inside_the_window_suppresses_the_signal_entirely(self):
        """窗口里有 None 是**不发信号**，不是打折 —— 折扣只处理"行数不够"。

        两者不能混：拿缺值的窗口算出一个"打了折的均值"再据此发信号，
        等于把没算出来的数当成算出来的数用。
        """
        rows = self._atr_rows([3.0, 1.0, 1.0, 1.0, 1.0])
        rows[2]["atr"] = None
        assert "ATR扩张" not in {s["name"] for s in detect_signals(rows)}


class TestHeadAndShoulders:

    def test_measured_move_target_sits_below_the_neckline(self):
        rows = newest_first(zigzag(
            peaks=[(10, 12.0), (30, 15.0), (50, 12.0)],
            troughs=[(4, 9.0), (20, 10.0), (40, 10.0), (56, 10.5)],
            n=60,
        ))
        found = detect_head_and_shoulders(rows)
        assert found is not None
        levels = found["key_levels"]
        assert levels["target"] < levels["neckline"] < levels["head"], levels

    def test_shoulders_are_labelled_oldest_to_newest(self):
        rows = newest_first(zigzag(
            peaks=[(10, 12.0), (30, 15.0), (50, 12.0)],
            troughs=[(4, 9.0), (20, 10.0), (40, 10.0), (56, 10.5)],
            n=60,
        ))
        levels = detect_head_and_shoulders(rows)["key_levels"]
        assert levels["left_shoulder"] < levels["head"]
        assert levels["right_shoulder"] < levels["head"]


class TestWedge:

    def test_rising_highs_and_lows_is_a_rising_wedge(self):
        rows = newest_first([10.0 + i * 0.3 for i in range(25)])
        found = detect_wedge(rows)
        assert found is not None
        assert (found["name"], found["direction"]) == ("上升楔形", "bearish"), found

    def test_falling_highs_and_lows_is_a_falling_wedge(self):
        rows = newest_first([25.0 - i * 0.3 for i in range(25)])
        found = detect_wedge(rows)
        assert found is not None
        assert (found["name"], found["direction"]) == ("下降楔形", "bullish"), found

    def test_description_matches_the_measured_slopes(self):
        rows = newest_first([10.0 + i * 0.3 for i in range(25)])
        assert "同时上升" in detect_wedge(rows)["desc"]


class TestFlag:

    def test_pole_up_then_drift_is_a_bull_flag(self):
        rows = newest_first(
            [10.0, 10.8, 11.6, 12.4, 13.2, 14.0, 14.6, 15.0, 15.4, 15.8,
             16.0, 15.9, 15.8, 15.7, 15.6, 15.5, 15.4, 15.3]
        )
        found = detect_flag(rows)
        assert found is not None and found["direction"] == "bullish", found

    def test_pole_down_then_drift_is_a_bear_flag(self):
        rows = newest_first(
            [20.0, 19.2, 18.4, 17.6, 16.8, 16.0, 15.4, 15.0, 14.6, 14.2,
             14.0, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7]
        )
        found = detect_flag(rows)
        assert found is not None and found["direction"] == "bearish", found

    def test_a_monotonic_decline_is_never_called_a_bull_flag(self):
        rows = newest_first([20.0 - i * 0.3 for i in range(25)])
        found = detect_flag(rows)
        assert found is None or found["direction"] != "bullish", found


class TestSqlInterpolation:

    def test_market_sql_binds_thscode_as_a_parameter(self):
        sql = build_market_sql()
        assert "WHERE thscode = ?" in sql
        assert "'" not in sql.split("WHERE")[1].split("ORDER")[0]

    def test_indicators_sql_binds_thscode_as_a_parameter(self):
        sql = build_indicators_sql()
        assert "WHERE thscode = ?" in sql

    def test_indicators_only_sql_binds_thscode_as_a_parameter(self):
        sql = build_indicators_only_sql()
        assert "WHERE thscode = ?" in sql

    def test_no_sql_builder_coerces_null_to_zero(self):
        for builder in (build_market_sql, build_indicators_sql,
                        build_indicators_only_sql):
            assert "COALESCE" not in builder().upper(), builder.__name__


class TestMovingAverageLoading:
    """The moving averages were never loaded at all.

    `build_indicators_sql` used to select INDICATORS_ONLY_FIELDS, which does
    not contain ma5..ma250, and `fetch_market_data` then overwrote the market
    row's own MA columns with the absent indicator ones. Every stock therefore
    reported ma60=0/ma120=0 and `alignment: unknown` / `price_vs_ma20:
    unknown` — silently, for the entire universe.
    """

    def test_indicators_sql_selects_every_moving_average(self):
        sql = build_indicators_sql()
        for alias in ("ma5", "ma10", "ma20", "ma60", "ma120", "ma250"):
            assert f"AS {alias}" in sql, f"{alias} missing from the indicators query"

    def test_indicators_sql_covers_the_signal_oscillators(self):
        sql = build_indicators_sql()
        for alias in ("k", "d", "j", "stoch_k", "cci", "willr", "vi_plus", "dc_upper"):
            assert f"AS {alias}" in sql, f"{alias} missing from the indicators query"


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------

class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def clear_prefix(self, prefix):
        doomed = [k for k in self.store if k.startswith(prefix)]
        for key in doomed:
            self.store.pop(key)
        return len(doomed)


class TestCache:

    def test_memory_tier_honours_ttl(self):
        cache = MultiLevelCache()
        asyncio.run(cache.set("query", "k", [1, 2, 3], ttl=1))
        assert asyncio.run(cache.get("query", "k")) == [1, 2, 3]
        time.sleep(1.2)
        assert asyncio.run(cache.get("query", "k")) is None

    def test_lru_stores_an_expiry_per_entry(self):
        lru = LRUCache(default_ttl=60)
        lru.set("k", 1)
        assert any("expires_at" in name for _, (name, _) in
                   [("x", ("expires_at", None))] ) or True
        entry = next(iter(lru._cache.values()))
        assert isinstance(entry, tuple) and len(entry) == 2
        assert entry[0] > time.monotonic()  # (expires_at, value)

    def test_ttl_zero_means_do_not_cache(self):
        lru = LRUCache(default_ttl=60)
        lru.set("k", 1, ttl=0)
        assert lru.get("k") is None

    def test_clear_all_also_drops_the_redis_tier(self):
        async def scenario():
            cache = MultiLevelCache()
            cache.set_redis(_FakeRedis())
            await cache.set("query", "k", [1, 2, 3], ttl=300)
            await cache.clear_all()
            return await cache.get("query", "k")

        assert asyncio.run(scenario()) is None

    def test_stable_hash_is_identical_across_processes(self):
        program = (
            "import sys; sys.path.insert(0, '.');"
            "from datasources.cache import stable_hash;"
            "print(stable_hash('SELECT 1'))"
        )
        keys = {
            subprocess.run(
                [sys.executable, "-c", program], capture_output=True, text=True,
                cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
            ).stdout.strip()
            for _ in range(3)
        }
        assert len(keys) == 1 and keys != {"", "None"}, f"unstable: {keys}"

    def test_stable_hash_separates_different_inputs(self):
        assert stable_hash("SELECT 1") != stable_hash("SELECT 2")
        assert stable_hash("db", "SELECT 1") != stable_hash("dbx", "SELECT 1")


# ----------------------------------------------------------------------
# The annotator's `source` contract.
#
# 前端要能区分「算法算出来的位」和「模型嘴上说的位」——两者会画在同一张 K 线上
# 但形状与颜色不同。source 缺失时前端会把它当成未知来源而静默不画（不报错），
# 所以这个字段的缺失是静默失效，必须由测试守住。
# ----------------------------------------------------------------------

def _annotator_bars(n=25, *, doji_low_vol_on_last=True):
    """Chronological bars that trip detect_key_k on the final bar.

    十字星 + 缩量：body 为 0（open == close），最后一天的成交量远低于 20 日均量。
    """
    rows = []
    for i in range(n):
        close = 10.0
        volume = 1000.0
        rows.append({
            "date": f"d{i:02d}",
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": volume,
        })
    if doji_low_vol_on_last:
        rows[-1]["volume"] = 100.0
    return rows


class TestAnnotatorSourceContract:

    def test_every_annotation_carries_source(self):
        from zettaranc.annotator import annotator

        found = annotator.annotate_all(_annotator_bars())
        assert found, "用例数据应当触发至少一个形态，否则测试没有意义"
        missing = [a for a in found if "source" not in a]
        assert not missing, f"缺 source 的标注会被前端静默丢弃: {missing}"

    def test_algorithm_detections_are_labelled_algorithm(self):
        from zettaranc.annotator import annotator

        found = annotator.annotate_all(_annotator_bars())
        assert {a["source"] for a in found} == {"algorithm"}

    def test_annotation_keeps_its_existing_shape(self):
        """加 source 不能动到既有字段——前端按 type/date/price 渲染。"""
        from zettaranc.annotator import annotator

        found = annotator.annotate_all(_annotator_bars())
        for a in found:
            assert set(a) >= {"type", "date", "price", "text", "confidence", "metadata", "source"}

    def test_a_detector_supplied_source_is_not_overwritten(self):
        """模型侧的标注走同一张表但带 source="llm"，不能被盖成 algorithm。"""
        from zettaranc.annotator import ZettarancAnnotator

        original = ZettarancAnnotator.detect_key_k

        def fake_detect(bars):
            return [{
                "type": "key_k", "date": "d01", "price": 10.0, "text": "模型说的",
                "confidence": 0.5, "metadata": {}, "source": "llm",
            }]

        ZettarancAnnotator.detect_key_k = staticmethod(fake_detect)
        try:
            found = ZettarancAnnotator.annotate_all(_annotator_bars(), ["key_k"])
        finally:
            ZettarancAnnotator.detect_key_k = original

        assert len(found) == 1
        assert found[0]["source"] == "llm", "显式 source 不应被 setdefault 覆盖"


# ----------------------------------------------------------------------
# 可绘制坐标契约：形态要能画到图上，就必须给出关键点的位置（日期 + 价格）。
#
# 前端按**日期**把点对齐到 K 线（与 annotate 同口径），所以 date 必须是
# 数据里真实存在的日期串——空串或 None 会让这个点画不出来。
#
# 两个字段的分工：
#   points —— 离散顶点（头肩的三个肩、双顶的两个顶）
#   lines  —— 参考线，统一成"两端点"形状，水平线两端同价、斜线两端不同价
# ----------------------------------------------------------------------

class TestDrawableCoordinates:

    @staticmethod
    def _assert_points_ok(rows, points):
        assert isinstance(points, list) and len(points) >= 2, "顶点至少两个才画得出线"
        for p in points:
            assert set(p) >= {"index", "date", "price", "label"}
            assert isinstance(p["index"], int) and 0 <= p["index"] < len(rows)
            assert isinstance(p["price"], float) and p["price"] > 0
            assert p["date"] and p["date"] == str(rows[p["index"]].get("date")), \
                "date 必须与对应 K 线一致，否则前端对不上"
            assert p["label"]

    @staticmethod
    def _assert_lines_ok(rows, lines):
        assert isinstance(lines, list) and lines, "至少一条参考线"
        for ln in lines:
            assert "label" in ln and ln["label"]
            pts = ln["points"]
            assert isinstance(pts, list) and len(pts) == 2, "参考线统一两端点"
            for p in pts:
                assert p["date"] and p["date"] == str(rows[p["index"]].get("date"))
                assert p["price"] > 0

    def test_头肩顶给出三个顶点与颈线目标(self):
        from zettaranc.pattern import detect_head_and_shoulders
        # 形状取自本文件已有的头肩顶用例（左肩 12 / 头 15 / 右肩 12）
        rows = newest_first(zigzag(
            peaks=[(10, 12.0), (30, 15.0), (50, 12.0)],
            troughs=[(4, 9.0), (20, 10.0), (40, 10.0), (56, 10.5)],
            n=60,
        ))
        found = detect_head_and_shoulders(rows)
        assert found is not None, "这个形状在既有用例里是能触发的，不该失配"
        self._assert_points_ok(rows, found["points"])
        assert [p["label"] for p in found["points"]] == ["左肩", "头", "右肩"]
        self._assert_lines_ok(rows, found["lines"])
        labels = {ln["label"] for ln in found["lines"]}
        assert {"颈线", "目标"} <= labels

    def test_双顶双底的顶点按时间顺序(self):
        from zettaranc.pattern import detect_double_top_bottom
        # 两个等高的顶（PEAK_TOLERANCE 3% 之内）
        rows = newest_first(zigzag(
            peaks=[(20, 15.0), (40, 15.0)],
            troughs=[(10, 10.0), (30, 12.0), (50, 12.0)],
            n=60,
        ))
        found = detect_double_top_bottom(rows)
        assert found is not None, "两个等高的顶应被判为双顶"
        self._assert_points_ok(rows, found["points"])
        idx = [p["index"] for p in found["points"]]
        # rows 倒序：index 大 = 更老。按时间顺序（老 -> 新）应递减
        assert idx == sorted(idx, reverse=True), f"顶点应按时间顺序：{idx}"
        self._assert_lines_ok(rows, found["lines"])

    def test_三角形给出上下两条边界线(self):
        from zettaranc.pattern import detect_triangle
        # 必须自己造行：conftest 的 newest_first 让 high/low 与 close 成同一比例，
        # 斜率必然同号，对称三角形（上边界下移、下边界上移）在那里永远触发不了。
        rows = []
        for i in range(30):  # i=0 最新
            # 振幅随「越老」越大 -> 最新端最小 = 收敛。
            # 方向写反的话上边界会变成上移，判定条件（upper_slope < 0 表示高点下移）
            # 就不成立，检测器直接返回 None。
            amp = 1.0 + i * 0.15
            rows.append({
                "date": f"d{i:02d}", "open": 20.0,
                "high": 20.0 + amp, "low": 20.0 - amp,
                "close": 20.0, "vol": 1000.0,
            })
        found = detect_triangle(rows)
        assert found is not None, "收敛的上下边界应被判为对称三角形"
        self._assert_lines_ok(rows, found["lines"])
        labels = {ln["label"] for ln in found["lines"]}
        assert labels == {"上边界", "下边界"}

    def test_边界线两端价格不同才算斜线(self):
        from zettaranc.pattern import _boundary_lines
        rows = newest_first([10 + i * 0.5 for i in range(40)], n=40)
        lines = _boundary_lines(rows, 30, 5)
        assert len(lines) == 2
        upper = next(l for l in lines if l["label"] == "上边界")
        # 单调上涨的数据里，上边界两端价格不应相同
        assert upper["points"][0]["price"] != upper["points"][1]["price"]

    def test_所有形态的坐标都能对齐到真实日期(self):
        """跨检测器的统一契约：给出的 date 必须能在 rows 里找到。"""
        from zettaranc.pattern import analyze_chart_pattern
        rows = newest_first(zigzag(
            [(4, 20.0), (16, 20.0), (28, 18.0)],
            [(10, 12.0), (22, 13.0), (34, 11.0)]), n=60)
        out = analyze_chart_pattern(rows)
        valid_dates = {str(r.get("date")) for r in rows}
        for p in out["patterns"]:
            for pt in (p.get("points") or []):
                assert pt["date"] in valid_dates, f"{p['name']} 的顶点日期不在数据里：{pt['date']}"
            for ln in (p.get("lines") or []):
                for pt in ln["points"]:
                    assert pt["date"] in valid_dates, f"{p['name']} 的参考线日期不在数据里"

    def test_旗杆不会横跨整段历史(self):
        """真实数据动辄 250+ 根，旗杆必须封顶。

        不封顶时 `pole = rows[consolidation_len:]` 会吃下全部历史：判出来的
        change_pct 是区间总涨跌幅（这里会是 -70% 的"熊市旗形"），画出来的旗杆
        线横跨一年多。短窗口的单测发现不了，所以这条用长序列守住。
        """
        from zettaranc.pattern import detect_flag, FLAG_POLE_MAX

        closes = [100.0 - i * 0.2 for i in range(380)]   # 长期阴跌
        closes += [24.0 + i * 0.6 for i in range(10)]    # 急涨 24.0 -> 29.4
        closes += [29.4, 29.5, 29.3, 29.4, 29.5, 29.4, 29.5, 29.3, 29.4, 29.5]  # 横盘
        rows = newest_first(closes)

        found = detect_flag(rows)
        assert found is not None, "急涨后横盘应被判为牛市旗形"
        assert found["direction"] == "bullish", f"不该被整段历史带偏：{found['desc']}"

        pole = next(l for l in found["lines"] if l["label"] == "旗杆")
        span = abs(pole["points"][0]["index"] - pole["points"][1]["index"])
        assert span <= FLAG_POLE_MAX, f"旗杆跨度 {span} 超过上限 {FLAG_POLE_MAX}"


# ---------------------------------------------------------------------------
# 补充几何形态：三重顶底 / 矩形整理 / 岛形反转
# ---------------------------------------------------------------------------

class TestTripleTopBottom:

    def test_三个等高顶判为三重顶(self):
        from zettaranc.pattern import detect_triple_top_bottom
        rows = newest_first(zigzag(
            peaks=[(10, 15.0), (30, 15.0), (50, 15.0)],
            troughs=[(4, 12.0), (20, 12.0), (40, 12.0), (56, 12.0)],
            n=60,
        ))
        found = detect_triple_top_bottom(rows)
        assert found is not None, "三个等高顶应判为三重顶"
        assert found["name"] == "三重顶" and found["direction"] == "bearish"
        assert [p["label"] for p in found["points"]] == ["顶1", "顶2", "顶3"]
        idx = [p["index"] for p in found["points"]]
        assert idx == sorted(idx, reverse=True), f"顶点应按时间顺序（老->新）递减：{idx}"
        assert {ln["label"] for ln in found["lines"]} == {"颈线", "目标"}

    def test_三个等高底判为三重底(self):
        from zettaranc.pattern import detect_triple_top_bottom
        rows = newest_first(zigzag(
            peaks=[(4, 18.0), (20, 18.0), (40, 18.0), (56, 18.0)],
            troughs=[(10, 15.0), (30, 15.0), (50, 15.0)],
            n=60,
        ))
        found = detect_triple_top_bottom(rows)
        assert found is not None and found["name"] == "三重底"
        assert found["direction"] == "bullish"

    def test_逐级抬高的顶不算三重顶(self):
        """那是上升三角形/上升趋势，容差必须挡得住。"""
        from zettaranc.pattern import detect_triple_top_bottom
        rows = newest_first(zigzag(
            peaks=[(10, 12.0), (30, 15.0), (50, 18.0)],
            troughs=[(4, 10.0), (20, 13.0), (40, 16.0), (56, 16.0)],
            n=60,
        ))
        found = detect_triple_top_bottom(rows)
        assert found is None or found["name"] != "三重顶", f"逐级抬高不该判三重顶：{found}"

    def test_两点不足时不误判(self):
        from zettaranc.pattern import detect_triple_top_bottom
        rows = newest_first([10.0 + (i % 5) for i in range(20)], n=20)
        assert detect_triple_top_bottom(rows) is None


class TestRectangle:

    @staticmethod
    def _box(n=40, lower=10.0, upper=11.5):
        """在 [lower, upper] 之间来回震荡的行。

        带宽刻意取 15%：检测器的上限是 18%，取 20% 会被自己挡掉 ——
        那种"测试数据本身就违反被测约束"的用例只会浪费一次排查。
        """
        rows = []
        for i in range(n):
            # 方波：上沿/下沿各碰一半
            high = upper if i % 4 in (0, 1) else upper - 0.3
            low = lower if i % 4 in (2, 3) else lower + 0.3
            close = (high + low) / 2
            rows.append({"date": f"2026-01-{i + 1:02d}", "open": close,
                         "high": high, "low": low, "close": close, "vol": 1000.0})
        return rows

    def test_上下沿反复被碰判为矩形(self):
        from zettaranc.pattern import detect_rectangle
        rows = self._box()
        rows.reverse()   # rows[0] 最新
        found = detect_rectangle(rows)
        assert found is not None and found["name"] == "矩形整理"
        labels = {ln["label"] for ln in found["lines"]}
        assert labels == {"箱体上沿", "箱体下沿"}

    def test_箱体不预判方向(self):
        from zettaranc.pattern import detect_rectangle
        rows = self._box()
        rows.reverse()
        assert detect_rectangle(rows)["direction"] == "neutral", "没突破就不该站边"

    def test_突破后给出方向(self):
        from zettaranc.pattern import detect_rectangle
        rows = self._box()
        rows.reverse()
        rows[0]["close"] = 12.5   # 向上突破上沿
        found = detect_rectangle(rows)
        assert found is not None
        assert found["direction"] == "bullish"
        assert "突破" in found["desc"]

    def test_单边趋势不算矩形(self):
        from zettaranc.pattern import detect_rectangle
        rows = newest_first([10.0 + i * 0.5 for i in range(40)], n=40)
        assert detect_rectangle(rows) is None, "单边上涨的振幅远超上限，不该判箱体"

    def test_只碰过一次的通道不算矩形(self):
        """V 形往返：上下沿各只碰到一次，是一条通道而不是箱体。"""
        from zettaranc.pattern import detect_rectangle
        closes = [10.0] + [10.0 + i * 0.15 for i in range(1, 20)] + [12.85 - i * 0.15 for i in range(1, 20)]
        rows = newest_first(closes, n=len(closes))
        assert detect_rectangle(rows) is None


class TestIslandReversal:

    @staticmethod
    def _bar(date, high, low):
        return {"date": date, "open": low, "high": high, "low": low, "close": high, "vol": 1000.0}

    def test_上跳后下跳判为顶部岛形(self):
        from zettaranc.pattern import detect_island_reversal
        # 行序：最新在前。时间顺序 = 反转后再反转
        chrono = [
            self._bar("d0", 10.0, 9.5),    # 缺口中枢之下
            self._bar("d1", 10.0, 9.5),
            self._bar("d2", 12.0, 11.5),   # 向上跳空（low 11.5 > 前一根 high 10.0）
            self._bar("d3", 12.5, 12.0),   # 岛
            self._bar("d4", 13.0, 12.5),   # 岛
            self._bar("d5", 10.5, 10.0),   # 向下跳空（high 10.5 < 前一根 low 12.5）
            self._bar("d6", 10.5, 10.0),
        ]
        rows = list(reversed(chrono))
        found = detect_island_reversal(rows)
        assert found is not None, "上下两个反向缺口夹出的孤立区间应判为岛形"
        assert found["name"] == "顶部岛形反转"
        assert found["direction"] == "bearish"

    def test_下跳后上跳判为底部岛形(self):
        from zettaranc.pattern import detect_island_reversal
        chrono = [
            self._bar("d0", 10.5, 10.0),
            self._bar("d1", 10.5, 10.0),
            self._bar("d2", 9.0, 8.5),     # 向下跳空
            self._bar("d3", 8.5, 8.0),     # 岛
            self._bar("d4", 8.0, 7.5),
            self._bar("d5", 9.5, 9.0),     # 向上跳空
            self._bar("d6", 9.5, 9.0),
        ]
        rows = list(reversed(chrono))
        found = detect_island_reversal(rows)
        assert found is not None and found["name"] == "底部岛形反转"
        assert found["direction"] == "bullish"

    def test_只有一个缺口不算岛形(self):
        from zettaranc.pattern import detect_island_reversal
        chrono = [
            self._bar("d0", 10.0, 9.5),
            self._bar("d1", 12.0, 11.5),   # 向上跳空
            self._bar("d2", 12.5, 12.0),
            self._bar("d3", 13.0, 12.5),   # 之后一路走高，没有反向缺口
            self._bar("d4", 13.5, 13.0),
        ]
        rows = list(reversed(chrono))
        assert detect_island_reversal(rows) is None, "单一缺口是普通跳空，不是反转"

    def test_两个同向缺口不算岛形(self):
        from zettaranc.pattern import detect_island_reversal
        chrono = [
            self._bar("d0", 10.0, 9.5),
            self._bar("d1", 12.0, 11.5),   # 上跳
            self._bar("d2", 12.5, 12.0),
            self._bar("d3", 14.0, 13.5),   # 再上跳（同向 = 持续缺口）
            self._bar("d4", 14.5, 14.0),
        ]
        rows = list(reversed(chrono))
        assert detect_island_reversal(rows) is None
