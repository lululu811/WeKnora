"""披露持有人抽取器测试。

用**合成的 PDF 文本片段**做锚点测试，不造真实 PDF、不连网。三个必测场景：
含汇金行 / 无汇金行 / 表格跨页断裂。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etfholdings.extractor import (  # noqa: E402
    extract_holders,
    find_anchor_span,
    parse_rows,
)

# 真实季报的章节文本长得像这样：锚点 → 表头 → 10 行数据 → 下一章节
TEXT_WITH_HUIJIN = """
第三节 管理人报告

二、报告期内基金投资策略和运作分析
本报告期内本基金保持被动跟踪运作。

四、报告期末基金资产组合情况
序号  资产类别  金额（元）  占基金总资产的比例（%）

五、投资组合报告
1.1 期末基金资产组合情况

七、基金份额持有人信息
前十名基金份额持有人信息

序号  持有人名称  持有份额（份）  占总份额比例（%）
1  中国证券金融股份有限公司  8,500,000,000  18.75
2  中央汇金投资有限责任公司  7,200,000,000  15.88
3  香港中央结算有限公司  3,100,000,000  6.84
4  中国工商银行股份有限公司－易方达上证50增强指数证券投资基金  900,000,000  1.99
5  某某保险资金  500,000,000  1.10
6  某某养老产业基金  400,000,000  0.88
7  某某社保基金  300,000,000  0.66
8  某某证券投资基金  200,000,000  0.44
9  某某资产管理  150,000,000  0.33
10  某某投资管理  100,000,000  0.22

第九节 备查文件目录
"""

TEXT_NO_HUIJIN = """
七、基金份额持有人信息
前十大持有人

序号  持有人名称  持有份额（份）  占比（%）
1  某某保险资金一号  2,000,000,000  8.00
2  某某银行理财  1,500,000,000  6.00
3  某某证券公司  1,000,000,000  4.00
4  某某公募基金  800,000,000  3.20

第九节 备查文件目录
"""

# 表格跨页：下一页没有表头，序号直接从 3. 接着上一页
TEXT_PAGE_BREAK = """
七、基金份额持有人信息
前十名持有人

序号  持有人名称  持有份额（份）  占总份额比例（%）
1  中国证券金融股份有限公司  8,500,000,000  18.75
2  中央汇金投资有限责任公司  7,200,000,000  15.88
\f
序号  持有人名称  持有份额（份）  占总份额比例（%）
3  香港中央结算有限公司  3,100,000,000  6.84
4  某某保险资金  500,000,000  1.10

第九节 备查文件目录
"""

TEXT_NO_ANCHOR = """
第八节 备查文件目录
本基金无此类信息。
"""


# ---------- 锚点定位 ----------

def test_find_anchor_span_locates_section():
    span = find_anchor_span(TEXT_WITH_HUIJIN)
    assert span is not None
    start, end = span
    assert "前十名基金份额持有人信息" in TEXT_WITH_HUIJIN[start:start + 30]
    # 必须在下一个章节之前停住，不能把第九节吞进来
    assert "备查文件" not in TEXT_WITH_HUIJIN[start:end]


def test_find_anchor_span_absent_returns_none():
    assert find_anchor_span(TEXT_NO_ANCHOR) is None
    assert find_anchor_span("") is None


# ---------- 行解析 ----------

def test_parses_huijin_rows_and_marks_them():
    """场景 1：含汇金行。汇金/证金行都要被识别。"""
    rows = parse_rows(TEXT_WITH_HUIJIN)
    assert len(rows) == 10, f"应抽满 10 行，实得 {len(rows)}"

    names = [r["holder_name"] for r in rows]
    assert "中国证券金融股份有限公司" in names
    assert "中央汇金投资有限责任公司" in names


def test_huijin_flag_only_on_matching_rows():
    # is_huijin 是 extract_holders 打的标（parse_rows 是纯解析，不掺判断）
    rows = extract_holders(TEXT_WITH_HUIJIN, "510300.SH", "2026-06-30")
    flagged = {r["holder_name"]: r["is_huijin"] for r in rows}
    assert flagged["中国证券金融股份有限公司"] is True
    assert flagged["中央汇金投资有限责任公司"] is True
    assert flagged["香港中央结算有限公司"] is False


def test_parse_no_huijin_rows():
    """场景 2：无汇金行。普通持有人照常抽出，且没有任何行被误标为汇金。"""
    rows = extract_holders(TEXT_NO_HUIJIN, "510300.SH", "2026-06-30")
    assert len(rows) == 4
    assert not any(r["is_huijin"] for r in rows)


def test_page_break_does_not_lose_rows():
    """场景 3：表格跨页断裂。下一页无表头，序号从 3. 续上。"""
    rows = parse_rows(TEXT_PAGE_BREAK)
    names = [r["holder_name"] for r in rows]
    assert len(rows) == 4, f"跨页后应抽到 4 行，实得 {rows}"
    assert names == [
        "中国证券金融股份有限公司",
        "中央汇金投资有限责任公司",
        "香港中央结算有限公司",
        "某某保险资金",
    ]


def test_shares_and_pct_values():
    rows = parse_rows(TEXT_WITH_HUIJIN)
    first = rows[0]
    assert first["hold_share"] == pytest.approx(8.5e9)
    assert first["hold_pct"] == pytest.approx(18.75)


def test_share_unit_suffix_handled():
    text = """
前十名持有人
序号  持有人名称  持有份额  占比（%）
1  某某资金  1.23亿  5.00
"""
    rows = parse_rows(text)
    assert rows[0]["hold_share"] == pytest.approx(1.23e8)


def test_parse_missing_anchor_returns_empty():
    assert parse_rows(TEXT_NO_ANCHOR) == []
    assert parse_rows("") == []


# ---------- status 三态 ----------

def test_status_pending_without_total_shares():
    """没有交叉数据 → 全部 pending，不因缺数据就 disputed。"""
    rows = extract_holders(TEXT_WITH_HUIJIN, "510300.SH", "2026-06-30")
    assert len(rows) == 10
    assert all(r["status"] == "pending" for r in rows)
    # 原始值照常保留
    assert rows[0]["hold_share"] == pytest.approx(8.5e9)


def test_status_verified_when_reconciles():
    # 总量取 8.5e9/0.1875 → 18.75% 应能对上
    total = 8.5e9 / 0.1875
    rows = extract_holders(TEXT_WITH_HUIJIN, "510300.SH", "2026-06-30", total_shares=total)
    first = rows[0]
    assert first["status"] == "verified"
    assert first["hold_pct"] == pytest.approx(18.75)


def test_status_disputed_keeps_raw_numbers():
    """总量与披露口径对不上 → disputed，**原始占比与份额一字不改**。"""
    rows = extract_holders(TEXT_WITH_HUIJIN, "510300.SH", "2026-06-30", total_shares=1.0e12)
    first = rows[0]
    assert first["status"] == "disputed"
    assert first["hold_pct"] == pytest.approx(18.75)
    assert first["hold_share"] == pytest.approx(8.5e9)


def test_extract_returns_stable_shape_for_ghosts():
    """没有锚点 → 空列表，绝不抛异常。"""
    rows = extract_holders(TEXT_NO_ANCHOR, "510300.SH", "2026-06-30")
    assert rows == []