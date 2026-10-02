"""AI 待判槽位的锚点解析：外网只补本地两处都没有的项。

背景
----
``main_fund_flow`` 一直是 ``AI_DIMENSIONS`` 声明的锚点名（analyze.py 的
``("shareholder", "股东资金面", (("main_fund_flow", "amount"), ...))``），但
``anchor()`` 只查本地库（financial）和年报事实（facts），两处都没有这个字段 ——
于是「股东资金面」这一维**永远**带着 ``main_fund_flow`` 进 missing_anchors，
在 render_markdown 里显示成「⚠️ 无量化锚点」。

而数据其实早就抓回来了：``fetch_external`` 的 push2his 桶里有 60 天 fund_flow。
它只是从不进入锚点解析（``build_ai_slots`` 收不到 external）。

这里守三件事：
1. 外网资金流能补上 ``main_fund_flow``，让该维度不再是「无锚点」；
2. 外网**不许**抢本地已有锚点的口径（``holder_count`` 年报和东财都有）；
3. 5d/20d 是附加键，不改变 missing / has_anchor 的语义。
"""

import pytest

from halo.analyze import _fund_flow_anchors, build_ai_slots


def facts_with(**fields):
    """年报事实的形状：``{field: {"value": ...}}``（见 _field_value）。"""
    return {k: {"value": v} for k, v in fields.items()}


def external_with_fund_flow(rows):
    return {"push2his": {"fund_flow": rows}}


def series(*pairs):
    return [{"date": d, "main_net": v} for d, v in pairs]


def daily(n, value=1.0):
    """n 个交易日的资金流，日期递增。"""
    return series(*[(f"2026-01-{i:02d}", value) for i in range(1, n + 1)])


def slot(slots, key):
    return next(s for s in slots if s["dimension"] == key)


# ---------------------------------------------------------------------------
# _fund_flow_anchors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("external", [None, {}, {"push2his": {}}, external_with_fund_flow([])])
def test_no_usable_series_yields_no_anchors(external):
    assert _fund_flow_anchors(external) == {}


def test_non_numeric_rows_are_skipped_not_coerced():
    """拿不到数就是拿不到，不许用 0 顶替 —— 0 会被判分读成「资金持平」。"""
    rows = [
        {"date": "2026-01-01", "main_net": None},
        {"date": "2026-01-02", "main_net": "n/a"},
        {"date": "2026-01-03", "main_net": 5.0},
    ]
    out = _fund_flow_anchors(external_with_fund_flow(rows))
    assert out["main_fund_flow"] == 5.0
    assert out["main_fund_flow_5d"] == 5.0


def test_latest_day_wins_regardless_of_input_order():
    """接口的 klines 顺序不保证，锚点必须按日期自己取最新。"""
    rows = series(("2026-01-03", 30.0), ("2026-01-01", 10.0), ("2026-01-02", 20.0))
    assert _fund_flow_anchors(external_with_fund_flow(rows))["main_fund_flow"] == 30.0


def test_multi_scale_sums_are_trailing_windows():
    out = _fund_flow_anchors(external_with_fund_flow(daily(21)))
    assert out["main_fund_flow"] == 1.0
    assert out["main_fund_flow_5d"] == 5.0
    assert out["main_fund_flow_20d"] == 20.0


def test_short_series_windows_do_not_pad():
    """只有 3 天数据时，20d 窗口就是这 3 天，不许补零充数。"""
    out = _fund_flow_anchors(external_with_fund_flow(daily(3, value=2.0)))
    assert out["main_fund_flow_20d"] == 6.0


def test_no_cross_tier_merge_into_main():
    """extdata.fund_flow 明确不自行合并「主力」，锚点这层同样不许。

    该接口没取 f56（超大单），少一个档位就做大单加法等于用错口径。
    """
    rows = [{"date": "2026-01-01", "main_net": 100.0,
             "small_net": 1.0, "medium_net": 2.0, "large_net": 3.0}]
    out = _fund_flow_anchors(external_with_fund_flow(rows))
    assert out["main_fund_flow"] == 100.0, "必须是接口给的原值，不是自算的档位之和"


# ---------------------------------------------------------------------------
# build_ai_slots：解析顺序与语义
# ---------------------------------------------------------------------------


def test_main_fund_flow_fills_from_external():
    slots = build_ai_slots(
        facts_with(holder_count=12345), {},
        None, external_with_fund_flow(series(("2026-01-01", 7.0))),
    )
    sh = slot(slots, "shareholder")
    assert sh["anchors"]["main_fund_flow"] == 7.0
    assert "main_fund_flow" not in sh["missing_anchors"]
    assert sh["has_anchor"] is True


def test_without_external_the_anchor_still_reports_missing():
    """不传 external 时行为与改动前一致：如实说缺，不假装有。"""
    slots = build_ai_slots(facts_with(holder_count=12345), {}, None, None)
    sh = slot(slots, "shareholder")
    assert "main_fund_flow" in sh["missing_anchors"]
    assert sh["has_anchor"] is False


def test_external_must_not_override_annual_report_caliber():
    """holder_count 年报与东财都有：年报是权威原文，外网不许抢先换口径。"""
    slots = build_ai_slots(
        facts_with(holder_count=11111), {},
        None,
        {"datacenter": {"holder_count_latest": 99999},
         "push2his": {"fund_flow": series(("2026-01-01", 1.0))}},
    )
    assert slot(slots, "shareholder")["anchors"]["holder_count"] == 11111


def test_local_db_still_wins_over_external():
    """本地库优先级最高，外网连它也不许覆盖。"""
    slots = build_ai_slots(
        facts_with(holder_count=11111),
        {"holder_count": 22222},
        None,
        {"datacenter": {"holder_count_latest": 99999}},
    )
    assert slot(slots, "shareholder")["anchors"]["holder_count"] == 22222


def test_extras_land_only_on_shareholder():
    ext = external_with_fund_flow(daily(21))
    slots = build_ai_slots(facts_with(holder_count=1), {}, None, ext)
    sh = slot(slots, "shareholder")
    assert sh["anchors"]["main_fund_flow_5d"] == 5.0
    assert sh["anchors"]["main_fund_flow_20d"] == 20.0
    for other in slots:
        if other["dimension"] != "shareholder":
            leaked = [k for k in other["anchors"] if k.startswith("main_fund_flow")]
            assert leaked == [], f"{other['dimension']} 不该拿到资金流锚点：{leaked}"


def test_extras_do_not_change_has_anchor_semantics():
    """附加键不进 wanted：5d/20d 存在也不能把维度抬成「有锚点」。

    这里年报事实为空，holder_count 缺 —— 即使资金流三个尺度都在，
    missing_anchors 仍然必须如实列出 holder_count。
    """
    slots = build_ai_slots({}, {}, None, external_with_fund_flow(daily(21)))
    sh = slot(slots, "shareholder")
    assert sh["anchors"]["main_fund_flow"] == 1.0
    assert sh["anchors"]["main_fund_flow_5d"] == 5.0
    assert sh["missing_anchors"] == ["holder_count"]
    assert sh["has_anchor"] is False


def test_fund_flow_failure_does_not_disturb_other_dimensions():
    """push2his 易封：该档失败只该让资金流锚点缺失，不牵连其它维度。"""
    healthy = build_ai_slots(facts_with(holder_count=1, employees_total=100), {}, None, None)
    degraded = build_ai_slots(
        facts_with(holder_count=1, employees_total=100), {},
        None, {"errors": {"push2his": "boom"}, "subdomains": {"push2his": {"ok": False}}},
    )
    for key in ("moat", "stag", "esg", "management", "valuation", "risk"):
        assert slot(degraded, key)["anchors"] == slot(healthy, key)["anchors"], key
    assert slot(degraded, "shareholder")["missing_anchors"] == ["main_fund_flow"]
