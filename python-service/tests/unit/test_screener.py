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
from zettaranc.signals import detect_signals


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

    # 12 行而不是 10：「放量突破」要「最新一根 + 前 10 日均量」共 11 行，
    # 窗口不足时按设计不触发（拿残缺均量凑数会让稀疏数据的票误报）。
    rows = [dict(_row("600519.SH", f"2026-09-{24 - i:02d}")) for i in range(12)]
    if not bearish:
        # bullish: every oscillator oversold, one bar crossing up through them
        for r in rows:
            r.update(rsi6=12.0, mfi=15.0, willr=-85.0, cci=-150.0, zscore=-2.5,
                     cmf=0.2, adx=30.0, di_plus=20.0, di_minus=5.0,
                     aroon_up=80.0, aroon_down=10.0,
                     st_dir=1.0, st_val=1.05, k=19.0, d=18.0,
                     stoch_k=19.0, stoch_d=18.0, cdl_hammer=120.0)
        # 放量突破要 close + volume。最新一根放量且相对前一日明显高开
        # （10.4 vs 10.0 = +4%），配合 rows[1] 起的 10 日 1e8 恒定量
        # 构成「涨幅 >3% 且 量比 3.0」的命中。
        for r in rows:
            r.setdefault("volume", 1e8)
        rows[0].update(dif=0.5, dea=0.2, vi_plus=1.2, vi_minus=1.0,
                       bb_width=0.5, bb_upper=11.0, bb_mid=10.0, bb_lower=9.0,
                       # Donchian 比的是**前一日**上轨：rows[1].dc_upper=10.0，
                       # rows[0].close=10.4 越过它，构成突破
                       dc_upper=10.6, close=10.4, atr=0.5, volume=3.0e8)
        rows[1].update(dif=0.1, dea=0.3, k=15.0, d=20.0,
                       stoch_k=15.0, stoch_d=20.0, close=10.0, atr=0.5,
                       dc_upper=10.0)
    else:
        # bearish: every oscillator overbought, crossovers pointing down
        for r in rows:
            r.update(rsi6=88.0, mfi=85.0, willr=-15.0, cci=150.0, zscore=2.5,
                     cmf=-0.2, adx=30.0, di_plus=5.0, di_minus=20.0,
                     aroon_up=10.0, aroon_down=80.0,
                     st_dir=-1.0, st_val=9.0, k=85.0, d=82.0,
                     stoch_k=85.0, stoch_d=82.0, cdl_shooting_star=120.0,
                     bb_width=5.0)
        for r in rows:
            r.setdefault("volume", 1e8)
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


class TestSharding:
    """分片保证单条查询不撞 100k 行上限。

    实测：全市场 5,571 只 × 20 天，indicators 约 111,206 行、market 价量约
    83,269 行。单张表就已经贴着上限，**两张表相加必然超**，超了会被
    python-service 静默截断（只留前 100k 行），表现为"少了些票"且不报错。
    """

    def test_whole_market_fits_in_shards(self):
        codes = [f"{i:06d}.SZ" for i in range(5571)]
        shards = screener.shard_codes(codes)
        assert len(shards) > 1, "全市场一次查必然超限，必须分片"
        for sh in shards:
            worst = len(sh) * screener.LOOKBACK_DAYS
            assert worst <= screener.QUERY_ROW_BUDGET, (
                f"一片 {len(sh)} 只最坏会产生 {worst} 行，"
                f"超过预算 {screener.QUERY_ROW_BUDGET}"
            )

    def test_no_code_is_lost_or_duplicated(self):
        codes = [f"{i:06d}.SZ" for i in range(5571)]
        flat = [c for sh in screener.shard_codes(codes) for c in sh]
        assert len(flat) == len(codes)
        assert sorted(flat) == sorted(codes), "分片不能丢票也不能重复"

    def test_small_input_is_a_single_shard(self):
        assert len(screener.shard_codes(["600519.SH"])) == 1

    def test_empty_input(self):
        assert screener.shard_codes([]) == []


class TestMergePriceRows:
    def test_joins_on_code_and_date_not_code_alone(self):
        """只按 thscode 关联会让每一天都挂上"最新一天"的价量，
        「放量突破」于是拿最新价配昨天的量，量比彻底失真。"""
        ind = [
            {"thscode": "A.SH", "date": "2026-09-24"},
            {"thscode": "A.SH", "date": "2026-09-23"},
        ]
        price = [
            {"thscode": "A.SH", "date": "2026-09-24", "close": 12.0, "volume": 9e8},
            {"thscode": "A.SH", "date": "2026-09-23", "close": 11.0, "volume": 3e8},
        ]
        n = screener.merge_price_rows(ind, price)
        assert n == 2
        assert ind[0]["close"] == 12.0
        assert ind[1]["close"] == 11.0, "第二天不能挂上第一天的价量"

    def test_does_not_invent_dates(self):
        """价量多出来的日期不能被加进行里，否则 group_by_symbol 的
        "最新行"判定会漂移。"""
        ind = [{"thscode": "A.SH", "date": "2026-09-24"}]
        price = [
            {"thscode": "A.SH", "date": "2026-09-24", "close": 1.0},
            {"thscode": "A.SH", "date": "2026-09-25", "close": 2.0},
        ]
        screener.merge_price_rows(ind, price)
        assert len(ind) == 1, "不能因为价量多一天就给指标行加一天"
        assert ind[0]["date"] == "2026-09-24"

    def test_unmatched_price_is_left_absent_not_zero(self):
        ind = [{"thscode": "A.SH", "date": "2026-09-24"}]
        screener.merge_price_rows(ind, [{"thscode": "B.SH", "date": "2026-09-24",
                                          "close": 1.0}])
        assert "close" not in ind[0], "对不上就该是缺失，不能补 0"

    def test_null_values_are_preserved_as_null(self):
        ind = [{"thscode": "A.SH", "date": "2026-09-24"}]
        screener.merge_price_rows(ind, [{"thscode": "A.SH", "date": "2026-09-24",
                                          "close": None, "volume": 1e8}])
        assert ind[0]["close"] is None, "库里是 NULL 就得是 None"
        assert ind[0]["volume"] == 1e8

    def test_empty_inputs(self):
        assert screener.merge_price_rows([], []) == 0
        assert screener.merge_price_rows([{"thscode": "A", "date": "d"}], []) == 0
        assert screener.merge_price_rows([], [{"thscode": "A", "date": "d"}]) == 0


class TestPriceAwareSignals:
    """这两个信号过去锁在 SCREEN_UNSUPPORTED 里，因为 v_indicators_daily
    一个价格列都没有。现在价量接进来了，它们必须真的能触发。"""

    def _row(self, **over):
        base = {
            "rsi6": 50.0, "macd_hist": 0.0, "mfi": 50.0, "adx": 20.0,
            "bb_upper": 11.0, "atr": 0.5, "obv": 0.0, "cmf": 0.0,
            "zscore": 0.0, "lin_slope": 0.0, "k": 50.0, "d": 50.0, "j": 50.0,
            "cci": 0.0, "willr": -50.0, "stoch_k": 50.0, "stoch_d": 50.0,
            "dif": 0.0, "dea": 0.0, "di_plus": 10.0, "di_minus": 5.0,
            "st_dir": 1.0, "st_val": 1.0, "psar": 1.0,
            "aroon_up": 50.0, "aroon_down": 50.0,
            "vi_plus": 1.0, "vi_minus": 1.0, "bb_width": 2.0,
            "dc_upper": 12.0, "dc_lower": 8.0,
            "kc_upper": 11.0, "kc_mid": 10.0, "kc_lower": 9.0, "vwap": 10.0,
            "close": None, "high": None, "low": None, "volume": None,
        }
        base.update(over)
        return base

    def _series(self, latest_close, latest_vol, prev_close=10.0, prev_vol=1e8, n=11):
        rows = [self._row(date=f"2026-09-{10 + i:02d}", close=prev_close,
                          volume=prev_vol) for i in range(n)]
        rows.insert(0, self._row(date="2026-09-24", close=latest_close,
                                 volume=latest_vol))
        return rows

    def _hits(self, rows, name):
        return [s for s in detect_signals(rows) if s["name"] == name]

    def test_volume_breakout_fires_on_real_price_and_volume(self):
        rows = self._series(latest_close=10.5, latest_vol=2.5e8)  # +5%, 量比 2.5
        hits = self._hits(rows, "放量突破")
        assert len(hits) == 1
        assert hits[0]["signal"] == "bullish"
        assert "2.50" in hits[0]["desc"]

    def test_volume_breakout_needs_volume(self):
        rows = self._series(latest_close=10.5, latest_vol=1.2e8)  # 量比 1.2
        assert self._hits(rows, "放量突破") == []

    def test_volume_breakout_needs_price_move(self):
        rows = self._series(latest_close=10.2, latest_vol=3.0e8)  # +2%
        assert self._hits(rows, "放量突破") == []

    def test_volume_breakout_refuses_a_short_window(self):
        """窗口不足不能拿残缺均量凑数：用 3 天均量算出的量比是另一个指标，
        会让稀疏数据的票触发、完整数据的票不触发，而两边看起来一样。"""
        rows = self._series(latest_close=10.5, latest_vol=5e8, n=3)
        assert self._hits(rows, "放量突破") == []

    def test_volume_breakout_refuses_missing_price(self):
        rows = self._series(latest_close=None, latest_vol=5e8)
        assert self._hits(rows, "放量突破") == []

    def test_donchian_break_compares_against_the_previous_upper(self):
        """判据必须拿**前一日**的上轨比。

        指标库的 dc_upper 是含当日的 20 日最高价，而收盘价不可能高于当日
        最高价 —— `close > dc_upper` 数学上恒不成立，实测 60 只票 × 1,200 行
        回放命中 0 次。改比前一日上轨后同一批数据命中 55 次。
        """
        rows = self._series(latest_close=12.5, latest_vol=1e8, prev_close=12.0)
        # 当日上轨被自己抬高（12.5 >= close），比当日必然不成立
        rows[0]["dc_upper"] = 12.6
        # 前一日上轨 12.0，收盘 12.5 越过它 = 真突破
        rows[1]["dc_upper"] = 12.0
        hits = self._hits(rows, "Donchian上轨突破")
        assert len(hits) == 1, "收盘越过前一日上轨必须命中"
        assert "12.00" in hits[0]["desc"]

    def test_donchian_break_does_not_fire_when_close_stays_inside(self):
        """前一日上轨仍在收盘之上时不触发：没突破就是没突破。"""
        rows = self._series(latest_close=12.5, latest_vol=1e8, prev_close=12.0)
        rows[0]["dc_upper"] = 13.0   # 当日上轨被自己抬高
        rows[1]["dc_upper"] = 12.8   # 前一日上轨 12.8 > 收盘 12.5，未突破
        assert self._hits(rows, "Donchian上轨突破") == []

    def test_price_signals_are_no_longer_unsupported(self):
        assert "放量突破" not in screener.SCREEN_UNSUPPORTED
        assert "Donchian上轨突破" not in screener.SCREEN_UNSUPPORTED


class TestNoDeadSignalConfig:
    """防"只声明不使用"的配置表复活。

    曾经有一份 SCREEN_SIGNAL_FIELDS（信号名 -> 指标别名），注释写着
    "列出映射是为了让 STRATEGY_RULES 里写错名字时能在启动/测试期暴露出来"。
    实际上**没有任何代码读它**，而且它早已和真实信号集脱节 —— 缺
    KDJ超卖金叉、Aroon多头排列、放量突破与全部蜡烛形态。真按它做闸门
    会把大半合法信号判成"写错了"。

    真正在起作用的闸门是
    test_every_rule_signal_is_reachable_from_real_indicator_values。
    """

    def test_the_dead_mapping_table_is_gone(self):
        assert not hasattr(screener, "SCREEN_SIGNAL_FIELDS"), (
            "SCREEN_SIGNAL_FIELDS 是死配置：没有代码读它，且已与真实信号集脱节。"
            "写错信号名由 test_every_rule_signal_is_reachable_from_real_"
            "indicator_values 拦，不需要这张表。"
        )

    def test_every_mapped_field_would_have_been_a_real_indicator_column(self):
        """记录那张表本该做的事：把信号名映射到真实存在的指标列。

        死配置里的 13 个映射值全是合法列名 —— 所以它"看起来对"，
        这正是危险之处：没人会因为它列出的列名有错而发现它根本没被调用。
        真正缺的是那些**信号名**（KDJ超卖金叉、Aroon多头排列、放量突破
        与全部蜡烛形态），而这份测试无法在表被删掉后再复现那个缺口。
        所以这里只守住"列名合法"这一半，另一半由
        test_every_rule_signal_is_reachable_from_real_indicator_values 守。
        """
        from zettaranc.data_loader import INDICATOR_COLUMNS

        historical_fields = {
            "dif", "rsi6", "cci", "willr", "mfi", "zscore",
            "dc_upper", "atr", "bb_width", "cmf", "vi_plus", "adx",
        }
        unknown = {
            f for f in historical_fields
            if f not in INDICATOR_COLUMNS
        }
        assert not unknown, (
            f"这些映射目标不是真实的指标列名：{sorted(unknown)}"
        )

    def test_unsupported_signals_is_an_empty_but_live_gate(self):
        """取不到数据的信号才进这个集合；现在价量已接入，所以是空的。"""
        assert screener.SCREEN_UNSUPPORTED == {}
        rule = {"match_signals": ["放量突破", "Donchian上轨突破"], "min_count": 1}
        assert screener.unsupported_signals(rule) == []

    def test_adding_an_unfetchable_signal_would_surface_it(self):
        """闸门本身要有效：塞一个不存在的信号名进去必须被报出来。"""
        rule = {"match_signals": ["这个信号不存在"], "min_count": 1}
        with_patch = screener.SCREEN_UNSUPPORTED
        with_patch["这个信号不存在"] = "假装它需要取不到的数据"
        try:
            assert screener.unsupported_signals(rule) == ["这个信号不存在"]
        finally:
            del with_patch["这个信号不存在"]
