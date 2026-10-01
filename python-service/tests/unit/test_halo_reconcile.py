"""HALO 对账的单元测试。

用假的 ``AsyncSQLExecutor`` 注入，不碰真 DuckDB、不联网。真实 DuckDB 的
SQL 口径在开发期已用 600519 单独验证过（见报告），这里锁的是**逻辑分支**
和那条最要紧的红线：���账失败时绝不用 DuckDB 的值覆盖 PDF 的值。
"""

import asyncio

import pytest

from halo import reconcile as rc
from halo.store import (
    REPORT_ANNUAL,
    REPORT_H1,
    REPORT_Q1,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
)


class FakeSource:
    """按调用次序返回预置行，并记录 SQL/参数以便断言查询形状。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, query, params=None):
        self.calls.append((query, list(params or [])))
        return self.rows


# 贵州茅台 2026 上半年真实值（开发期实测）
MAOTAI_Q2 = {
    "thscode": "600519.SH", "db_period": "quarterly", "fiscal_year": 2026,
    "fiscal_period": "Q2", "period_end_ms": 1782748800000,
    "assets_total": 309050784569.31,
    "operating_costs": 9473762565.88,
    "net_profit": 46033330566.78,
    "parent_holder_net_profit": 46026723467.42,
    "inventory_turnover_ratio": 0.1544,
}


def _row(**over):
    r = dict(MAOTAI_Q2)
    r.update(over)
    return r


# ---------------------------------------------------------------------------
# 报告期定位
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_annual_maps_to_FY(self):
        """年报 -> DuckDB 的 annual/FY 口径。"""
        t = rc.resolve_target("2025-12-31", REPORT_ANNUAL)
        assert (t.db_period, t.fiscal_year, t.fiscal_period) == ("annual", 2025, "FY")

    def test_h1_maps_to_Q2(self):
        t = rc.resolve_target("2026-06-30", REPORT_H1)
        assert (t.db_period, t.fiscal_year, t.fiscal_period) == ("quarterly", 2026, "Q2")

    def test_q1_maps_to_Q1(self):
        t = rc.resolve_target("2026-03-31", REPORT_Q1)
        assert t.fiscal_period == "Q1" and t.db_period == "quarterly"

    def test_date_only_fallback(self):
        """没有 report_type 时退回按 ISO 日期定位。"""
        t = rc.resolve_target("2026-06-30")
        assert t.iso_date == "2026-06-30" and t.fiscal_year == 2026

    def test_unparseable_period_returns_none(self):
        """定位不到就返回 None，不要猜一个报告期去查。"""
        assert rc.resolve_target("未知") is None

    def test_explicit_override_wins(self):
        t = rc.resolve_target("2025-12-31", REPORT_ANNUAL, fiscal_period="Q4")
        assert t.fiscal_period == "Q4" and t.db_period == "quarterly"


# ---------------------------------------------------------------------------
# 参照值
# ---------------------------------------------------------------------------


class TestFetchReference:
    def test_inventory_is_backderived(self):
        """94.74亿 / 0.1544 ≈ 613.59 亿元。"""
        src = FakeSource([MAOTAI_Q2])
        ref = asyncio.run(rc.fetch_reference(src, "600519.SH",
                                             rc.resolve_target("2026-06-30", REPORT_H1)))
        assert ref.inventory_estimate == pytest.approx(61358565841.19)
        assert ref.assets_total == pytest.approx(309050784569.31)
        assert ref.net_profit == pytest.approx(46033330566.78)

    def test_zero_turnover_gives_no_estimate(self):
        """周转率为 0 时不能拿 0 去比（会把一切判成不一致）。"""
        src = FakeSource([_row(inventory_turnover_ratio=0.0)])
        ref = asyncio.run(rc.fetch_reference(src, "600519.SH",
                                             rc.resolve_target("2026-06-30", REPORT_H1)))
        assert ref.inventory_estimate is None

    def test_null_turnover_gives_no_estimate(self):
        """年报 FY 在指标表里若无对应行，LEFT JOIN 会给出 NULL。"""
        src = FakeSource([_row(inventory_turnover_ratio=None)])
        ref = asyncio.run(rc.fetch_reference(src, "600519.SH",
                                             rc.resolve_target("2025-12-31", REPORT_ANNUAL)))
        assert ref.inventory_estimate is None
        assert ref.assets_total is not None   # 其余字段照常可用

    def test_no_rows_returns_none(self):
        src = FakeSource([])
        assert asyncio.run(rc.fetch_reference(src, "600519.SH",
                                             rc.resolve_target("2026-06-30", REPORT_H1))) is None

    def test_sql_uses_full_join_key_not_period_end_ms(self):
        """只按 period_end_ms join 会因 annual/FY 与 quarterly/Q4 共享时间戳
        而放大 40% 的行。完整键是硬要求，这里直接锁住。"""
        src = FakeSource([MAOTAI_Q2])
        asyncio.run(rc.fetch_reference(src, "600519.SH",
                                       rc.resolve_target("2026-06-30", REPORT_H1)))
        sql, params = src.calls[0]
        assert "i.fiscal_period = b.fiscal_period" in sql
        assert "i.fiscal_year  = b.fiscal_year" in sql
        assert "i.period       = b.period" in sql
        # WHERE 里不允许出现 period_end_ms 参与等值筛选
        where = sql.split("WHERE", 1)[1]
        assert "period_end_ms =" not in where.replace("b.period_end_ms / 1000", "")
        assert params == ["600519.SH", 2026, "Q2", "quarterly"]

    def test_sql_maps_FY_to_dash4_for_indicators(self):
        """FY 在指标表里没有行（实测 '2025-FY' 为 0 行），
        必须在 SQL 里特判成 -4，否则年报的存货规则静默失效。"""
        sql = rc._REFERENCE_SQL
        assert "b.fiscal_period = 'FY'" in sql
        assert "|| '-4'" in sql


# ---------------------------------------------------------------------------
# 逐字段对账
# ---------------------------------------------------------------------------


def _ref(**over):
    base = dict(
        thscode="600519.SH", db_period="quarterly", fiscal_year=2026, fiscal_period="Q2",
        period_end_ms=1782748800000, assets_total=309050784569.31, net_profit=46033330566.78,
        parent_holder_net_profit=46026723467.42,
        inventory_estimate=61358565841.19, inventory_turnover_ratio=0.1544,
        operating_costs=9473762565.88,
    )
    base.update(over)
    return rc.ReferenceRow(**base)


class TestCompareField:
    def test_total_assets_pass(self):
        r = rc.compare_field("total_assets", 309050784569.31, _ref(), STATUS_PENDING)
        assert r.passed is True and r.status_after == STATUS_VERIFIED
        assert r.diff_ratio == pytest.approx(0.0)

    def test_total_assets_within_1pct_passes(self):
        r = rc.compare_field("total_assets", 309050784569.31 * 1.005, _ref(), STATUS_PENDING)
        assert r.passed is True

    def test_total_assets_beyond_1pct_fails(self):
        r = rc.compare_field("total_assets", 309050784569.31 * 1.02, _ref(), STATUS_PENDING)
        assert r.passed is False and r.status_after == STATUS_DISPUTED
        # 差异率的分母取 max(|PDF|,|DuckDB|)，所以 1.02 倍的偏差算出来是 1.96%
        assert 0.019 < r.diff_ratio < 0.020
        assert "不以 DuckDB 值补位" in r.reason

    def test_inventory_uses_wider_threshold(self):
        """存货参照值是反推量，容差 20%。

        参照值 = 营业成本 / 存货周转率，而周转率分母是**平均**存货；年报给的是
        **期末**余额。茅台实测期末 614.27 亿 vs 反推均值 578.79 亿，差 5.78%
        纯属口径。所以 5% 阈值会把正常的存货增减误报成"抽取被证伪"。
        """
        base = _ref()
        # 5.78% —— 真实茅台年报的实测差异，必须通过
        r = rc.compare_field("inventory", base.inventory_estimate * 1.0578,
                             base, STATUS_PENDING)
        assert r.passed is True
        assert r.reason and "口径" in r.reason, "存货结论必须带出口径说明"

        # 但上界仍要兜住量级错误（抽到附注编号 10.00 这类会差几个数量级）
        far = rc.compare_field("inventory", base.inventory_estimate * 1.5,
                               base, STATUS_PENDING)
        assert far.passed is False and far.status_after == STATUS_DISPUTED

    def test_net_profit_uses_not_parent_holder(self):
        """用净利润，不是归母。拿归母来比会差一点点并被 1% 阈值判成冲突。"""
        r = rc.compare_field("net_profit", 46026723467.42, _ref(), STATUS_PENDING)
        # 归母与净利润的差约 0.014%，仍在 1% 内 —— 但参照值取的是 net_profit
        assert r.duckdb_value == pytest.approx(46033330566.78)
        assert r.passed is True

    def test_field_without_rule_is_not_reconciled(self):
        r = rc.compare_field("goodwill", 1.5e9, _ref(), STATUS_PENDING)
        assert r.passed is None
        assert r.status_after == STATUS_PENDING   # 维持原状，不假装对过
        assert "无 DuckDB 对账口径" in r.reason

    def test_missing_reference_keeps_status(self):
        r = rc.compare_field("total_assets", 3.0e11, None, STATUS_PENDING)
        assert r.passed is None and r.status_after == STATUS_PENDING

    def test_missing_duckdb_value_keeps_status(self):
        r = rc.compare_field("inventory", 6.1e10, _ref(inventory_estimate=None), STATUS_PENDING)
        assert r.passed is None and r.status_after == STATUS_PENDING

    def test_missing_pdf_value_keeps_missing(self):
        """PDF 没抽到就还是缺失，绝不用 DuckDB 的值补上。"""
        r = rc.compare_field("inventory", None, _ref(), STATUS_PENDING)
        assert r.passed is None and r.status_after == STATUS_PENDING
        assert r.pdf_value is None
        assert "不补值" in r.reason

    def test_verified_is_demoted_when_reconcile_fails(self):
        """对账失手要拦住它进评分公式。

        双通道一致只能证明「两个读法一样」，而两个读法可能一起读错了同一列
        —— 这正是对账要抓的情况。只有 verified 能进评分，往严走才安全。
        """
        r = rc.compare_field("total_assets", 1.0, _ref(), STATUS_VERIFIED)
        assert r.passed is False
        assert r.status_before == STATUS_VERIFIED
        assert r.status_after == STATUS_DISPUTED

    def test_diff_is_reported(self):
        r = rc.compare_field("total_assets", 3.0e11, _ref(), STATUS_PENDING)
        assert r.diff is not None and r.duckdb_value is not None
        d = r.to_dict()
        assert d["field"] == "total_assets" and "duckdb_value" in d


# ---------------------------------------------------------------------------
# 批量对账 + 应用
# ---------------------------------------------------------------------------


def _rec(field, value, status=STATUS_PENDING):
    return {"field": field, "value": value, "unit": "CNY",
            "status": status, "scope": "consolidated"}


class TestReconcileRecords:
    def test_end_to_end_pass_and_fail_mixed(self):
        src = FakeSource([MAOTAI_Q2])
        recs = [
            _rec("total_assets", 309050784569.31),          # 通过
            _rec("inventory", 61.358e9),                  # 通过
            _rec("net_profit", 1.0),                      # 不通过
            _rec("goodwill", 1.5e9),                      # 无口径，不参与
        ]
        out = asyncio.run(rc.reconcile_records(
            src, recs, thscode="600519.SH", period="2026-06-30", report_type=REPORT_H1))
        by = {r.field: r for r in out}
        assert set(by) == {"total_assets", "inventory", "net_profit"}   # goodwill 不在结果里
        assert by["total_assets"].passed is True
        assert by["inventory"].passed is True
        assert by["net_profit"].passed is False
        assert by["net_profit"].status_after == STATUS_DISPUTED

    def test_no_reference_row_yields_all_unreconciled(self):
        src = FakeSource([])
        out = asyncio.run(rc.reconcile_records(
            src, [_rec("total_assets", 3.0e11)], thscode="600519.SH",
            period="2026-06-30", report_type=REPORT_H1))
        assert out[0].passed is None

    def test_unresolvable_period_returns_empty(self):
        src = FakeSource([MAOTAI_Q2])
        out = asyncio.run(rc.reconcile_records(
            src, [_rec("total_assets", 3.0e11)], thscode="600519.SH", period="??"))
        assert out == [] and src.calls == []


class TestApplyReconcile:
    def test_only_status_changes_value_untouched(self):
        """红线测试：应用对账结论时，PDF 抽出来的值一个字节都不能动。"""
        recs = [_rec("net_profit", 1.0), _rec("total_assets", 309050784569.31)]
        results = [
            rc.compare_field("net_profit", 1.0, _ref(), STATUS_PENDING),
            rc.compare_field("total_assets", 309050784569.31, _ref(), STATUS_PENDING),
        ]
        out = rc.apply_reconcile(recs, results)
        assert [r["value"] for r in out] == [1.0, 309050784569.31]   # 没被顶替
        assert out[0]["status"] == STATUS_DISPUTED
        assert out[1]["status"] == STATUS_VERIFIED
        assert out[0]["reconcile"]["duckdb_value"] == pytest.approx(46033330566.78)

    def test_input_records_not_mutated(self):
        recs = [_rec("total_assets", 3.0e11)]
        rc.apply_reconcile(recs, [rc.compare_field("total_assets", 3.0e11, _ref(), STATUS_PENDING)])
        assert recs[0]["status"] == STATUS_PENDING    # 原对象没被就地改
