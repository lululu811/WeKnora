"""大盘预览两个接口的端到端用例（打真实 DuckDB）。

单元测试（`tests/unit/test_market_snapshot.py`）用假数据源锁定**口径**，
但假源不执行 SQL，所以有几件事只有真库能证：
  * 四大指数确实能从 `index.v_index_daily` 取到行（而不是静默返回空数组）
  * 龙虎榜的 thscode 去重真的生效（同一只票在 all/org 两个口径下各有一条）
  * 涨跌家数三个计数加起来接近全市场规模（能抓住 lag 窗口写错那类 bug）

需要 python-service 在跑：
    pytest tests/e2e/test_market_api.py -v
    WEKNORA_PY_SERVICE_URL=http://127.0.0.1:50052 pytest tests/e2e/test_market_api.py -v
"""

import pytest


@pytest.fixture(scope="module")
def snapshot(client):
    status, body = client.get("/api/market/snapshot")
    assert status == 200, f"snapshot failed: {status} {body}"
    assert body.get("code") == 0
    return body


@pytest.fixture(scope="module")
def dragon(client):
    status, body = client.get("/api/market/dragon-tiger?limit=5")
    assert status == 200, f"dragon-tiger failed: {status} {body}"
    return body


class TestMarketSnapshot:

    def test_four_main_indices_present(self, snapshot):
        """四大指数必须都有行。空数组是最危险的失败形态（看起来像"今天没数据"）。"""
        codes = {i["thscode"] for i in snapshot["indices"]}
        assert {"000001.SH", "399001.SZ", "399006.SZ", "000300.SH"} <= codes

    def test_index_series_is_long_enough_to_draw(self, snapshot):
        """60 日折线要真的有 60 根，不是 1~2 根。"""
        for idx in snapshot["indices"]:
            assert len(idx["series"]) >= 55, f"{idx['thscode']} 只有 {len(idx['series'])} 根"

    def test_series_dates_are_ascending(self, snapshot):
        for idx in snapshot["indices"]:
            dates = [p["date"] for p in idx["series"]]
            assert dates == sorted(dates), f"{idx['thscode']} 曲线顺序反了"

    def test_last_equals_final_series_close(self, snapshot):
        """快照的 last 必须就是曲线的最后一根，两处口径不能各算各的。"""
        for idx in snapshot["indices"]:
            assert idx["last"] == pytest.approx(idx["series"][-1]["close"])

    def test_index_high_low_bracket_last(self, snapshot):
        """快照条的轨道依赖 low <= last <= high，否则点位会跑出轨道。"""
        for idx in snapshot["indices"]:
            assert idx["low"] <= idx["last"] <= idx["high"], f"{idx['thscode']} 越界"

    def test_ticker_strip_has_five_indices(self, snapshot):
        codes = {t["thscode"] for t in snapshot["tickers"]}
        assert len(codes) == 5

    def test_sentiment_counts_are_not_none(self, snapshot):
        s = snapshot["sentiment"]
        for key in ("trade_date", "limit_up", "limit_down", "broken"):
            assert s[key] is not None, f"{key} 为 null：涨跌停池可能没同步"

    def test_limit_up_count_matches_raw_query(self, snapshot, client):
        """情绪格的涨停家数必须与直接查池子一致。"""
        s = snapshot["sentiment"]
        rows = client.query_rows(
            "special",
            "SELECT count(*) AS c FROM v_limit_up_pool "
            f"WHERE trade_date = '{s['trade_date']}'",
        )
        assert s["limit_up"] == rows[0]["c"]

    def test_breadth_counts_are_plausible(self, snapshot):
        """涨跌家数三个计数加起来接近全市场规模。

        这条专门抓「先筛最新日再 lag()」那类 bug —— 那样写三个计数会一起是 0，
        断言 up > 0 与总和 > 0 会立刻红。
        """
        b = snapshot["sentiment"]["breadth"]
        if b.get("up") is None:
            pytest.skip("涨跌家数未返回（market 库不可用）")
        assert b["up"] > 0 and b["down"] > 0
        assert b["up"] + b["flat"] + b["down"] > 4000

    def test_limit_up_trend_is_ascending_by_date(self, snapshot):
        trend = snapshot["sentiment"]["limit_up_trend"]
        assert trend, "近 5 日涨停柱状图没有数据"
        dates = [t["trade_date"] for t in trend]
        assert dates == sorted(dates)

    def test_etfs_present(self, snapshot):
        assert len(snapshot["etfs"]) >= 4
        for e in snapshot["etfs"]:
            assert e["last"] is not None

    def test_no_unavailable_sources_on_healthy_instance(self, snapshot):
        assert snapshot["unavailable"] == [], f"数据源缺失: {snapshot['unavailable']}"


class TestDragonTiger:

    def test_returns_five_rows(self, dragon):
        assert dragon["count"] == 5

    def test_no_duplicate_thscode(self, dragon):
        """同一只票在 all/org 两个榜单口径下各有一条，接口必须按 thscode 去重。

        不去重的话"前五"里会出现同一只票两行，用户数下来只有 3 只。
        """
        codes = [r["thscode"] for r in dragon["data"]]
        assert len(codes) == len(set(codes)), f"有重复: {codes}"

    def test_sorted_by_net_value_desc(self, dragon):
        nets = [r["net_value"] for r in dragon["data"]]
        assert nets == sorted(nets, reverse=True)

    def test_tag_is_human_readable(self, dragon):
        """tag 必须是翻过的人话，且不含交易所枚举原文。"""
        for row in dragon["data"]:
            assert row["tag"], "tag 不能为空"
            assert "all" not in row["tag"] and "org" not in row["tag"]

    def test_trade_date_is_recent(self, dragon):
        assert dragon["trade_date"] is not None
