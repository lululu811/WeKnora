"""管线验证（promote_by_pipeline）与 scope 隔离的行为测试。

这两条规则是「HALO 六维能不能拿到输入」的关键：

* scope 隔离 —— 母公司口径的记录不能拿到合并口径的对账结论。早先 reconcile
  按 field 而非 (field, scope) 匹配，导致母公司总资产（1953 亿）领走了合并
  总资产（3038 亿）的 verified，一格从未被尺子量过的数就这么进了评分公式。
* 管线验证 —— 没有对账口径的字段（固定资产/在建工程/无形资产/员工数）靠
  「同 scope 下有口径的字段全部对账通过」获得可信度，否则它们永远是 pending，
  六维里的四维永远拿不到输入。
"""

import asyncio

import pytest

from halo import reconcile as rc
from halo.store import (
    SCOPE_CONSOLIDATED,
    SCOPE_PARENT,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    VERIFIED_BY_PIPELINE,
    VERIFIED_BY_RECONCILE,
)

# 贵州茅台 2025 年报的实测值（PDF 侧）与 DuckDB 侧参照
PDF = {
    "total_assets": 303_834_844_021.44,
    "net_profit": 85_310_324_833.67,
    "inventory": 61_427_421_796.18,
}
DUCK = {
    "assets_total": 303_834_844_021.44,      # 差异 0.0
    "net_profit": 85_310_324_833.67,         # 差异 0.0
    "inventory_estimate": 57_879_042_249.94, # 差异 5.78%，口径不同
}
PARENT_PDF = {
    "total_assets": 195_350_142_529.19,
    "net_profit": 85_305_341_965.73,
}


class FakeSrc:
    def __init__(self, row=None):
        self._row = row

    async def execute(self, query, params=None):
        if self._row is None:
            return []
        return [self._row]


def _ref_row(**over):
    row = dict(
        thscode="600519.SH", db_period="annual", fiscal_year=2025,
        fiscal_period="FY", period_end_ms=1767110400000,
        assets_total=DUCK["assets_total"], net_profit=DUCK["net_profit"],
        parent_holder_net_profit=DUCK["net_profit"],
        operating_costs=148_920_000_000.0, inventory_turnover_ratio=0.2573,
    )
    row.update(over)
    return row


def _rec(field, value, scope=SCOPE_CONSOLIDATED):
    return {"field": field, "value": value, "scope": scope, "status": STATUS_PENDING}


# ----------------------------------------------------------------------
# scope 隔离
# ----------------------------------------------------------------------


def test_parent_scope_does_not_inherit_consolidated_verdict():
    """母公司记录必须有自己���对账结论，不能领合并口径的。"""
    records = [
        _rec("total_assets", PDF["total_assets"], SCOPE_CONSOLIDATED),
        _rec("total_assets", PARENT_PDF["total_assets"], SCOPE_PARENT),
    ]
    results = asyncio.run(rc.reconcile_records(
        FakeSrc(_ref_row()), records, thscode="600519.SH",
        period="2025-12-31", report_type="annual",
    ))
    by_key = {(r.field, r.scope): r for r in results}
    assert by_key[("total_assets", SCOPE_CONSOLIDATED)].passed is True
    # 母公司口径没有对应的尺子（hithink 同步的是合并报表）
    assert by_key[("total_assets", SCOPE_PARENT)].passed is None
    assert by_key[("total_assets", SCOPE_PARENT)].status_after == STATUS_PENDING


def test_both_scopes_get_separate_results():
    """同名字段在两个 scope 下都要有结论（去重键必须带 scope）。"""
    records = [
        _rec("net_profit", PDF["net_profit"], SCOPE_CONSOLIDATED),
        _rec("net_profit", PARENT_PDF["net_profit"], SCOPE_PARENT),
    ]
    results = asyncio.run(rc.reconcile_records(
        FakeSrc(_ref_row()), records, thscode="600519.SH",
        period="2025-12-31", report_type="annual",
    ))
    assert len(results) == 2
    assert {r.scope for r in results} == {SCOPE_CONSOLIDATED, SCOPE_PARENT}


# ----------------------------------------------------------------------
# 管线验证
# ----------------------------------------------------------------------


def test_pipeline_promotes_fields_without_reconcile_rule():
    """有口径字段全部通过时，同 scope 的无口径字段升级为 verified。"""
    records = [
        _rec("total_assets", PDF["total_assets"]),
        _rec("net_profit", PDF["net_profit"]),
        # 以下三个在 DuckDB 里没有影子，属于「靠管线可信度」
        _rec("fixed_assets", 22_488_122_304.35),
        _rec("construction_in_progress", 2_471_886_030.58),
        _rec("employees_total", 34_992.0),
    ]
    results = [
        rc.ReconcileResult(
            field="total_assets", scope=SCOPE_CONSOLIDATED, passed=True,
            threshold=0.01, pdf_value=PDF["total_assets"],
            duckdb_value=DUCK["assets_total"], diff=0.0, diff_ratio=0.0,
            status_before=STATUS_PENDING, status_after=STATUS_VERIFIED, reason="ok",
        ),
        rc.ReconcileResult(
            field="net_profit", scope=SCOPE_CONSOLIDATED, passed=True,
            threshold=0.01, pdf_value=PDF["net_profit"],
            duckdb_value=DUCK["net_profit"], diff=0.0, diff_ratio=0.0,
            status_before=STATUS_PENDING, status_after=STATUS_VERIFIED, reason="ok",
        ),
    ]
    out = rc.finalize_status(records, results)
    by_field = {r["field"]: r for r in out}
    assert by_field["fixed_assets"]["status"] == STATUS_VERIFIED
    assert by_field["fixed_assets"]["verified_by"] == VERIFIED_BY_PIPELINE
    assert by_field["employees_total"]["status"] == STATUS_VERIFIED
    # 直接被尺子放行的，来源要标成 reconcile 而不是 pipeline
    assert by_field["total_assets"]["verified_by"] == VERIFIED_BY_RECONCILE


def test_pipeline_refuses_to_promote_disputed():
    """disputed 是被尺子**证伪**过的，管线可信不等于这一格对，绝不放行。"""
    records = [
        _rec("total_assets", 1.0),          # 会被判 disputed
        _rec("net_profit", PDF["net_profit"]),  # 通过
        _rec("fixed_assets", 22_488_122_304.35),
    ]
    results = [
        rc.ReconcileResult(
            field="total_assets", scope=SCOPE_CONSOLIDATED, passed=False,
            threshold=0.01, pdf_value=1.0, duckdb_value=DUCK["assets_total"],
            diff=1.0, diff_ratio=1.0, status_before=STATUS_PENDING,
            status_after=STATUS_DISPUTED, reason="差太远",
        ),
        rc.ReconcileResult(
            field="net_profit", scope=SCOPE_CONSOLIDATED, passed=True,
            threshold=0.01, pdf_value=PDF["net_profit"],
            duckdb_value=DUCK["net_profit"], diff=0.0, diff_ratio=0.0,
            status_before=STATUS_PENDING, status_after=STATUS_VERIFIED, reason="ok",
        ),
    ]
    out = rc.finalize_status(records, results)
    by_field = {r["field"]: r for r in out}
    # 有一个被证伪 → 整条 scope 的管线不算可信
    assert by_field["fixed_assets"]["status"] == STATUS_PENDING
    assert by_field["fixed_assets"].get("verified_by") is None


def test_pipeline_trust_is_scoped_not_global():
    """合并口径管线可信，不代表母公司口径也可信。"""
    records = [
        _rec("net_profit", PDF["net_profit"], SCOPE_CONSOLIDATED),
        _rec("fixed_assets", 22_488_122_304.35, SCOPE_CONSOLIDATED),
        _rec("net_profit", PARENT_PDF["net_profit"], SCOPE_PARENT),
        _rec("fixed_assets", 22_091_749_459.36, SCOPE_PARENT),
    ]
    results = [
        rc.ReconcileResult(
            field="net_profit", scope=SCOPE_CONSOLIDATED, passed=True,
            threshold=0.01, pdf_value=PDF["net_profit"],
            duckdb_value=DUCK["net_profit"], diff=0.0, diff_ratio=0.0,
            status_before=STATUS_PENDING, status_after=STATUS_VERIFIED, reason="ok",
        ),
        rc.ReconcileResult(
            field="net_profit", scope=SCOPE_PARENT, passed=None,
            threshold=0.01, pdf_value=PARENT_PDF["net_profit"], duckdb_value=None,
            diff=None, diff_ratio=None, status_before=STATUS_PENDING,
            status_after=STATUS_PENDING, reason="母公司口径无尺子",
        ),
    ]
    out = rc.finalize_status(records, results)
    by = {(r["field"], r["scope"]): r for r in out}
    assert by[("fixed_assets", SCOPE_CONSOLIDATED)]["status"] == STATUS_VERIFIED
    # 母公司口径一个能对得上的字段都没有，管线不建立信任
    assert by[("fixed_assets", SCOPE_PARENT)]["status"] == STATUS_PENDING


def test_unreconcilable_only_scope_is_not_trusted():
    """「对不了」不算通过也不算失败，但不能凭空当可信。"""
    records = [_rec("net_profit", PDF["net_profit"])]
    results = [
        rc.ReconcileResult(
            field="net_profit", scope=SCOPE_CONSOLIDATED, passed=None,
            threshold=0.01, pdf_value=PDF["net_profit"], duckdb_value=None,
            diff=None, diff_ratio=None, status_before=STATUS_PENDING,
            status_after=STATUS_PENDING, reason="参照缺失",
        ),
    ]
    out = rc.finalize_status(records, results)
    assert out[0]["status"] == STATUS_PENDING


# ----------------------------------------------------------------------
# 实测触发样本：北京金山办公 2025 年报（688111.SH）
# ----------------------------------------------------------------------

#: 两条 disputed 曾把整个合并口径钉死在 pending —— 六维因此"不可计算"，
#: 而如实报出来的原因是「缺 fixed_assets / construction_in_progress /
#: inventory / employees」，看不出病根其实在两格对账上。
#:
#: 一条是抽取错（p111 正文脚注的 0.00 覆盖了 p110 的净利润真值，见
#: test_halo_extractor.TestNarrativeFootnotes），一条是口径差（存货参照值是
#: 平均存货，年报给的是期末余额，见 reconcile.MATERIALITY_EXEMPTIONS）。
JINSHAN_PDF = {
    "total_assets": 18_155_801_690.14,
    "net_profit": 1_821_869_488.91,
    "inventory": 522_439.52,
    "fixed_assets": 453_218_785.42,
    "construction_in_progress": 134_959_435.80,
    "employees_total": 6048.0,
}

JINSHAN_DUCK = {
    "assets_total": 18_155_801_690.14,
    "net_profit": 1_821_869_488.91,
    # 833,129,464.33 / 1258.0513 = 662,238.07 = (522,439.52 + 802,036.62) / 2
    "operating_costs": 833_129_464.33,
    "inventory_turnover_ratio": 1258.0513,
}


def test_jinshan_2025_scope_becomes_trusted():
    """修好那两条后，六维要的字段才拿得到输入 —— 这是整条链路的验收点。"""
    records = [_rec(f, v) for f, v in JINSHAN_PDF.items()]
    row = dict(
        thscode="688111.SH", db_period="annual", fiscal_year=2025,
        fiscal_period="FY", period_end_ms=1767110400000,
        parent_holder_net_profit=JINSHAN_DUCK["net_profit"], **JINSHAN_DUCK,
    )
    results = asyncio.run(rc.reconcile_records(
        FakeSrc(row), records, thscode="688111.SH",
        period="2025-12-31", report_type="annual",
    ))
    out = rc.finalize_status(records, results)
    by = {r["field"]: r for r in out}

    # 存货走的是量级判据，不是相对阈值 —— 豁免理由要留在记录里可查
    assert by["inventory"]["status"] == STATUS_VERIFIED
    assert "量级判据" in by["inventory"]["reconcile"]["reason"]
    assert by["net_profit"]["status"] == STATUS_VERIFIED

    # 六维真正要的三个字段：靠管线信任升级，来源可区分
    for field in ("fixed_assets", "construction_in_progress", "employees_total"):
        assert by[field]["status"] == STATUS_VERIFIED, f"{field} 没拿到输入，六维算不出来"
        assert by[field]["verified_by"] == VERIFIED_BY_PIPELINE
