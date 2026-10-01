"""治理诚信与 ESG 事实抽取测试。

三个修复点都是真实年报暴露的，且失败方式都是「静默给出错误或缺失的结论」：

1. 勾选项可能距标题三行 —— 茅台 2025 年报「高管被留置」那一节，
   标题被折成两行、答案在第 3 行，只看下一行会漏掉最该抓到的风险信号。
2. 年报里「污染许可总量」与「实际排放量」并列出现且数值差几倍，
   抽错口径会把排放严重的企业说成排放轻微。
3. 「保留意见」这四个字在「二、形成审计意见的基础」这类标题里也出现，
   全文计数会把标题当成结论。
"""

import pytest

from halo.facts import _parse_checkbox, extract_emission_facts, extract_facts
from halo.pdf_extract import ExtractResult, PageText

MAOTAI_PDF = "/tmp/halo_cninfo_test/600519_2025_annual.pdf"
BAOSTEEL_PDF = "/tmp/halo_sample/600019_2024_annual.PDF"

moutai = pytest.mark.skipif(
    not __import__("os").path.exists(MAOTAI_PDF), reason="真实年报样本不在本机"
)
baosteel = pytest.mark.skipif(
    not __import__("os").path.exists(BAOSTEEL_PDF), reason="真实年报样本不在本机"
)


def _pages(text: str, page_no: int = 1) -> ExtractResult:
    return ExtractResult(pages=[PageText(page=page_no, text=text)], total_pages=1)


# ---------------------------------------------------------------------------
# 勾选项解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("□适用 √不适用", False),   # 不适用被勾 → 否
    ("√适用 □不适用", True),    # 适用被勾 → 是
    ("□是 √否", False),
    ("√是 □否", True),
    ("这里没有勾选项", None),
])
def test_parse_checkbox(line, expected):
    assert _parse_checkbox(line) is expected


def test_checkbox_found_two_lines_below_heading():
    """茅台真实排版：标题折行，答案隔两行。"""
    text = (
        "十、上市公司及其董事、高级管理人员、控股股东、实际控制人涉嫌违法违规、受到处罚及整改\n"
        "情况\n"
        "√适用 □不适用\n"
    )
    r = extract_facts(_pages(text, 39))
    fields = {f["field"]: f for f in r["facts"]}
    assert fields["executive_penalty"]["value"] == 1.0, "隔了两行仍应抽到"
    assert "留置" not in fields["executive_penalty"]["raw_text"]  # 只是结构占位


# ---------------------------------------------------------------------------
# 审计意见：章节限定
# ---------------------------------------------------------------------------


def test_audit_opinion_ignores_heading_of_next_section():
    """"二、形成审计意见的基础"里的「审计意见」不是结论。"""
    text = (
        "一、审计意见\n"
        "我们认为，后附的财务报表在所有重大方面按照企业会计准则的规定编制，"
        "公允反映了公司财务状况。\n"
        "二、形成审计意见的基础\n"
        "我们按照中国注册会计师审计准则的规定执行了审计工作。\n"
    )
    r = extract_facts(_pages(text))
    f = {x["field"]: x for x in r["facts"]}["audit_opinion"]
    assert f["value_text"] == "标准无保留意见"


def test_audit_opinion_detects_non_standard():
    text = (
        "一、审计意见\n"
        "除上述事项外，我们认为，后附的财务报表在所有重大方面按照企业会计准则的规定编制。\n"
        "我们提请财务报表使用者注意财务报表附注中披露的保留事项，形成保留意见。\n"
    )
    r = extract_facts(_pages(text))
    f = {x["field"]: x for x in r["facts"]}["audit_opinion"]
    assert f["value_text"] == "保留意见"


def test_audit_opinion_not_forced_when_unrecognised():
    """认不出就说认不出，不能默认「标准无保留」。"""
    text = "一、审计意见\n本段无法判断意见类型。\n"
    r = extract_facts(_pages(text))
    assert "audit_opinion" not in {x["field"] for x in r["facts"]}


# ---------------------------------------------------------------------------
# ESG 排放口径
# ---------------------------------------------------------------------------


def test_emission_prefers_actual_over_permit_quota():
    """宝钢 2024 年报 p58 的真实文本：许可总量与实际排放量并列。"""
    text = (
        "报告期内公司废气中主要污染许可总量：颗粒物为 21595.9 吨、"
        "二氧化硫为 30139.7 吨，氮氧化物为 55974.8 吨，"
        "公司 2024 年实际排放量颗粒物 4315.8 吨、二氧化硫 6213.4 吨，"
        "氮氧化物 18771.4 吨\n"
    )
    got = {f["field"]: f["value"] for f in extract_emission_facts(_pages(text, 58))}
    assert got["emission_particulate"] == 4315.8, "应取实际排放量，不是许可总量 21595.9"
    assert got["emission_so2"] == 6213.4, "应取实际排放量，不是许可总量 30139.7"
    assert got["emission_nox"] == 18771.4


def test_no_emission_means_no_disclosure_not_zero():
    """轻资产公司抽不到排放就该报「无环境披露」，不能返回 0 当成零排放。"""
    text = "公司主要业务为白酒酿造与销售，不涉及污染物排放。\n"
    r = extract_facts(_pages(text))
    assert r["emissions"] == {}
    assert r["has_environment_disclosure"] is False


# ---------------------------------------------------------------------------
# 真实样本回归
# ---------------------------------------------------------------------------


@moutai
def test_moutai_real_filing():
    from halo.pdf_extract import extract_pages
    r = extract_facts(extract_pages(MAOTAI_PDF))
    f = r["governance"]

    # 2026-03 高管被留置 —— 年报「√适用」。这是整份年报最该被抽到的风险信号，
    # 也是唯一一个「抽不到就等于漏报」的字段。
    assert f["executive_penalty"]["value"] == 1.0
    assert f["regulatory_penalty_3y"]["value"] == 0.0
    assert f["internal_control_nonstandard"]["value"] == 0.0
    assert f["internal_control_opinion"]["value_text"] == "标准的无保留意见"
    assert f["audit_opinion"]["value_text"] == "标准无保留意见"
    # 白酒年报里没有量化排放，不能编一个出来
    assert r["has_environment_disclosure"] is False


@baosteel
def test_baosteel_real_filing():
    from halo.pdf_extract import extract_pages
    r = extract_facts(extract_pages(BAOSTEEL_PDF))
    e = r["emissions"]
    assert r["has_environment_disclosure"] is True
    assert e["emission_particulate"]["value"] == 4315.8
    assert e["emission_so2"]["value"] == 6213.4
    assert e["emission_nox"]["value"] == 18771.4
    # 宝钢用另一种模板，没有「是否非标」是非题 —— 抽不到就留空，
    # 不从「标准的无保留意见」倒推：事实层不该混入推断。
    assert "internal_control_nonstandard" not in r["governance"]


@moutai
@baosteel
def test_both_filings_share_the_same_fact_shape():
    """两个行业、两种模板，治理类字段名集合应当一致（宝钢允许少一项）。"""
    from halo.pdf_extract import extract_pages
    a = {f["field"] for f in extract_facts(extract_pages(MAOTAI_PDF))["facts"]}
    b = {f["field"] for f in extract_facts(extract_pages(BAOSTEEL_PDF))["facts"]}
    common = {"audit_opinion", "internal_control_opinion", "regulatory_penalty_3y"}
    assert common <= a and common <= b
