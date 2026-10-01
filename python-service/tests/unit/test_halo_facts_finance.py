"""分红 / 股东户数 / 主营构成抽取测试。

三个坑都是真实年报暴露的，且失败方式都是「产出格式完好、语义错误」的数：

1. **单位不同**。茅台 2025 的主营构成表是「单位：元」、宝钢 2024 是
   「单位：百万元」。不读表头就会把宝钢 322,116 百万元当成 322,116 元，
   差 1e6 倍。
2. **同页混入别的表**。宝钢把利润表、现金流量表和主营构成表放在同一页，
   不锚定表头就会把「销售费用」「研发费用」「经营活动产生的现金流量净额」
   当成业务分部抽出来。
3. **分红口径不一致**。茅台「累计派发现金红利 650.33 亿元」是全年（含中期），
   「每股派发现金红利 27.993 元」只是年度部分，两者相除算出的分红率是错的。
   因此分红率只从证监会标准表取，叙述式年报如实缺失。
"""

import os

import pytest

from halo.facts_finance import (
    extract_business_segments,
    extract_dividend_facts,
    extract_shareholder_facts,
)
from halo.pdf_extract import ExtractResult, PageText

MAOTAI_PDF = "/tmp/halo_cninfo_test/600519_2025_annual.pdf"
BAOSTEEL_PDF = "/tmp/halo_sample/600019_2024_annual.PDF"

moutai = pytest.mark.skipif(not os.path.exists(MAOTAI_PDF), reason="真实年报不在本机")
baosteel = pytest.mark.skipif(not os.path.exists(BAOSTEEL_PDF), reason="真实年报不在本机")


def _pages(*texts: str) -> ExtractResult:
    return ExtractResult(
        pages=[PageText(page=i + 1, text=t) for i, t in enumerate(texts)],
        total_pages=len(texts),
    )


# ---------------------------------------------------------------------------
# 股东户数
# ---------------------------------------------------------------------------


def test_holder_count_both_dates():
    text = (
        "股东总数\n"
        "截至报告期末普通股股东总数(户) 255,892\n"
        "年度报告披露日前上一月末的普通股股东总数(户) 243,159\n"
    )
    got = {f["field"]: f["value"] for f in extract_shareholder_facts(_pages(text))}
    assert got["holder_count"] == 255892
    assert got["holder_count_prior"] == 243159


# ---------------------------------------------------------------------------
# 分红
# ---------------------------------------------------------------------------


def test_dividend_from_csrc_standard_table():
    """证监会标准表：口径一致，分红率直接给。"""
    text = (
        "⑤2022～2024 年度现金分红占净利润比例\n"
        "项目 2024 年预计 2024 年下半年 预计\n"
        "1 每股现金分红（含税）(元) 0.21 0.10\n"
        "2 现金分红总额（亿元） 45.16 21.50\n"
        "6 现金分红总额占合并报表归属于\n"
        "母公司股东的净利润比例（%） 61.34 76.33\n"
    )
    got = {f["field"]: f for f in extract_dividend_facts(_pages(text))}
    assert got["dividend_per_share"]["value"] == pytest.approx(0.21)
    assert got["dividend_total"]["value"] == pytest.approx(45.16e8)
    assert got["payout_ratio"]["value"] == pytest.approx(0.6134)


def test_dividend_narrative_must_not_fabricate_payout_ratio():
    """叙述式年报（茅台型）没有标准表 → 分红率**缺失**，不得用总额÷净利反算。

    茅台「累计派发现金红利 650.33 亿元」是全年含中期，而「每股 27.993 元」只是
    年度部分，反算出的分红率回答的是另一个问题。
    """
    text = (
        "继续落实现金分红三年规划，2025 年度累计派发现金红利 650.33 亿元"
        "（含 2025 年年度分红预案金额），分红总额创历史新高\n"
        "2025年年度利润分配拟向全体股东每股派发现金红利27.993元（含税）\n"
    )
    got = {f["field"]: f for f in extract_dividend_facts(_pages(text))}
    assert got["dividend_per_share"]["value"] == pytest.approx(27.993)
    assert got["dividend_total"]["value"] == pytest.approx(650.33e8)
    assert "payout_ratio" not in got, "无标准表时不得编造分红率"


# ---------------------------------------------------------------------------
# 主营构成
# ---------------------------------------------------------------------------


def test_segment_respects_table_unit():
    """单位：百万元 的表要换算成元。宝钢 2024 实测。"""
    text = (
        "(1). 主营业务分行业、分产品、分地区、分销售模式情况\n"
        "单位：百万元 币种：人民币\n"
        "主营业务分行业情况\n"
        "分行业 营业收入 营业成本 毛利率\n"
        "钢铁制造 322116 304546 3.4\n"
    )
    rev = {s["value_text"]: s for s in extract_business_segments(_pages(text))
           if s["field"].startswith("segment_revenue__")}
    assert rev["钢铁制造"]["value"] == pytest.approx(322116e6)


def test_segment_skips_page_without_unit_declaration():
    """找不到单位就不抽 —— 量纲错误的数据比没有数据危险得多。"""
    text = (
        "主营业务分行业情况\n"
        "分行业 营业收入 营业成本 毛利率\n"
        "钢铁制造 322116 304546 3.4\n"
    )
    assert extract_business_segments(_pages(text)) == []


def test_segment_groups_by_the_four_cut_dimensions():
    """分行业/分产品/分地区/分销售模式是**同一总量的四种切分**。

    茅台：酒类 1688 + 茅台酒 1465 + 国内 1639 + 直销 845 平铺出来会让人
    误加出 6749 亿的幻觉。field 名里带 __<维度> 就是为了不让下游这么算。
    """
    text = (
        "单位：元 币种：人民币\n"
        "主营业务分行业情况\n"
        "分行业 营业收入 营业成本 毛利率\n"
        "酒类 168800000000 14805900000 91.2\n"
        "主营业务分产品情况\n"
        "分产品 营业收入 营业成本 毛利率\n"
        "茅台酒 146500000000 9500000000 93.5\n"
        "其他系列酒 22300000000 5300000000 76.1\n"
        "主营业务分地区情况\n"
        "分地区 营业收入 营业成本 毛利率\n"
        "国内 163900000000 14500000000 91.2\n"
    )
    segs = extract_business_segments(_pages(text))
    dims = {s["field"].split("__", 1)[1] for s in segs if s["field"].endswith(("行业", "产品", "地区"))
            or "__" in s["field"]}
    assert "行业" in dims and "产品" in dims and "地区" in dims
    prod = [s for s in segs if s["field"] == "segment_revenue__产品"]
    assert len(prod) == 2, f"分产品表应有两行，实际 {len(prod)}"


def test_segment_ignores_income_statement_rows():
    """宝钢 2024 把利润表和主营构成表放在同一页，不能混进来。"""
    text = (
        "单位：元 币种：人民币\n"
        "主营业务分行业情况\n"
        "分行业 营业收入 营业成本 毛利率\n"
        "钢铁制造 251352000000 243000000000 3.4\n"
        "公司业务\n"
        "营业收入 322116000000 304546000000 5.5\n"
        "销售费用 1691000000 1691000000 0\n"
        "研发费用 3779000000 3779000000 0\n"
        "经营活动产生的现金流量净额 27736000000 0 100\n"
    )
    names = {s["value_text"] for s in extract_business_segments(_pages(text))}
    assert names == {"钢铁制造"}, f"混入了非分部行：{names}"


def test_segment_drops_mismatched_cost_column():
    """成本远高于收入说明列错位，宁可不给成本，也不产出负毛利率。"""
    text = (
        "单位：元 币种：人民币\n"
        "主营业务分产品情况\n"
        "分产品 营业收入 营业成本 毛利率\n"
        "某业务 1000000 9999999 0\n"
    )
    segs = extract_business_segments(_pages(text))
    fields = {s["field"] for s in segs}
    assert any(f.startswith("segment_revenue__") for f in fields)
    assert not any(f.startswith("segment_gross_margin") for f in fields), "错位时不应产出毛利率"


# ---------------------------------------------------------------------------
# 真实样本回归
# ---------------------------------------------------------------------------


@moutai
def test_moutai_real_segments_and_dividend():
    from halo.pdf_extract import extract_pages
    pages = extract_pages(MAOTAI_PDF)

    segs = {s["value_text"]: s for s in extract_business_segments(pages)
            if s["field"] == "segment_revenue__产品"}
    assert segs["茅台酒"]["value"] / 1e8 == pytest.approx(1465.0, abs=0.5)
    # 直销毛利率应高于批发代理（真实商业常识）
    margins = {s["value_text"]: s["value"] for s in extract_business_segments(pages)
                if s["field"] == "segment_gross_margin__销售模式"}
    assert margins["直销"] > margins["批发代理"]
    prod_margins = {s["value_text"]: s["value"] for s in extract_business_segments(pages)
                    if s["field"] == "segment_gross_margin__产品"}
    assert 0.90 < prod_margins["茅台酒"] < 0.95, f"茅台酒毛利率 {prod_margins}"

    div = {f["field"]: f for f in extract_dividend_facts(pages)}
    assert div["dividend_total"]["value"] / 1e8 == pytest.approx(650.33, abs=0.01)
    assert div["dividend_per_share"]["value"] == pytest.approx(27.993, abs=0.001)
    assert "payout_ratio" not in div

    hold = {f["field"]: f["value"] for f in extract_shareholder_facts(pages)}
    assert hold["holder_count"] == 255892


@baosteel
def test_baosteel_real_segments_in_million_yuan():
    from halo.pdf_extract import extract_pages
    pages = extract_pages(BAOSTEEL_PDF)

    names = {s["value_text"] for s in extract_business_segments(pages)
             if s["field"].startswith("segment_revenue__")}
    assert names == {"钢铁制造"}, f"应只有钢铁制造一个分部，实际 {names}"
    rev = [s for s in extract_business_segments(pages)
           if s["field"].startswith("segment_revenue__")][0]
    assert rev["value"] / 1e8 == pytest.approx(2513.5, abs=1.0), "百万元→元换算"

    div = {f["field"]: f for f in extract_dividend_facts(pages)}
    assert div["payout_ratio"]["value"] == pytest.approx(0.6134, abs=1e-4)
