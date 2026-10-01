"""End-to-end tests against a running python-service instance.

Run with the service up:

    pytest tests/e2e -v
    WEKNORA_PY_SERVICE_URL=http://127.0.0.1:50052 pytest tests/e2e -v

`TestServiceContract` pins what the service promises. The `Test*Regression`
classes pin behaviour that used to be broken — each names the defect it guards
against, so a future change that reintroduces it fails loudly instead of
silently producing inverted trading signals.
"""

import datetime

import pytest

from zettaranc import screener

LIQUID_CODE = "600519.SH"        # 贵州茅台, long history
THIN_CODE = "603448.SH"          # short history -> exercises the short-data path
NO_INDICATOR_CODE = "601059.SH"  # indicator rows exist but the values are NULL


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

class TestServiceContract:

    def test_health_reports_every_registered_datasource(self, client):
        status, body = client.get("/health")
        assert status in (200, 503)
        assert body["status"] in {"healthy", "degraded", "unhealthy"}
        for name in ("market", "financials", "fund", "special",
                     "futures", "index", "indicators"):
            assert name in body["datasources"], f"{name} not registered"

    def test_query_databases_lists_the_duckdb_files(self, client):
        status, body = client.get("/query/databases")
        assert status == 200
        assert set(body["databases"]) >= {"market", "financials", "indicators"}

    def test_query_executes_sql(self, client):
        assert client.query_rows("market", "SELECT 1 AS one") == [{"one": 1}]

    def test_query_binds_parameters(self, client):
        rows = client.query_rows(
            "market", "SELECT thscode FROM dim_symbol WHERE thscode = ?",
            limit=5, params=[LIQUID_CODE],
        )
        assert [r["thscode"] for r in rows] == [LIQUID_CODE]

    def test_query_appends_limit_when_absent(self, client):
        rows = client.query_rows("market", "SELECT thscode FROM dim_symbol", limit=3)
        assert len(rows) == 3

    def test_repeated_query_is_served_from_cache(self, client):
        client.post("/cache/clear")
        sql = "SELECT count(*) AS c FROM dim_symbol WHERE exchange = 'SZSE'"
        _, first = client.query("market", sql)
        _, second = client.query("market", sql)
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["data"] == first["data"]

    def test_cache_clear_resets_the_memory_cache(self, client):
        client.query_rows("market", "SELECT 2 AS two")
        assert client.get("/cache/stats")[1]["memory_size"] > 0
        assert client.post("/cache/clear")[1]["success"] is True
        assert client.get("/cache/stats")[1]["memory_size"] == 0

    def test_zettaranc_scan_returns_signals_and_summary(self, client):
        status, body = client.post(
            "/zettaranc/scan", {"thscode": LIQUID_CODE, "days": 30}
        )
        assert status == 200 and body["success"]
        result = body["result"]
        assert result["thscode"] == LIQUID_CODE
        assert 0 < result["days"] <= 30
        assert result["latest_date"], "the date column must survive the aliasing"
        assert isinstance(result["signals"], list)
        assert set(result["summary"]) >= {"verdict", "buy_signals", "sell_signals"}
        for signal in result["signals"]:
            assert signal["signal"] in {"bullish", "bearish", "neutral"}
            assert 0 < signal["strength"] <= 1

    def test_zettaranc_analyze_returns_all_four_sections(self, client):
        status, body = client.post(
            "/zettaranc/analyze", {"thscode": LIQUID_CODE, "days": 120}
        )
        assert status == 200 and body["success"]
        result = body["result"]
        assert set(result) >= {"trend", "volume", "chart_pattern", "levels"}
        assert result["levels"]["current_price"] > 0
        assert result["days"] <= result["requested_days"]

    def test_zettaranc_screen_returns_ranked_stocks(self, client):
        status, body = client.post("/zettaranc/screen", {"strategy": "oversold_combo", "limit": 5})
        assert status == 200 and body["success"]
        assert body["strategy"] == "oversold_combo"
        assert len(body["stocks"]) <= 5
        scores = [s["score"] for s in body["stocks"]]
        assert scores == sorted(scores, reverse=True)

    def test_zettaranc_rejects_out_of_range_days(self, client):
        for days in (5, 500):
            status, body = client.post(
                "/zettaranc/analyze", {"thscode": LIQUID_CODE, "days": days}
            )
            assert status == 422, body
            assert body["success"] is False
            assert "days" in body["error"]

    def test_zettaranc_rejects_unknown_strategy(self, client):
        status, body = client.post("/zettaranc/screen", {"strategy": "NOPE"})
        assert status == 422
        assert body["success"] is False
        assert "oversold_combo" in body["error"]


# ----------------------------------------------------------------------
# Regressions: behaviour that used to be wrong.
# ----------------------------------------------------------------------

class TestSqlInjectionRegression:
    """thscode used to be f-stringed into the SQL (BUG-8)."""

    def test_thscode_is_not_interpolated_into_sql(self, client):
        status, body = client.post(
            "/zettaranc/analyze",
            {"thscode": f"{LIQUID_CODE}' OR 1=1--", "days": 60},
        )
        assert status in (400, 422), (
            f"injected thscode reached the database (HTTP {status}): {body}"
        )
        assert body.get("success") is False
        assert "result" not in body

    def test_injected_thscode_cannot_read_another_instrument(self, client):
        _, clean = client.post("/zettaranc/analyze",
                               {"thscode": LIQUID_CODE, "days": 60})
        _, injected = client.post(
            "/zettaranc/analyze",
            {"thscode": f"{LIQUID_CODE}' OR 1=1--", "days": 60},
        )
        if injected.get("success") is False:
            return  # rejected outright, which is the correct outcome
        assert (injected["result"]["levels"]["current_price"]
                == clean["result"]["levels"]["current_price"]), (
            "the injected WHERE clause matched every instrument and the service "
            "analysed a different stock's data"
        )

    def test_scan_rejects_injected_thscode(self, client):
        status, body = client.post(
            "/zettaranc/scan",
            {"thscode": f"{LIQUID_CODE}' OR 1=1--", "days": 10},
        )
        assert status in (400, 422)
        assert body.get("success") is False

    def test_query_rejects_writes_and_multiple_statements(self, client):
        for sql in [
            "DELETE FROM dim_symbol",
            "SELECT 1; DROP TABLE dim_symbol",
            "PRAGMA database_list",
            "ATTACH '/tmp/evil.duckdb'",
        ]:
            status, body = client.query("market", sql)
            assert 400 <= status < 500, f"{sql!r} -> HTTP {status} {body}"


class TestQueryLimitRegression:
    """`if "LIMIT" not in sql.upper()` was bypassable by a comment (BUG-11)."""

    def test_limit_is_enforced_even_when_the_sql_mentions_limit(self, client):
        rows = client.query_rows(
            "market", "SELECT thscode FROM dim_symbol -- limit", limit=2
        )
        assert len(rows) == 2, f"LIMIT bypassed, got {len(rows)} rows"

    def test_limit_is_enforced_for_subqueries(self, client):
        rows = client.query_rows(
            "market",
            "SELECT thscode FROM (SELECT thscode FROM dim_symbol LIMIT 5000) t",
            limit=2,
        )
        assert len(rows) == 2, f"LIMIT bypassed, got {len(rows)} rows"

    def test_limit_is_enforced_for_a_cte(self, client):
        rows = client.query_rows(
            "market",
            "WITH t AS (SELECT thscode FROM dim_symbol) SELECT * FROM t -- limit",
            limit=2,
        )
        assert len(rows) == 2


class TestHealthHonestyRegression:
    """/health used to return 200 "healthy" no matter what (BUG-12)."""

    def test_health_status_reflects_datasource_health(self, client):
        status, body = client.get("/health")
        statuses = body["datasources"]
        if all(v == "healthy" for v in statuses.values()):
            assert status == 200 and body["status"] == "healthy"
        else:
            degraded = {k: v for k, v in statuses.items() if v != "healthy"}
            assert body["status"] != "healthy", (
                f"reports {body['status']} while {degraded} are degraded"
            )

    def test_zettaranc_health_really_queries_the_data(self, client):
        status, body = client.get("/zettaranc/health")
        assert status in (200, 503)
        assert body["success"] is (status == 200)


class TestShortHistoryRegression:
    """short histories used to be silently swallowed (BUG-13)."""

    def test_analyze_reports_the_number_of_bars_it_actually_analysed(self, client):
        bars = client.bar_count(THIN_CODE)
        if bars >= 120:
            pytest.skip(f"{THIN_CODE} now has {bars} bars")
        _, body = client.post("/zettaranc/analyze",
                              {"thscode": THIN_CODE, "days": 120})
        assert body["result"]["days"] == bars
        assert body["result"]["requested_days"] == 120

    def test_analyze_explains_which_sections_it_skipped(self, client):
        bars = client.bar_count(THIN_CODE)
        if bars >= 20:
            pytest.skip(f"{THIN_CODE} now has {bars} bars, trend is computed")
        _, body = client.post("/zettaranc/analyze",
                              {"thscode": THIN_CODE, "days": 120})
        result = body["result"]
        assert result["trend"] is None, (
            f"only {bars} bars so the trend section must be null, not absent"
        )
        assert result["complete"] is False
        assert any("trend" in reason for reason in result["insufficient_data"])


class TestScreenCoverageRegression:
    """the screener used to look at 100 arbitrary A-shares (BUG-14)."""

    def test_screen_covers_the_whole_market(self, client):
        """scanned 必须是**实际参与筛选的标的数**，不是清单长度。

        旧实现把 `scanned` 报成 `len(names)`（清单长度），于是哪怕指标
        快照被 max_rows 截掉一半，返回里的 scanned 仍写着完整的全市场数，
        调用方无从判断这次结果是不是全市场口径。现在两现在两个数分开报。
        """
        universe = client.query_rows(
            "market",
            "SELECT count(*) AS c FROM dim_symbol WHERE asset_type = 'a-share'",
        )[0]["c"]
        status, body = client.post("/zettaranc/screen", {"strategy": "oversold_combo", "limit": 5})
        assert status == 200
        assert body["scanned_from_universe"] == universe, (
            f"清单应有 {universe} 只，实报 {body['scanned_from_universe']}"
        )
        assert body["scanned"] <= body["scanned_from_universe"], (
            f"scanned({body['scanned']}) 不该超过清单({body['scanned_from_universe']})"
        )
        # 差值必须被显式解释，不能凭空消失
        gap = body["scanned_from_universe"] - body["scanned"]
        assert gap == body["no_indicator_count"], (
            f"少了 {gap} 只，但 no_indicator_count 只报了 {body['no_indicator_count']}"
        )
        if gap:
            assert body["warnings"], "有票没参与筛选却没有任何说明"
        assert body["truncated"] is False, (
            "全市场 × 10 天不该撞上 100k 行上限；撞上了就必须报 truncated"
        )

    def test_screen_spans_more_than_one_exchange(self, client):
        _, body = client.post("/zettaranc/screen", {"strategy": "SB1", "limit": 50})
        codes = [s["thscode"] for s in body["stocks"]]
        if len(codes) < 10:
            pytest.skip("not enough matches to judge the sample")
        assert len({c.split(".")[-1] for c in codes}) > 1, (
            "every match came from one exchange"
        )

    def test_every_strategy_is_recognised(self, client):
        """`anomaly` used to be structurally dead: two of its rules emitted
        `neutral` signals and the third compared two floats for equality."""
        for strategy in ("oversold_combo", "B2", "SB1", "shaofu", "limit_up",
                         "anomaly", "volatility_spike"):
            status, body = client.post("/zettaranc/screen",
                                        {"strategy": strategy, "limit": 5})
            assert status == 200, body
            assert "matched" in body

    def test_anomaly_only_returns_bearish_signals(self, client):
        """`anomaly` 过去开了 allow_neutral，而筛选侧判据是
        `signal == "bullish" or allow_neutral` —— 整个条件对所有方向短路成真，
        bearish 信号照收；score 又累加被夹到 [0,1] 的无符号 strength，
        于是命中 3 个看跌信号的票稳定排在命中 1 个中性信号的票之前。
        策略顶着"异常检测"的名字选出一批看跌票，而方向本该是它的重点。
        """
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "anomaly", "limit": 50})
        assert status == 200, body
        stocks = body.get("stocks") or []
        if not stocks:
            pytest.skip("当前无命中，无法判断方向")
        for s in stocks:
            dirs = set(s.get("matched_directions") or [])
            assert dirs == {"bearish"}, (
                f"{s['thscode']} 命中方向 {dirs}，anomaly 只该返回 bearish："
                f"{s.get('matched_signals')}"
            )

    def test_oversold_combo_only_returns_bullish_signals(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "oversold_combo", "limit": 50})
        assert status == 200, body
        for s in (body.get("stocks") or []):
            dirs = set(s.get("matched_directions") or [])
            assert dirs == {"bullish"}, (
                f"{s['thscode']} 命中方向 {dirs}，oversold_combo 只该返回 bullish"
            )


class TestMissingIndicatorDataRegression:
    """COALESCE(NULL, 0) used to fabricate oversold buy signals (BUG-4)."""

    def test_scan_does_not_emit_signals_derived_from_null_indicators(self, client):
        raw = client.latest_indicator_row(NO_INDICATOR_CODE)
        if raw is None or raw.get("momentum_rsi_6") is not None:
            pytest.skip(f"{NO_INDICATOR_CODE} now has real indicator data")
        status, body = client.post("/zettaranc/scan",
                                   {"thscode": NO_INDICATOR_CODE, "days": 10})
        assert status == 200
        result = body["result"]
        assert result["data_complete"] is False
        assert result["missing_indicators"]
        names = {s["name"] for s in result["signals"]}
        assert not names & {"RSI6超卖", "MFI超卖", "Williams%R超买", "Williams%R超卖"}, (
            f"NULL indicators were reported as real signals: {sorted(names)}"
        )
        assert result["summary"]["verdict"] != "偏多"

    def test_bollinger_status_is_not_fabricated_from_null_bands(self, client):
        raw = client.latest_indicator_row(THIN_CODE)
        if raw is None or raw.get("volatility_bbands_20_2_0_upper") is not None:
            pytest.skip(f"{THIN_CODE} now has real Bollinger bands")
        status, body = client.post("/zettaranc/analyze",
                                   {"thscode": THIN_CODE, "days": 120})
        assert status == 200
        bollinger = body["result"]["chart_pattern"]["bollinger_status"]
        assert bollinger is None, (
            f"NULL bands must not produce a 布林带 status block; got {bollinger}"
        )


class TestErrorContractRegression:
    """failures used to be reported as HTTP 200 (BUG-15)."""

    def test_errors_are_not_reported_as_http_200(self, client):
        for path, payload in [
            ("/query/", {"db": "does-not-exist", "sql": "SELECT 1"}),
            ("/query/", {"db": "market", "sql": "SELCT 1"}),
            ("/zettaranc/scan", {"thscode": "999999.XX", "days": 10}),
            ("/zettaranc/screen", {"strategy": "NOPE"}),
            ("/zettaranc/analyze", {"thscode": LIQUID_CODE, "days": 3}),
        ]:
            status, body = client.post(path, payload)
            assert 400 <= status < 500, (
                f"{path} {payload} -> HTTP {status} {body}; a failure that "
                "returns 200 is invisible to ingress and monitoring"
            )
            assert body["success"] is False
            assert body["error"], body


class TestLimitUpUsesRealData:
    """`limit_up` 过去只匹配 CMF/ADX/Aroon 三个代理指标，从没读过
    special.v_limit_up_pool —— 名字叫「涨停选股」，筛的却是"资金流入且趋势向上"。
    库里 29,657 行真实涨停记录一直躺着没用。"""

    def test_pool_restriction_is_disclosed(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "limit_up", "limit": 10})
        assert status == 200, body
        assert body["pool_restricted"] is True
        assert body["pool_size"], "必须报出真实涨停池的规模"
        # 池子只有几十只，选出来的数不该超过池子
        assert body["universe"] <= body["pool_size"], (
            f"候选集 {body['universe']} 超过了涨停池 {body['pool_size']}"
        )
        assert any("涨停" in w for w in body["warnings"]), (
            f"候选集被涨停池限制这件事必须写在 warnings 里：{body['warnings']}"
        )

    def test_every_result_carries_real_limit_up_provenance(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "limit_up", "limit": 50})
        assert status == 200, body
        stocks = body.get("stocks") or []
        if not stocks:
            pytest.skip("当前无涨停命中")
        for s in stocks:
            lu = s.get("limit_up")
            assert lu, f"{s['thscode']} 选出来了却没有涨停记录"
            assert lu.get("source") == "special.v_limit_up_pool"
            assert lu.get("trade_date"), "涨停日不能为空"

    def test_longest_streak_is_reported_not_the_earliest_day(self, client):
        """回归：归并时按代码覆盖会让 5 连板显示成 1 连板。

        基准口径必须与 SQL 的窗口一致：SQL 取的是最近
        `LIMIT_UP_LOOKBACK_DAYS` 个**自然日**内的涨停记录，不是全历史最大值。
        拿全历史 max 当基准会误报 —— 一只票 6 周前有过 6 板，本周只有 4 板，
        返回 4 板才是对的。
        """
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "limit_up", "limit": 200})
        assert status == 200, body

        # DuckDB 不接受 `INTERVAL ? DAY`（会在 `?` 处 Parser Error），
        # 所以基准 SQL 与生产代码一样用字面量窗口。
        cutoff = client.query_rows(
            "special",
            "SELECT CAST(MAX(trade_date) - INTERVAL "
            f"{screener.LIMIT_UP_LOOKBACK_DAYS} DAY AS VARCHAR) AS c "
            "FROM v_limit_up_pool",
        )[0]["c"]
        pool = client.query_rows(
            "special",
            "SELECT thscode, MAX(continue_day_cnt) AS c FROM v_limit_up_pool "
            f"WHERE trade_date >= CAST('{cutoff}' AS DATE) "
            "GROUP BY thscode HAVING c >= 3",
        )
        if not pool:
            pytest.skip(f"最近 {screener.LIMIT_UP_LOOKBACK_DAYS} 天没有 3 板以上个股")
        expect = {r["thscode"]: r["c"] for r in pool}
        for s in (body.get("stocks") or []):
            if s["thscode"] not in expect:
                continue
            got = (s.get("limit_up") or {}).get("continue_day_cnt")
            assert got == expect[s["thscode"]], (
                f"{s['thscode']} 近 {screener.LIMIT_UP_LOOKBACK_DAYS} 天内最高 "
                f"{expect[s['thscode']]} 板，返回却是 {got} 板"
            )


class TestScreenProvenance:
    """选股结果必须能追回到源表与截止日。

    工具层（internal/agent/tools/hithink_finance）每一行结果都带 `_source`，
    这里是对齐：一次选股返回几十只票，模型据此回答"凭什么说它超卖"时
    要有可引用的表名和日期，否则和凭空断言无异。
    """

    def test_reports_every_source_table(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "oversold_combo", "limit": 3})
        assert status == 200, body
        src = body.get("sources")
        assert src, "返回里必须有 sources"
        assert "indicators.v_indicators_daily" in src["signals"]
        assert "market.dim_symbol" in src["universe"]

    def test_price_source_is_listed_only_when_it_was_merged(self, client):
        """价量是可选维度。取不到时不能列进去 —— 列出没用的源表
        会让人以为形态信号用到了价量（而放量突破正是靠它）。"""
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "vol_breakout", "limit": 3})
        assert status == 200, body
        src = body["sources"]
        if body["price_merged_rows"] > 0:
            assert "market.v_daily_qfq" in src["signals"]
        else:
            assert "market.v_daily_qfq" not in src["signals"]

    def test_reports_the_as_of_dates(self, client):
        """截止日必须报。指标是 3 个交易日前的快照时，
        「当前超卖」和「快照当天的超卖」不是一回事。"""
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "oversold_combo", "limit": 3})
        assert status == 200, body
        src = body["sources"]
        assert src["indicator_as_of"], "必须报指标截止日"
        assert src["lookback_days"] == screener.LOOKBACK_DAYS

    def test_stale_data_is_called_out(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "oversold_combo", "limit": 3})
        assert status == 200, body
        as_of = datetime.date.fromisoformat(
            body["sources"]["indicator_as_of"][:10])
        lag = (datetime.date.today() - as_of).days
        if lag > 7:
            assert any("截止" in w for w in body["warnings"]), (
                f"数据已陈旧 {lag} 天却没有任何提示：{body['warnings']}"
            )

class TestFourBricks:
    """四块砖端点（2026-10-01 新增）。

    此前四块砖只在工作台前端算得出，服务端取不到，于是 agent 被要求对
    "XX 现在四块砖什么状态"一律回答"算不出来"。这里守住新端点：它必须
    返回**真实数值**，而不是又一个空壳。
    """

    def test_returns_a_real_reading(self, client):
        status, body = client.post("/zettaranc/four-bricks",
                                    {"thscode": LIQUID_CODE, "days": 120})
        assert status == 200, body
        r = body["result"]
        assert r["insufficient"] is False
        assert r["thscode"] == LIQUID_CODE
        # 四项布尔 + 总分 + 红砖数，缺一不可
        for k in ("bull1", "bull2", "bull3", "bull4"):
            assert isinstance(r[k], bool), f"{k} 不是布尔"
        assert -4 <= r["score"] <= 4
        assert r["bull_count"] == sum(1 for k in ("bull1", "bull2", "bull3", "bull4")
                                      if r[k])
        # score 必须等于四项之和，否则"四砖全红(+4)"这类标签会自相矛盾
        expected = sum(1 if r[k] else -1
                       for k in ("bull1", "bull2", "bull3", "bull4"))
        assert r["score"] == expected, (
            f"score={r['score']} 与四项之和 {expected} 不一致 —— "
            "标签会和分数打架"
        )

    def test_reports_the_periods_it_used(self, client):
        """周期必须报出来：换周期会让历史读数不可比，而调用方无从得知。"""
        status, body = client.post("/zettaranc/four-bricks",
                                    {"thscode": LIQUID_CODE, "days": 120})
        assert status == 200, body
        p = body["result"]["periods"]
        assert p["short"] == 5
        assert p["trend"] == [10, 14]
        assert p["bbi"] == [3, 6, 12, 24]

    def test_refuses_to_invent_trading_advice(self, client):
        """数值状态可以有，战法解释不行 —— 那是知识库的事，两套说法必然漂移。

        只查 `text`（状态标签）。`note` 里出现"买点"是在**劝阻**生成它，
        那是正确的用法，不该被这条测试当成违规。
        """
        status, body = client.post("/zettaranc/four-bricks",
                                    {"thscode": LIQUID_CODE, "days": 120})
        assert status == 200, body
        r = body["result"]
        for banned in ("买入", "卖出", "买点", "卖点", "建议", "止损"):
            assert banned not in r.get("text", ""), (
                f"状态标签里出现了战法建议 {banned!r} —— 解释应来自知识库"
            )

    def test_says_so_when_bars_are_insufficient(self, client):
        """历史不够时必须明说缺多少，不能返回一个看起来正常的读数。"""
        status, body = client.post("/zettaranc/four-bricks",
                                    {"thscode": THIN_CODE, "days": 2})
        assert status == 200, body
        r = body["result"]
        if r["insufficient"]:
            assert r["actual_bars"] < r["required_bars"]
            assert "根 K 线" in r["note"]

    def test_rejects_a_malformed_symbol(self, client):
        status, _ = client.post("/zettaranc/four-bricks", {"thscode": "not-a-code"})
        assert status in (400, 404, 422)
