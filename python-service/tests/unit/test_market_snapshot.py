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
