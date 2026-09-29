"""End-to-end tests against a running python-service instance.

Run with the service up:

    pytest tests/e2e -v
    WEKNORA_PY_SERVICE_URL=http://127.0.0.1:50052 pytest tests/e2e -v

`TestServiceContract` pins what the service promises. The `Test*Regression`
classes pin behaviour that used to be broken — each names the defect it guards
against, so a future change that reintroduces it fails loudly instead of
silently producing inverted trading signals.
"""

import pytest

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
        status, body = client.post("/zettaranc/screen", {"strategy": "B1", "limit": 5})
        assert status == 200 and body["success"]
        assert body["strategy"] == "B1"
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
        assert "B1" in body["error"]


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
        调用方无从判断这次结果是不是全市场口径。现在两��数分开报。
        """
        universe = client.query_rows(
            "market",
            "SELECT count(*) AS c FROM dim_symbol WHERE asset_type = 'a-share'",
        )[0]["c"]
        status, body = client.post("/zettaranc/screen", {"strategy": "B1", "limit": 5})
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
        for strategy in ("B1", "B2", "SB1", "shaofu", "limit_up",
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

    def test_b1_only_returns_bullish_signals(self, client):
        status, body = client.post("/zettaranc/screen",
                                    {"strategy": "B1", "limit": 50})
        assert status == 200, body
        for s in (body.get("stocks") or []):
            dirs = set(s.get("matched_directions") or [])
            assert dirs == {"bullish"}, (
                f"{s['thscode']} 命中方向 {dirs}，B1 只该返回 bullish"
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
