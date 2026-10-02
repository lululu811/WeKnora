"""analyze() 的入参契约：可选参数传空串必须等同于不传。

实测过的线上失败
----------------
``/halo/score`` 的 ``period`` 是可选参数。调用方把「未指定」序列化成 ``""``
而不是省略时（Go 侧 ``map[string]any`` 就是这么发的），``period`` 不是 ``None``，
于是跳过「取最新期次」的解析，一路带到 ``_fetch_financials`` 的
``int(period[:4])``，在那里抛 ``ValueError: invalid literal for int() with
base 10: ''`` —— 整个端点 500。

为什么它长期隐身
----------------
只在 **financials 数据源就绪时**才炸：数据源缺失时 ``_fetch_financials`` 提前
返回空 dict，于是本地没有 DuckDB 的开发环境完全测不出来。这条测试用假数据源
把那条分支逼出来，不依赖本机是否有库。

修法是 ``if not period`` 而不是 ``if period is None``：空串和 None 对调用方是
同一个意思（「我没指定」），不该有两种行为。
"""

import asyncio

import pytest

from halo import analyze as az


class FakeFinancialSource:
    """只要有实例，_fetch_financials 就会走到 int(period[:4]) 那一行。"""

    async def execute(self, query, params=None):
        return []


class FakeStore:
    def __init__(self, latest="2025-12-31"):
        self._latest = latest

    def latest_period(self, thscode, report_type):
        return self._latest

    def query(self, thscode, period=None, report_type=None, scope=None, only_verified=None):
        return []


@pytest.fixture
def financials_ready(monkeypatch):
    """让 _fetch_financials 不提前返回 —— 这是暴露该 bug 的前提。"""
    monkeypatch.setattr(az, "get_financials_source", lambda: FakeFinancialSource())


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("given", ["", None])
def test_unspecified_period_resolves_to_latest(financials_ready, given):
    """空串与 None 必须同义：都解析成最新期次，都不许抛。"""
    result = run(az.analyze("600519", store=FakeStore(), period=given))
    assert result["period"] == "2025-12-31"


def test_empty_period_does_not_raise(financials_ready):
    """回归：这一条在修复前抛 ValueError（端点 500）。"""
    result = run(az.analyze("600519", store=FakeStore(), period=""))
    assert result["period"] == "2025-12-31", "空串被当成「没指定」，应回落到最新期次"


def test_explicit_period_is_respected(financials_ready):
    """显式给的期次不许被「最新期次」顶掉。"""
    result = run(az.analyze("600519", store=FakeStore(latest="2025-12-31"), period="2024-12-31"))
    assert result["period"] == "2024-12-31"


def test_no_period_anywhere_returns_full_shape_not_exception(financials_ready):
    """库里一期都没有时是「没数据」，不是异常 —— 且必须带完整结构。"""
    result = run(az.analyze("600519", store=FakeStore(latest=None), period=""))
    assert result["ok"] is False
    assert "没有已落库的年报事实" in result["reason"]
    for key in ("thscode", "period", "report_type", "scope", "missing",
                "asset_type", "halo", "growth", "facts", "ai_slots", "markdown"):
        assert key in result, f"早返回也必须带 {key}，否则调用方会在 KeyError 上崩"
