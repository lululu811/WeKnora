"""Unit tests for the set-based market screener.

The screener used to be an N+1: one `scan_patterns` call per A-share, 5571
round-trips, 36-51 seconds per request, over a hardcoded `LIMIT 100` slice
of the universe. It is now two set-based queries. These tests pin the parts
that are easy to get wrong:

* the universe is the whole `a-share` set, ordered, not an arbitrary 100
* indicators that are NULL disqualify a symbol rather than reading as oversold
* a strategy whose rule names signals the screener cannot evaluate says so,
  instead of silently matching nothing
"""

import pytest

from conftest import newest_first

from zettaranc import screener


def _row(code, date, **overrides):
    """One indicator row shaped like the screener's query output."""
    base = {
        "thscode": code, "date": date,
        "dif": 0.0, "dea": 0.0, "macd_hist": 0.0,
        "k": 50.0, "d": 50.0, "j": 50.0,
        "rsi6": 50.0, "rsi14": 50.0,
        "stoch_k": 50.0, "stoch_d": 50.0,
        "cci": 0.0, "willr": -50.0, "mfi": 50.0,
        "adx": 20.0, "di_plus": 10.0, "di_minus": 5.0,
        "st_dir": 1.0, "st_val": 1.0, "psar": 1.0,
        "aroon_up": 50.0, "aroon_down": 50.0,
        "vi_plus": 1.0, "vi_minus": 1.0,
        "bb_upper": 11.0, "bb_mid": 10.0, "bb_lower": 9.0, "bb_width": 2.0,
        "atr": 0.5, "dc_upper": 12.0, "dc_lower": 8.0,
        "kc_upper": 11.0, "kc_mid": 10.0, "kc_lower": 9.0,
        "cmf": 0.0, "obv": 0.0, "vwap": 10.0,
        "zscore": 0.0, "lin_slope": 0.0,
        "cdl_morning_star": 0.0, "cdl_evening_star": 0.0, "cdl_hammer": 0.0,
        "cdl_shooting_star": 0.0, "cdl_doji": 0.0, "cdl_engulfing": 0.0,
        "cdl_harami": 0.0, "cdl_piercing": 0.0, "cdl_dark_cloud": 0.0,
        "cdl_3white": 0.0, "cdl_3black": 0.0,
    }
    base.update(overrides)
    return base


def _two_days(code, **latest):
    return [_row(code, "2026-09-24", **latest), _row(code, "2026-09-23")]


class TestUniverseSql:

    def test_universe_covers_every_a_share_in_a_stable_order(self):
        sql = screener.build_universe_sql()
        assert "asset_type = 'a-share'" in sql
        assert "ORDER BY thscode" in sql, \
            "without ORDER BY the universe slice is arbitrary"

    def test_indicator_snapshot_binds_the_code_list(self):
        sql = screener.build_indicator_snapshot_sql()
        assert "string_split(?, ',')" in sql, \
            "the code list must be a bind parameter, not interpolated SQL"
        assert "{" not in sql and "}" not in sql

    def test_indicator_snapshot_takes_more_than_one_day(self):
        """detect_signals needs rows[1] to evaluate crossovers."""
        sql = screener.build_indicator_snapshot_sql()
        assert "row_number() OVER" in sql
        assert screener.LOOKBACK_DAYS >= 2


class TestGrouping:

    def test_rows_are_grouped_newest_first(self):
        rows = _two_days("600519.SH", rsi6=12.0) + _two_days("000001.SZ", rsi6=55.0)
        grouped = screener.group_by_symbol(rows)
        assert set(grouped) == {"600519.SH", "000001.SZ"}
        for entry in grouped.values():
            assert entry["rows"][0]["date"] == "2026-09-24"

    def test_names_are_attached_from_the_universe(self):
        grouped = screener.group_by_symbol(
            _two_days("600519.SH"), names={"600519.SH": "贵州茅台"}
        )
        assert grouped["600519.SH"]["name"] == "贵州茅台"

    def test_null_core_indicators_mark_the_symbol_incomplete(self):
        rows = _two_days("601059.SH", rsi6=None, mfi=None)
        entry = screener.group_by_symbol(rows)["601059.SH"]
        assert entry["data_complete"] is False
        assert set(entry["missing"]) >= {"rsi6", "mfi"}


class TestScreen:

    def test_incomplete_symbols_never_enter_the_pool(self):
        """The bug this guards: COALESCE(NULL,0) made RSI6=0 look oversold, so
        instruments with no indicator data were selected as buys."""
        rows = _two_days("601059.SH", rsi6=None, mfi=None, willr=None, adx=None)
        rule = {"match_signals": ["RSI6超卖", "MFI超卖"], "min_count": 2}
        assert screener.screen(rows, rule, 10)["matched"] == 0

    def test_genuinely_oversold_symbols_still_match(self):
        rows = _two_days("600519.SH", rsi6=12.0, mfi=15.0, willr=-85.0)
        rule = {"match_signals": ["RSI6超卖", "MFI超卖"], "min_count": 2}
        result = screener.screen(rows, rule, 10)
        assert result["matched"] == 1
        stock = result["stocks"][0]
        assert stock["thscode"] == "600519.SH"
        assert set(stock["matched_signals"]) == {"RSI6超卖", "MFI超卖"}
        assert stock["score"] > 0

    def test_neutral_signals_are_excluded_unless_the_rule_allows_them(self):
        rows = _two_days("600519.SH", atr=5.0)  # ATR扩张 is a `neutral` signal
        strict = {"match_signals": ["ATR扩张"], "min_count": 1}
        assert screener.screen(rows, strict, 10)["matched"] == 0
        lax = {"match_signals": ["ATR扩张"], "min_count": 1, "allow_neutral": True}
        assert screener.screen(rows, lax, 10)["matched"] == 1

    def test_allow_neutral_no_longer_lets_bearish_signals_through(self):
        """旧实现是 `s["signal"] == "bullish" or allow_neutral`：
        一旦某条规则开了 allow_neutral，条件对**所有**方向短路成真，
        bearish 信号照样入选 —— `anomaly` 因此稳定地选出看跌票。
        """
        # di_minus >> di_plus 是 bearish（ADX空头趋势）
        rows = _two_days("600519.SH", adx=35.0, di_plus=1.0, di_minus=30.0)
        rule = {"match_signals": ["ADX空头趋势"], "min_count": 1,
                "allow_neutral": True}
        assert screener.screen(rows, rule, 10)["matched"] == 0, \
            "allow_neutral 只该放行 neutral，不该放行 bearish"

    def test_direction_bearish_selects_only_bearish_signals(self):
        rows = _two_days("600519.SH", adx=35.0, di_plus=1.0, di_minus=30.0)
        rule = {"match_signals": ["ADX空头趋势"], "min_count": 1,
                "direction": "bearish"}
        result = screener.screen(rows, rule, 10)
        assert result["matched"] == 1
        assert result["stocks"][0]["matched_directions"] == ["bearish"]

    def test_direction_bearish_rejects_bullish_signals(self):
        rows = _two_days("600519.SH", rsi6=12.0)
        rule = {"match_signals": ["RSI6超卖"], "min_count": 1,
                "direction": "bearish"}
        assert screener.screen(rows, rule, 10)["matched"] == 0

    def test_direction_overrides_allow_neutral(self):
        """显式 direction 优先于 allow_neutral：声明了方向就不再看宽松开关。"""
        rows = _two_days("600519.SH", atr=5.0)  # ATR扩张 = neutral
        rule = {"match_signals": ["ATR扩张"], "min_count": 1,
                "allow_neutral": True, "direction": "bullish"}
        assert screener.screen(rows, rule, 10)["matched"] == 0

    def test_min_count_is_enforced(self):
        rows = _two_days("600519.SH", rsi6=12.0)
        rule = {"match_signals": ["RSI6超卖", "CCI超卖"], "min_count": 2}
        assert screener.screen(rows, rule, 10)["matched"] == 0

    def test_results_are_ranked_by_score_then_code(self):
        rows = (
            _two_days("600002.SH", rsi6=12.0, mfi=15.0)
            + _two_days("600001.SH", rsi6=12.0)
            + _two_days("600003.SH", rsi6=12.0, mfi=15.0, willr=-90.0, cci=-150.0)
        )
        rule = {"match_signals": ["RSI6超卖", "MFI超卖", "Williams%R超卖", "CCI超卖"],
                "min_count": 1}
        stocks = screener.screen(rows, rule, 10)["stocks"]
        scores = [s["score"] for s in stocks]
        assert scores == sorted(scores, reverse=True)
        assert stocks[0]["thscode"] == "600003.SH"
        # ties break on thscode so the ranking is reproducible
        tied = [s for s in stocks if s["score"] == scores[-1]]
        assert [s["thscode"] for s in tied] == sorted(s["thscode"] for s in tied)

    def test_limit_truncates_but_matched_reports_the_full_count(self):
        rows = [r for i in range(5) for r in _two_days(f"60000{i}.SH", rsi6=12.0)]
        rule = {"match_signals": ["RSI6超卖"], "min_count": 1}
        result = screener.screen(rows, rule, 2)
        assert result["matched"] == 5
        assert len(result["stocks"]) == 2

    def test_incomplete_count_is_reported(self):
        rows = (_two_days("600519.SH", rsi6=12.0)
                + _two_days("601059.SH", rsi6=None, mfi=None, adx=None, atr=None,
                            bb_upper=None, obv=None))
        rule = {"match_signals": ["RSI6超卖"], "min_count": 1}
        assert screener.screen(rows, rule, 10)["incomplete"] == 1


class TestRuleSignals:

    def test_known_strategies_do_not_reference_unevaluable_signals(self):
        """A rule naming a pattern the screener cannot see would silently match
        nothing — which is exactly how `anomaly` came to be structurally dead."""
        from main import STRATEGY_RULES

        for name, rule in STRATEGY_RULES.items():
            unsupported = screener.unsupported_signals(rule)
            assert not unsupported, (
                f"strategy {name} references signals the set-based screener "
                f"cannot evaluate: {unsupported}"
            )

    def test_every_rule_signal_is_reachable_from_real_indicator_values(self):
        """Every signal a strategy names must be one detect_signals can actually
        produce, or the strategy silently matches nothing.

        This is exactly how `anomaly` died: two of its three rules emitted
        `neutral` signals that the screener filtered out, and the third
        compared two floats for equality.
        """
        from main import STRATEGY_RULES
        from zettaranc.signals import detect_signals

        emitted = _emitted_names() | _emitted_names(bearish=True)

        for name, rule in STRATEGY_RULES.items():
            unknown = set(rule["match_signals"]) - emitted
            assert not unknown, (
                f"strategy {name} names signals nothing emits: {sorted(unknown)}"
            )

    def test_rule_signals_are_evaluable_by_the_screener(self):
        """Signals needing price/volume cannot be seen by the set-based screener
        because v_indicators_daily carries no price columns. If a strategy names
        one, the endpoint reports it in `unsupported_signals` instead of silently
        matching nothing."""
        from main import STRATEGY_RULES

        for name, rule in STRATEGY_RULES.items():
            unsupported = screener.unsupported_signals(rule)
            assert not unsupported, (
                f"strategy {name} names signals the screener cannot evaluate: "
                f"{unsupported}"
            )


def _emitted_names(bearish: bool = False):
    """All signal names detect_signals can produce for a real indicator shape.

    Two fixtures are needed: no single bar is both "oversold bullish" and
    "overbought bearish", so one row can never cover the whole rule table.
    """
    from zettaranc.signals import detect_signals

    rows = [dict(_row("600519.SH", f"2026-09-{24 - i:02d}")) for i in range(10)]
    if not bearish:
        # bullish: every oscillator oversold, one bar crossing up through them
        for r in rows:
            r.update(rsi6=12.0, mfi=15.0, willr=-85.0, cci=-150.0, zscore=-2.5,
                     cmf=0.2, adx=30.0, di_plus=20.0, di_minus=5.0,
                     aroon_up=80.0, aroon_down=10.0,
                     st_dir=1.0, st_val=1.05, k=19.0, d=18.0,
                     stoch_k=19.0, stoch_d=18.0, cdl_hammer=120.0)
        rows[0].update(dif=0.5, dea=0.2, vi_plus=1.2, vi_minus=1.0,
                       bb_width=0.5, bb_upper=11.0, bb_mid=10.0, bb_lower=9.0,
                       dc_upper=10.5, close=10.4, atr=0.5)
        rows[1].update(dif=0.1, dea=0.3, k=15.0, d=20.0,
                       stoch_k=15.0, stoch_d=20.0, close=10.3, atr=0.5)
    else:
        # bearish: every oscillator overbought, crossovers pointing down
        for r in rows:
            r.update(rsi6=88.0, mfi=85.0, willr=-15.0, cci=150.0, zscore=2.5,
                     cmf=-0.2, adx=30.0, di_plus=5.0, di_minus=20.0,
                     aroon_up=10.0, aroon_down=80.0,
                     st_dir=-1.0, st_val=9.0, k=85.0, d=82.0,
                     stoch_k=85.0, stoch_d=82.0, cdl_shooting_star=120.0,
                     bb_width=5.0)
        rows[0].update(dif=-0.5, dea=-0.2, vi_plus=1.0, vi_minus=1.2,
                       bb_width=0.5, bb_upper=11.0, bb_mid=10.0, bb_lower=9.0,
                       atr=5.0)
        rows[1].update(dif=-0.1, dea=-0.3, k=82.0, d=85.0,
                       stoch_k=82.0, stoch_d=85.0, atr=0.5)
    return {s["name"] for s in detect_signals(rows)}


class TestLimitUpPoolCollapse:
    """涨停池按代码归并时必须保留**连板数最大**的那条。

    实测缺陷：601811.SH（新华文轩）在近 6 个交易日留了 5 行，连板数 1→2→3→4→5。
    归并时用 `by_code[code] = row`，而 SQL 按 trade_date DESC 排序，于是最后
    写进去的是最早那天的 1 连板，5 连板被顶掉，排名字段跟着错。
    """

    def test_highest_streak_wins_over_earlier_rows(self):
        rows = [
            {"thscode": "601811.SH", "trade_date": "2026-09-24", "continue_day_cnt": 5},
            {"thscode": "601811.SH", "trade_date": "2026-09-23", "continue_day_cnt": 4},
            {"thscode": "601811.SH", "trade_date": "2026-09-22", "continue_day_cnt": 3},
            {"thscode": "601811.SH", "trade_date": "2026-09-21", "continue_day_cnt": 2},
            {"thscode": "601811.SH", "trade_date": "2026-09-18", "continue_day_cnt": 1},
        ]
        best = screener.collapse_limit_up_pool(rows)
        assert set(best) == {"601811.SH"}
        assert best["601811.SH"]["continue_day_cnt"] == 5
        assert best["601811.SH"]["trade_date"] == "2026-09-24", \
            "取 5 连板那一行，日期也必须跟着那一行，不能张冠李戴"

    def test_order_does_not_matter(self):
        ascending = [{"thscode": "X.SH", "continue_day_cnt": n} for n in (1, 2, 3, 4, 5)]
        assert screener.collapse_limit_up_pool(ascending)["X.SH"]["continue_day_cnt"] == 5
        assert screener.collapse_limit_up_pool(list(reversed(ascending)))["X.SH"]["continue_day_cnt"] == 5

    def test_null_streak_does_not_win(self):
        rows = [
            {"thscode": "Y.SH", "continue_day_cnt": None},
            {"thscode": "Y.SH", "continue_day_cnt": 2},
        ]
        assert screener.collapse_limit_up_pool(rows)["Y.SH"]["continue_day_cnt"] == 2

    def test_empty_and_missing_code(self):
        assert screener.collapse_limit_up_pool([]) == {}
        assert screener.collapse_limit_up_pool([{"continue_day_cnt": 3}]) == {}
        assert screener.collapse_limit_up_pool(None) == {}


class TestLimitUpPoolSql:
    def test_reads_the_real_limit_up_table(self):
        sql = screener.build_limit_up_pool_sql()
        assert "v_limit_up_pool" in sql, "必须读真实涨停表，不能靠代理指标"
        # 连板/封板时间/封单金额都是原生列
        for col in ("continue_day_cnt", "limit_up_time", "seal_money"):
            assert col in sql, f"{col} 是该表的原生列，应当取出来"

    def test_window_is_a_literal_because_duckdb_rejects_placeholders(self):
        """窗口用字面量是被迫的，不是疏忽。

        DuckDB 不接受 `INTERVAL ? DAY` 或 `?::DATE` 这类写法，会在 `?` 处
        抛 Parser Error。这条断言记录的是这个约束 —— 如果哪天 DuckDB 支持了
        绑定，这里会提醒把窗口改回参数形式。
        """
        sql = screener.build_limit_up_pool_sql()
        assert "?" not in sql, (
            "DuckDB 的 INTERVAL 不支持占位符，用 ? 会 Parser Error"
        )
        assert f"INTERVAL {screener.LIMIT_UP_LOOKBACK_DAYS} DAY" in sql, \
            "窗口天数应作为字面量出现在 SQL 里"
