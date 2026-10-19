"""
大盘预览两个只读接口的单元测试：`/api/market/snapshot` 与 `/api/market/dragon-tiger`。

用假数据源（`registry` 打桩）而不是连本地 DuckDB，理由有两个：
  * 这些接口的价值全在**口径**（哪个日期、空了怎么办、去重、排序），
    不在 SQL 能不能跑通 —— SQL 跑不通在 e2e 层就能发现；
  * 本地库每天在变，写死"涨停 56 家"的断言明天就红。

真正打真实 DuckDB 的覆盖放在 `tests/e2e/`，这里只保证契约。

被测函数全部从 `main` 直接导入，所以这些用例要求 python-service 的依赖已安装
（与 `test_read_only_sql.py` 同一前提）。
"""

import asyncio
import datetime as dt
import re

import pytest

import main as svc
from datasources import registry


class FakeSource:
    """按 SQL 关键字分派的假数据源。只认调用方写的分支，不做通用 SQL 解析。"""

    def __init__(self, routes):
        # routes: [(匹配正则, 返回值), ...]，按顺序命中第一个
        self.routes = routes
        self.calls = []

    async def execute(self, query, params=None):
        self.calls.append((query, list(params or [])))
        for pattern, payload in self.routes:
            if re.search(pattern, query, re.IGNORECASE | re.DOTALL):
                return payload() if callable(payload) else payload
        return []


@pytest.fixture
def fake_registry(monkeypatch):
    """把 `registry.get` 换成查表版本，并在用例结束后复原。"""

    def install(sources: dict):
        def _get(name):
            return sources.get(name)

        monkeypatch.setattr(registry, "get", _get)

    yield install
    # monkeypatch 自动复原


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# 龙虎榜
# --------------------------------------------------------------------------

def test_dragon_tiger_sql_dedupes_by_thscode(fake_registry):
    """去重发生在 SQL 里（row_number 按 thscode 分区），所以这里断言 SQL 文本。

    假数据源不执行 SQL，只回放预置行 —— 用它去验"结果里有没有重复"等于验
    自己写的假数据。真正跑 DuckDB 的断言在 `tests/e2e/test_market_api.py`。
    """
    src = FakeSource([(r"FROM \(\s*SELECT", [])])
    fake_registry({"special": src})

    _run(svc.market_dragon_tiger(limit=5))

    sql = src.calls[0][0]
    assert "PARTITION BY thscode" in sql, "去重窗口必须按 thscode 分区"
    assert "row_number()" in sql
    assert "rn = 1" in sql


def test_dragon_tiger_org_net_null_is_not_zero(fake_registry):
    """无机构席位数据时 org_net_value 是 null，不是 0。

    0 会被读成"机构净卖出 0 元"，那是与"没有数据"完全不同的结论。
    """
    rows = [
        {"thscode": "001246.SZ", "name": "力勤资源", "trade_date": dt.date(2026, 9, 30),
         "board_type": "all", "net_value": 504127136.94, "org_net_value": None, "range_days": 1},
    ]
    fake_registry({"special": FakeSource([(r"FROM \(\s*SELECT", rows)])})

    out = _run(svc.market_dragon_tiger(limit=5))

    assert out["data"][0]["org_net_value"] is None
    assert out["data"][0]["net_value"] == pytest.approx(504127136.94)


def test_dragon_tiger_tag_uses_only_locally_available_fields(fake_registry):
    """tag 只能由 board_type + range_days 拼出，不得编造交易所上榜原因。"""
    rows = [
        {"thscode": "301218.SZ", "name": "华是科技", "trade_date": dt.date(2026, 9, 30),
         "board_type": "all", "net_value": 1.0, "org_net_value": 1.0, "range_days": 3},
    ]
    fake_registry({"special": FakeSource([(r"FROM \(\s*SELECT", rows)])})

    out = _run(svc.market_dragon_tiger(limit=5))
    tag = out["data"][0]["tag"]

    assert tag == "全部榜 · 3 日累计"
    for fake_reason in ("偏离", "换手率", "涨幅限制"):
        assert fake_reason not in tag


def test_dragon_tiger_missing_source_raises_503(fake_registry):
    """数据源整个不在时必须 503，不能返回空数组假装"今天没有龙虎榜"。"""
    from fastapi import HTTPException

    fake_registry({})
    with pytest.raises(HTTPException) as exc:
        _run(svc.market_dragon_tiger(limit=5))
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------
# 快照：指数
# --------------------------------------------------------------------------

def test_snapshot_index_series_is_chronological_and_has_prev_close(fake_registry):
    """曲线按时间正序返回，且 last/prev_close 来自倒数第一、第二根。

    前端直接画线、再用 prev_close 画昨收虚线，所以顺序反了图就是倒的。
    """
    bars = [
        {"thscode": "000001.SH", "trade_date": dt.date(2026, 9, 28), "open": 1.0,
         "high": 2.0, "low": 0.5, "close": 1.5},
        {"thscode": "000001.SH", "trade_date": dt.date(2026, 9, 29), "open": 1.5,
         "high": 2.2, "low": 1.4, "close": 1.8},
        {"thscode": "000001.SH", "trade_date": dt.date(2026, 9, 30), "open": 1.8,
         "high": 2.4, "low": 1.7, "close": 2.0},
    ]
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", bars)])})

    out = _run(svc.market_snapshot(days=60))
    idx = out["indices"][0]

    assert idx["last"] == pytest.approx(2.0)
    assert idx["prev_close"] == pytest.approx(1.8)
    assert [p["date"] for p in idx["series"]] == ["2026-09-28", "2026-09-29", "2026-09-30"]


def test_snapshot_change_pct_null_when_only_one_bar(fake_registry):
    """只有一根 K 线（新股/首日）没有"前收"，涨跌幅必须是 null 而非 0。"""
    bars = [
        {"thscode": "000001.SH", "trade_date": dt.date(2026, 9, 30), "open": 1.0,
         "high": 2.0, "low": 0.5, "close": 2.0},
    ]
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", bars)])})

    out = _run(svc.market_snapshot(days=60))
    assert out["indices"][0]["change_pct"] is None


def test_snapshot_missing_index_is_omitted_not_zeroed(fake_registry):
    """库里没有的指数整条不出现 —— 不能用 0 顶替（0 是"真的等于零"）。"""
    bars = [
        {"thscode": "000001.SH", "trade_date": dt.date(2026, 9, 30), "open": 1.0,
         "high": 2.0, "low": 0.5, "close": 2.0},
    ]
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", bars)])})

    out = _run(svc.market_snapshot(days=60))
    codes = [i["thscode"] for i in out["indices"]]

    assert codes == ["000001.SH"]
    assert all(i["last"] is not None for i in out["indices"])


def test_snapshot_queries_index_daily_not_index_latest(fake_registry):
    """指数快照必须打 v_index_daily。

    `v_index_latest` 只装 .TI 同花顺板块（848 行全是 881xxx.TI），四大指数
    一个都没有 —— 查它会得到空数组，页面显示成"今天没数据"而不是"路由错了"。
    """
    src = FakeSource([(r"JOIN LATERAL", [])])
    fake_registry({"index": src})

    _run(svc.market_snapshot(days=60))

    joined = " ".join(q for q, _ in src.calls)
    assert "v_index_daily" in joined
    assert "v_index_latest" not in joined


def test_snapshot_breadth_sql_lags_over_two_days_not_one(fake_registry):
    """涨跌家数必须在"最近两个交易日"上算 lag。

    先 `WHERE date = max(date)` 再 lag() 会让每个分区只剩一行、prev 恒为 NULL，
    三个计数一起变成 0（实测过），看着像"全市场平盘"。
    """
    captured = []

    class CapturingSource(FakeSource):
        async def execute(self, query, params=None):
            captured.append(query)
            if "last2" in query:
                return [{"up": 2569, "flat": 168, "down": 2819}]
            return []

    # 情绪块要先有 trade_date 才会走到涨跌家数那一步，所以 special 要回一个日期。
    special = FakeSource([
        (r"MAX\(trade_date\)", [{"d": dt.date(2026, 9, 30)}]),
        (r"count\(\*\).*limit_down", [{"limit_up": 68, "limit_down": 9, "broken": 21}]),
        (r"continue_day_cnt DESC", [{"name": "天普股份", "continue_day_cnt": 6}]),
        (r"GROUP BY trade_date ORDER BY", []),
    ])
    fake_registry({
        "index": FakeSource([(r"JOIN LATERAL", [])]),
        "special": special,
        "market": CapturingSource([]),
    })

    out = _run(svc.market_snapshot(days=60))

    breadth_sql = [q for q in captured if "last2" in q]
    assert breadth_sql, "未执行涨跌家数查询"
    assert "LIMIT 2" in breadth_sql[0]
    assert out["sentiment"]["breadth"] == {"up": 2569, "flat": 168, "down": 2819}


# --------------------------------------------------------------------------
# 快照：情绪与 ETF
# --------------------------------------------------------------------------

def test_snapshot_broken_rate_null_when_no_limit_activity(fake_registry):
    """涨停与炸板都是 0 时炸板率是 null，不是 0%。

    "0% 的炸板率"是一个结论，"算不出来"是另一回事。
    """
    src = FakeSource([
        (r"MAX\(trade_date\).*FROM v_limit_up_pool", [{"d": dt.date(2026, 9, 30)}]),
        (r"count\(\*\).*limit_down", [{"limit_up": 0, "limit_down": 0, "broken": 0}]),
        (r"continue_day_cnt DESC", []),
        (r"GROUP BY trade_date ORDER BY", []),
    ])
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", [])]), "special": src})

    out = _run(svc.market_snapshot(days=60))
    assert out["sentiment"]["broken_rate"] is None


def test_snapshot_broken_rate_uses_limit_up_plus_broken(fake_registry):
    """炸板率分母是 涨停 + 炸板（不是全市场家数）。"""
    src = FakeSource([
        (r"MAX\(trade_date\).*FROM v_limit_up_pool", [{"d": dt.date(2026, 9, 30)}]),
        (r"count\(\*\).*limit_down", [{"limit_up": 68, "limit_down": 9, "broken": 21}]),
        (r"continue_day_cnt DESC", [{"name": "天普股份", "continue_day_cnt": 6}]),
        (r"GROUP BY trade_date ORDER BY", []),
    ])
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", [])]), "special": src})

    out = _run(svc.market_snapshot(days=60))
    # 21 / (68 + 21) = 23.6%
    assert out["sentiment"]["broken_rate"] == pytest.approx(23.6, abs=0.05)


def test_snapshot_sentiment_empty_when_no_pool_data(fake_registry):
    """涨跌停池没同步时情绪块为 null，不是 0。"""
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", [])]), "special": FakeSource([])})

    out = _run(svc.market_snapshot(days=60))
    s = out["sentiment"]

    assert s["trade_date"] is None
    assert s["limit_up"] is None
    assert s["limit_down"] is None
    assert s["broken"] is None
    assert s["max_streak"] is None
    assert s["limit_up_trend"] == []


def test_snapshot_reports_unavailable_sources(fake_registry):
    """某个库整个缺失时记进 unavailable，前端可据此提示"数据未同步"。"""
    fake_registry({"index": FakeSource([(r"JOIN LATERAL", [])])})

    out = _run(svc.market_snapshot(days=60))

    assert "special" in out["unavailable"]
    assert "fund" in out["unavailable"]


def test_snapshot_etf_preserves_declared_order(fake_registry):
    """ETF 按常量顺序输出，不按数据库返回顺序 —— 那是设计稿里那六行的次序。"""
    rows = [
        {"thscode": "512100.SH", "name": "中证1000ETF南方", "trade_date": dt.date(2026, 10, 2),
         "last_price": 2.971, "change_pct": -0.3},
        {"thscode": "510300.SH", "name": "沪深300ETF华泰柏瑞", "trade_date": dt.date(2026, 10, 2),
         "last_price": 4.432, "change_pct": 0.36},
    ]
    fake_registry({
        "index": FakeSource([(r"JOIN LATERAL", [])]),
        "fund": FakeSource([(r"FROM v_etf_latest", rows)]),
    })

    out = _run(svc.market_snapshot(days=60))
    codes = [e["thscode"] for e in out["etfs"]]

    # 库里只返回了两条，输出也只保留这两条，但相对次序必须跟常量一致
    assert codes == ["510300.SH", "512100.SH"]


def test_snapshot_never_raises_when_a_source_explodes(fake_registry):
    """某个查询抛异常时降级为该块为空，整页仍然 200。"""
    class ExplodingSource:
        async def execute(self, query, params=None):
            raise RuntimeError("Binder Error: 视图不存在")

    fake_registry({
        "index": FakeSource([(r"JOIN LATERAL", [])]),
        "special": ExplodingSource(),
        "fund": ExplodingSource(),
    })

    out = _run(svc.market_snapshot(days=60))  # 不抛异常即为通过

    assert out["code"] == 0
    assert "special" in out["unavailable"]


# --------------------------------------------------------------------------
# 大宗商品期股联动
# --------------------------------------------------------------------------

def test_commodities_returns_items_with_change_pct_and_linked_stocks(fake_registry):
    """大宗商品接口返回涨跌幅、关联股票列表及品类。"""
    rows = [
        {
            "variety_code": "CU",
            "name": "沪铜主力",
            "thscode": "CU2611.SHF",
            "close_price": 80500.0,
            "prev_close": 80000.0,
            "change_pct": 0.625,
            "volume": 50000.0,
            "turnover": 4025000000.0,
            "trade_date": dt.date(2026, 10, 12),
        },
        {
            "variety_code": "AU",
            "name": "沪金主力",
            "thscode": "AU2612.SHF",
            "close_price": 600.0,
            "prev_close": 606.0,
            "change_pct": -0.99,
            "volume": 20000.0,
            "turnover": 1200000000.0,
            "trade_date": dt.date(2026, 10, 12),
        },
    ]
    fake_registry({
        "futures": FakeSource([(r"FROM v_futures_latest", rows)]),
    })

    out = _run(svc.market_commodities())
    assert out["ok"] is True
    assert len(out["items"]) == 2

    cu = next(i for i in out["items"] if i["variety"] == "CU")
    assert cu["name"] == "沪铜"
    assert cu["category"] == "有色金属"
    assert cu["change_pct"] == 0.625
    assert "紫金矿业" in cu["linked_stocks"]

    au = next(i for i in out["items"] if i["variety"] == "AU")
    assert au["name"] == "沪金"
    assert au["change_pct"] == -0.99
    assert "山东黄金" in au["linked_stocks"]


def test_commodities_handles_missing_futures_source(fake_registry):
    """futures 源不可用时优雅降级。"""
    fake_registry({})
    out = _run(svc.market_commodities())
    assert out["ok"] is False
    assert "futures" in out["unavailable"]


# --------------------------------------------------------------------------
# 个股公募与 ETF 机构持仓穿透
# --------------------------------------------------------------------------

def test_fund_holdings_returns_summary_and_items(fake_registry):
    """个股机构重仓穿透接口返回汇总指标与明细。"""
    detail_rows = [
        {
            "fund_thscode": "159163.SZ",
            "fund_name": "有色ETF泰康",
            "fund_category": "ETF",
            "manager_name": None,
            "hold_ratio": 8.56,
            "position_count": 653200.0,
            "security_market_value_rate_pct": 0.086,
            "period_increase_rate_pct": -0.7,
            "investment_rank": 1,
        },
        {
            "fund_thscode": "000001.OF",
            "fund_name": "华夏成长混合",
            "fund_category": "主动权益",
            "manager_name": "王经理",
            "hold_ratio": 4.2,
            "position_count": 320000.0,
            "security_market_value_rate_pct": 0.042,
            "period_increase_rate_pct": 1.5,
            "investment_rank": 3,
        },
    ]
    summary_rows = [
        {
            "total_funds": 2,
            "total_hold_ratio": 12.76,
            "avg_nav_ratio": 0.064,
            "increase_count": 1,
            "decrease_count": 1,
            "unchanged_count": 0,
        }
    ]
    fake_registry({
        "fund": FakeSource([
            (r"ORDER BY h\.hold_ratio DESC", detail_rows),
            (r"SELECT COUNT\(\*\) AS total_funds", summary_rows),
        ]),
    })

    out = _run(svc.market_fund_holdings(thscode="601899.SH", limit=10))
    assert out["ok"] is True
    assert out["stock_thscode"] == "601899.SH"
    assert out["summary"]["total_funds"] == 2
    assert out["summary"]["total_hold_ratio"] == 12.76
    assert out["summary"]["increase_count"] == 1
    assert out["summary"]["decrease_count"] == 1
    assert len(out["items"]) == 2
    assert out["items"][0]["fund_name"] == "有色ETF泰康"
    assert out["items"][0]["fund_category"] == "ETF"
    assert out["items"][1]["manager_name"] == "王经理"


def test_fund_holdings_handles_missing_fund_source(fake_registry):
    """fund 源缺失时返回标准错误结构。"""
    fake_registry({})
    out = _run(svc.market_fund_holdings(thscode="601899.SH"))
    assert out["ok"] is False
    assert "fund" in out["unavailable"]


# --------------------------------------------------------------------------
# 连板梯队详情、异动归因与炸板跌停监控
# --------------------------------------------------------------------------

def test_ladder_detail_returns_max_board_and_grouped_items(fake_registry):
    """连板梯队详情接口返回最高板及按板位分组的股票。"""
    rows = [
        {
            "trade_date": dt.date(2026, 10, 12),
            "continue_day_cnt": 6,
            "thscode": "002242.SZ",
            "name": "九阳股份",
            "last_price": 16.64,
            "limit_up_time": "09:25",
            "seal_money": 59186816.0,
        },
        {
            "trade_date": dt.date(2026, 10, 12),
            "continue_day_cnt": 2,
            "thscode": "605287.SH",
            "name": "德才股份",
            "last_price": 44.2,
            "limit_up_time": "09:25",
            "seal_money": 239409300.0,
        },
    ]
    fake_registry({
        "special": FakeSource([(r"FROM v_limit_up_pool", rows)]),
    })

    out = _run(svc.market_ladder_detail(min_board=2, limit=50))
    assert out["ok"] is True
    assert out["max_board"] == 6
    assert out["total_count"] == 2
    assert "6板" in out["by_level"]
    assert "2板" in out["by_level"]
    assert out["by_level"]["6板"][0]["name"] == "九阳股份"


def test_anomaly_reasons_returns_content_and_keywords(fake_registry):
    """异动归因接口解析关键词与事实陈述。"""
    rows = [
        {
            "capture_date": dt.date(2026, 10, 12),
            "thscode": "000002.SZ",
            "stock_name": "万科A",
            "tag_name": "大跌",
            "analysis_content": "1、房地产板块下跌；2、亏损扩大。",
            "keyword_list": '["房地产板块下跌", "亏损扩大"]',
        }
    ]
    fake_registry({
        "special": FakeSource([(r"FROM v_anomaly_list", rows)]),
    })

    out = _run(svc.market_anomaly_reasons(thscode="000002.SZ", limit=5))
    assert out["ok"] is True
    assert out["count"] == 1
    item = out["items"][0]
    assert item["stock_name"] == "万科A"
    assert item["tag_name"] == "大跌"
    assert item["keywords"] == ["房地产板块下跌", "亏损扩大"]


def test_limit_break_down_returns_both_pools(fake_registry):
    """炸板与跌停池接口返回两个列表。"""
    break_rows = [
        {
            "trade_date": dt.date(2026, 10, 12),
            "thscode": "600127.SH",
            "name": "金健米业",
            "last_price": 13.9,
            "open_times": 7,
            "price_change_ratio": -8.43,
        }
    ]
    down_rows = [
        {
            "trade_date": dt.date(2026, 10, 12),
            "thscode": "002285.SZ",
            "name": "世联行",
            "last_price": 2.94,
            "price_change_ratio": -10.09,
            "first_limit_time": "09:50",
            "last_limit_time": "14:49",
        }
    ]
    fake_registry({
        "special": FakeSource([
            (r"FROM v_limit_break_pool", break_rows),
            (r"FROM v_limit_down_pool", down_rows),
        ]),
    })

    out = _run(svc.market_limit_break_down(limit=20))
    assert out["ok"] is True
    assert out["break_count"] == 1
    assert out["down_count"] == 1
    assert out["break_pool"][0]["open_times"] == 7
    assert out["down_pool"][0]["name"] == "世联行"


def test_dividend_calendar_returns_items_and_yield(fake_registry):
    """分红日历接口返回除息事件与预估股息率。"""
    div_rows = [
        {
            "thscode": "600519.SH",
            "name": "贵州茅台",
            "ex_date": dt.date(2026, 6, 26),
            "dividend_per_share": 28.02,
            "per_share_bonus": 0.0,
            "allotment_ratio": 0.0,
            "close": 1500.0,
            "div_yield_pct": 1.87,
        },
        {
            "thscode": "002763.SZ",
            "name": "汇洁股份",
            "ex_date": dt.date(2026, 5, 15),
            "dividend_per_share": 0.8,
            "per_share_bonus": 0.0,
            "allotment_ratio": 0.0,
            "close": 7.48,
            "div_yield_pct": 10.70,
        },
    ]
    fake_registry({
        "market": FakeSource([
            (r"FROM raw_adjustment_events", div_rows),
        ]),
    })

    out = _run(svc.market_dividend_calendar(sort_by="yield", limit=10))
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["items"][0]["thscode"] == "600519.SH"
    assert out["items"][0]["dividend_per_share"] == 28.02
    assert out["items"][1]["dividend_yield"] == 10.70


def test_dividend_calendar_handles_missing_market(fake_registry):
    """market 库缺失时返回标准降级结构。"""
    fake_registry({})
    out = _run(svc.market_dividend_calendar())
    assert out["ok"] is False
    assert "market" in out["unavailable"]


def test_hot_stocks_returns_skyrocket_and_rank_items(fake_registry):
    """热度榜接口返回飙升黑马标的与排名异动。"""
    rows = [
        {
            "capture_date": dt.date(2026, 10, 13),
            "period": "day",
            "rank": 29,
            "thscode": "600391.SH",
            "ticker": "600391",
            "name": "航发科技",
            "heat": 18970.4,
            "rank_change": 28,
            "rank_trend": "up",
        }
    ]
    fake_registry({
        "special": FakeSource([
            (r"FROM v_skyrocket", rows),
        ]),
    })

    out = _run(svc.market_hot_stocks(mode="skyrocket", limit=10))
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["items"][0]["thscode"] == "600391.SH"
    assert out["items"][0]["rank_change"] == 28


def test_hot_stock_trend_returns_time_series(fake_registry):
    """个股人气排名趋势接口返回按时间正序排列的走势。"""
    trend_rows = [
        {"thscode": "600418.SH", "trend_date": dt.date(2026, 10, 12), "date_ms": 1728691200000, "rank": 2},
        {"thscode": "600418.SH", "trend_date": dt.date(2026, 10, 11), "date_ms": 1728604800000, "rank": 1},
    ]
    fake_registry({
        "special": FakeSource([
            (r"FROM v_hot_stock_rank_trend", trend_rows),
        ]),
    })

    out = _run(svc.market_hot_stock_trend(thscode="600418.SH", limit=10))
    assert out["ok"] is True
    assert out["count"] == 2
    # 验证反转为正序（从前到后）
    assert out["items"][0]["date"] == "2026-10-11"
    assert out["items"][1]["date"] == "2026-10-12"


def test_futures_warehouse_returns_inventory_changes(fake_registry):
    """期货仓单接口返回注册仓单与增减量。"""
    rows = [
        {"trade_date": dt.date(2026, 10, 13), "thscode": "HC2701.SHF", "amount": 228527.0, "amount_change": 33564.0, "equivalent_lots": 22852.0},
        {"trade_date": dt.date(2026, 10, 13), "thscode": "AL2611.SHF", "amount": 95305.0, "amount_change": -4987.0, "equivalent_lots": 19061.0},
    ]
    fake_registry({
        "futures": FakeSource([
            (r"FROM raw_futures_warehouse_receipts", rows),
        ]),
    })

    out = _run(svc.market_futures_warehouse(limit=10))
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["items"][0]["thscode"] == "HC2701.SHF"
    assert out["items"][1]["amount_change"] == -4987.0


def test_futures_basis_rate_returns_contango_backwardation(fake_registry):
    """期货基差接口返回现货价、期货价与基差率。"""
    rows = [
        {"trade_date": dt.date(2026, 10, 13), "thscode": "CU2611.SHF", "spot_price": 76800.0, "close_price": 76200.0, "close_basis": 600.0, "close_basis_rate": 0.78},
    ]
    fake_registry({
        "futures": FakeSource([
            (r"FROM raw_futures_basis", rows),
        ]),
    })

    out = _run(svc.market_futures_basis_rate(limit=10))
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["items"][0]["close_basis"] == 600.0
    assert out["items"][0]["close_basis_rate"] == 0.78


def test_etf_holders_detail_returns_structure_and_top_holders(fake_registry):
    """ETF持有人详情接口返回持有人结构与前十大持有人。"""
    struct_rows = [
        {
            "fund_thscode": "159113.SZ",
            "fund_name": "科创债ETF大成",
            "merge_scope": "separate",
            "report_date_ms": 1782748800000,
            "ins_position": 99.97,
            "holder_amount": 265,
            "avg_holder_share": 367231.08,
            "psnl_rate": 0.03,
            "mgmt_staff_hold_rate": None,
        }
    ]
    top_rows = [
        {
            "fund_thscode": "159113.SZ",
            "holder_name": "国投证券股份有限公司",
            "holder_type": "券商",
            "holder_code": "015003",
            "rank": 1,
            "hold_share": 904.43,
            "hold_rate_pct": 7.32,
            "report_date_ms": 1782748800000,
            "publish_date_ms": 1783008000000,
        }
    ]
    fake_registry({
        "fund": FakeSource([
            (r"FROM v_fund_holders", struct_rows),
            (r"FROM v_fund_top_holders", top_rows),
        ]),
    })

    out = _run(svc.market_etf_holders(thscode="159113.SZ", limit=10))
    assert out["ok"] is True
    assert out["fund_thscode"] == "159113.SZ"
    assert out["structure"]["ins_position"] == 99.97
    assert out["structure"]["psnl_rate"] == 0.03
    assert out["count"] == 1
    assert out["top_holders"][0]["holder_name"] == "国投证券股份有限公司"
    assert out["top_holders"][0]["holder_type"] == "券商"


def test_etf_institutional_ranking_returns_ordered_list(fake_registry):
    """全市场机构持仓排行接口返回机构控盘度榜单。"""
    rows = [
        {
            "fund_thscode": "159113.SZ",
            "fund_name": "科创债ETF大成",
            "merge_scope": "separate",
            "report_date_ms": 1782748800000,
            "ins_position": 99.97,
            "holder_amount": 265,
            "avg_holder_share": 367231.08,
            "psnl_rate": 0.03,
        }
    ]
    fake_registry({
        "fund": FakeSource([
            (r"FROM v_fund_holders", rows),
        ]),
    })

    out = _run(svc.market_etf_institutional_ranking(order_by="institution", limit=10))
    assert out["ok"] is True
    assert out["count"] == 1
    assert out["items"][0]["fund_thscode"] == "159113.SZ"
    assert out["items"][0]["ins_position"] == 99.97


def test_squeeze_status_detects_squeeze_and_supertrend(fake_registry):
    """TTM Squeeze 接口识别布林带收缩在肯特纳通道内及 Supertrend 顺势牛。"""
    rows = [
        {
            "date": dt.date(2026, 10, 13),
            "volatility_bbands_20_2_0_upper": 11.75,
            "volatility_bbands_20_2_0_lower": 11.24,
            "volatility_bbands_20_2_0_middle": 11.50,
            "volatility_kc_20_2_upper": 11.91,
            "volatility_kc_20_2_lower": 11.07,
            "volatility_kc_20_2_middle": 11.49,
            "trend_supertrend_10_3_0_trend": 11.05,
            "trend_supertrend_10_3_0_direction": 1.0,
            "trend_supertrend_10_3_0_long": 11.05,
            "trend_supertrend_10_3_0_short": None,
            "momentum_macd_12_26_9_hist": 0.0071,
            "statistics_beta_20": 0.85,
        }
    ]
    fake_registry({
        "indicators": FakeSource([
            (r"FROM v_indicators_daily", rows),
        ]),
    })

    out = _run(svc.market_squeeze_status(thscode="000001.SZ", limit=10))
    assert out["ok"] is True
    assert out["thscode"] == "000001.SZ"
    assert out["is_squeezing"] is True
    assert out["squeeze_status"] == "on"
    assert out["supertrend_direction"] == 1
    assert out["supertrend_level"] == 11.05
    assert out["momentum_hist"] == 0.0071
