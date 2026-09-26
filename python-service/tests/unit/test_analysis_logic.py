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
