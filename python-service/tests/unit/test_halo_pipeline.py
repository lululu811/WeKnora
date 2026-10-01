"""pipeline 编排层的单元测试。

这里锁的是三件**只有真实年报才暴露**的问题，每一个都是在跑贵州茅台 2025
年报（143 页）的过程中发现并修掉的：

1. 表标题与表体跨页 —— 「合并资产负债表」标题在 p56，资产数据在 p57。只扫
   锚点页会一条都抽不到，于是 pipeline 静默退化到只抽旁边母公司表的数据，
   把母公司口径当成合并口径用。结果不是空，是错值。
2. 字段与锚点未绑定 —— 「净利润」在年报里命中 8 页，其中现金分红比例表的
   「通股股东的净利润的比率（%） 42.56」是比率不是金额。
3. 报告期年份取错 —— 2025 年报在 2026 年披露，用披露年份会把 2025 年报记成
   2026，评分内核取数错一整年。
"""

import os

import pytest

from halo import pipeline
from halo.pdf_extract import ExtractResult, PageText

REAL_PDF = "/tmp/halo_cninfo_test/600519_2025_annual.pdf"
real_pdf_available = pytest.mark.skipif(
    not os.path.exists(REAL_PDF), reason="真实年报样本不在本机，跳过 smoke 测试"
)


def _pages(spec: dict) -> ExtractResult:
    """用 {页码: 文本} 造一个 ExtractResult。"""
    return ExtractResult(
        pages=[PageText(page=p, text=t) for p, t in sorted(spec.items())],
        total_pages=len(spec),
    )


# ----------------------------------------------------------------------
# 报告期换算
# ----------------------------------------------------------------------


def test_period_uses_report_year_not_disclosure_year():
    assert pipeline.period_of(2025, "annual") == "2025-12-31"
    assert pipeline.period_of(2025, "h1") == "2025-06-30"
    assert pipeline.period_of(2025, "q1") == "2025-03-31"
    assert pipeline.period_of(2025, "q3") == "2025-09-30"


def test_period_rejects_unknown_report_type():
    # 静默给个默认值会让 2025Q2 之类的东西被当成年报记进去。
    with pytest.raises(ValueError):
        pipeline.period_of(2025, "q2")


# ----------------------------------------------------------------------
# 跨页补偿
# ----------------------------------------------------------------------


def test_pages_to_scan_extends_beyond_anchor_page():
    # 标题在 56，数据在 57/58 —— 这是茅台年报合并资产负债表的真实分页。
    pages = _pages({56: "合并资产负债表", 57: "存货 10 61,427,421,796.18", 58: "负债", 59: "母公司"})
    got = pipeline._pages_to_scan(pages, {56})
    assert 57 in got and 58 in got, "锚点页之后的数据页必须纳入扫描"
    assert 59 not in got, "补偿窗口有上限，不该无限往后吞"


def test_pages_to_scan_respects_document_end():
    pages = _pages({143: "最后一页"})
    assert pipeline._pages_to_scan(pages, {143}) == [143], "不能扫出文档范围"


def test_pages_to_scan_dedupes_overlapping_anchors():
    pages = _pages({56: "a", 57: "b", 58: "c"})
    assert pipeline._pages_to_scan(pages, {56, 57}) == [56, 57, 58]


# ----------------------------------------------------------------------
# 字段-锚点绑定
# ----------------------------------------------------------------------


def test_net_profit_is_not_taken_from_dividend_ratio_table(monkeypatch):
    """现金分红比例表里的「净利润的比率（%）」绝不能被当成净利润。

    这是实测茅台年报 p33 的真实行：数值 42.56 长得和正常金额毫无区别。
    """
    dividend_page = "通股股东的净利润的比率（%） 42.56"
    income_page = "五、净利润 85,310,324,833.67"
    pages = _pages({33: dividend_page, 62: income_page})

    def fake_extract(pages_seq, **_kw):
        out = []
        for page, text in pages_seq:
            if "比率" in text:
                out.append(pipeline.Fact(field="net_profit", value=42.56, unit=None,
                    scope="consolidated", extract_by="rule",
                    source_page=page, raw_text=text, confidence=1.0))
            else:
                out.append(pipeline.Fact(field="net_profit", value=85310324833.67, unit="CNY",
                    scope="consolidated", extract_by="rule",
                    source_page=page, raw_text=text, confidence=1.0))
        return out, "consolidated", None

    monkeypatch.setattr(pipeline, "extract_rule_pages", fake_extract)

    # 只给利润表锚点（p61 标题 → p62 数据）
    facts = pipeline._extract_by_anchors(pages, {"consolidated_income_statement": [61]})
    values = [f.value for f in facts]
    assert 42.56 not in values, "比率被当成净利润抽出来了"
    assert 85310324833.67 in values


def test_balance_fields_not_taken_from_income_statement(monkeypatch):
    """利润表页里出现的「存货」也不该进资产字段（同名不同表）。"""
    pages = _pages({57: "存货 10 61,427,421,796.18", 62: "存货周转 42.56"})
    monkeypatch.setattr(
        pipeline, "extract_rule_pages",
        lambda pages_seq, **_kw: ([pipeline.Fact(
            field="inventory", value=1.0, unit="CNY", scope="consolidated",
            extract_by="rule", source_page=pages_seq[0][0], raw_text=pages_seq[0][1],
            confidence=1.0)], "consolidated", None),
    )
    facts = pipeline._extract_by_anchors(pages, {"consolidated_income_statement": [61]})
    assert facts == [], "利润表锚点不该产出资产字段"


def test_unanchored_document_falls_back_to_full_scan(monkeypatch):
    """锚定失败时宁可多抽也不能一条不出：规则通道的失败模式是「抽不到」，
    不会凭空造数，所以全量扫描是安全的降级。"""
    pages = _pages({10: "固定资产 1,000.00", 20: "存货 2,000.00"})
    monkeypatch.setattr(
        pipeline, "extract_rule_pages",
        lambda pages_seq, **_kw: ([pipeline.Fact(
            field="fixed_assets", value=1.0, unit="CNY", scope="consolidated",
            extract_by="rule", source_page=page, raw_text=text, confidence=1.0)
            for page, text in pages_seq], "consolidated", None),
    )
    facts = pipeline._extract_by_anchors(pages, {})
    assert len(facts) == 2, "无锚点时应全量扫描"


# ----------------------------------------------------------------------
# 真实年报 smoke
# ----------------------------------------------------------------------


@real_pdf_available
def test_real_moutai_annual_report_extraction():
    """用真实年报跑一遍，锁住实测值。

    期望值来自 2026-10-01 对该 PDF 的独立提取：员工数是 p31 原文
    「在职员工的数量合计 34,992」；总资产与 DuckDB financials.duckdb 里
    v_balance_sheet.assets_total 的 303,834,844,021.44 一致。
    """
    from halo.extractor import extract_by_rule
    from halo.pdf_extract import extract_pages, find_anchors

    result = extract_pages(REAL_PDF)
    assert result.total_pages == 143
    assert result.failed_pages == [], f"解析失败页：{result.failed_pages}"

    anchors = find_anchors(result, keywords=pipeline.HALO_ANCHORS)
    facts = pipeline._extract_by_anchors(result, anchors)

    # 按 (field, source_page) 索引，而不是 (field, scope)：
    # scope 跨页判定尚未修复时，母公司表续页（p60）会被误标成 consolidated 并
    # 覆盖掉 p57 的正确值，用 scope 做键会让本该通过的数值断言跟着一起失败，
    # 掩盖「值已修好、scope 还没修好」这个真实状态。
    by_page = {(f.field, f.source_page): f for f in facts}

    emp = by_page[("employees_total", 31)]
    assert emp.value == 34992, f"员工数应为 34992，实际 {emp.value}"
    assert emp.scope == "consolidated"

    # 合并口径在 p57，数值须与 DuckDB v_balance_sheet.assets_total 一致。
    total = by_page[("total_assets", 57)]
    assert total.value == 303834844021.44, f"合并总资产应与 DuckDB 一致，实际 {total.value}"
    assert total.scope == "consolidated", f"p57 是合并报表，scope 应为 consolidated"

    # 附注编号（存货 10 / 固定资产 19 …）不得被当成金额。
    assert by_page[("fixed_assets", 57)].value == 22488122304.35
    assert by_page[("inventory", 57)].value == 61427421796.18


@real_pdf_available
def test_real_moutai_parent_scope_not_mislabeled_as_consolidated():
    """母公司表续页的 scope 必须保持 parent。

    p59 是母公司资产负债表（页内含表标题），p60 是它的续页（不含标题）。
    逐页重置状态机会把 p60 判回默认的 consolidated，于是母公司数据混进合并
    口径 —— 而这些资产字段在 DuckDB 里没有对账口径，混进去没人会发现。

    断言只圈 p59/p60：p62 是**合并**利润表（其 net_profit 与 DuckDB
    v_income_statement.net_profit 完全一致），p63 才是母公司利润表。
    把 p62 也要求成 parent 会把正确值判成错。
    """
    from halo.pdf_extract import extract_pages, find_anchors

    result = extract_pages(REAL_PDF)
    anchors = find_anchors(result, keywords=pipeline.HALO_ANCHORS)
    facts = pipeline._extract_by_anchors(result, anchors)

    for f in facts:
        if f.source_page in (59, 60) and f.scope != "parent":
            raise AssertionError(
                f"p{f.source_page} 的 {f.field} scope={f.scope}，"
                f"母公司表（p59-60）不该标成 {f.scope}"
            )



# --------------------------------------------------------------------------
# thscode 格式统一
# --------------------------------------------------------------------------


def test_thscode_normalization():
    """巨潮认 6 位、DuckDB 认带后缀，事实库必须存后者。

    格式不一致的后果是**静默**的：存 6 位后，对账按 600519.SH 去 join 查不
    到参照，不报错，只把每条都判成「对不了」，看起来像「这只股票没数据」。
    """
    assert pipeline.normalize_thscode("600519") == "600519.SH"
    assert pipeline.normalize_thscode("600519.SH") == "600519.SH"
    assert pipeline.normalize_thscode("688981") == "688981.SH"   # 科创板归沪
    assert pipeline.normalize_thscode("000001") == "000001.SZ"
    assert pipeline.normalize_thscode("300888") == "300888.SZ"   # 创业板归深
    assert pipeline.normalize_thscode("830799") == "830799.BJ"   # 北交所
    assert pipeline.normalize_thscode("") == ""
    assert pipeline.normalize_thscode("ABC") == "ABC"           # 非股票代码原样返回
