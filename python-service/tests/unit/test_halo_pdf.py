"""``halo.pdf_extract`` 单元测试。

绝大多数用例用**假 PdfReader**：不造二进制 PDF、不依赖磁盘文件，就能确定性地
覆盖「页码 1 起始」「单页失败不中断整份文档」「max_pages 截断」这些行为。
造真 PDF 来测这些是本末倒置——它们跟 PDF 格式无关，只跟我们的循环与容错逻辑有关。

最后有一组对真实年报的 smoke 用例（贵州茅台 2025 年报，143 页），文件不在就 skip。
它守的是**真实 PDF 上的关键词位置**，即人工复核时能否按页码翻到原文。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pytest

from halo import pdf_extract as pe
from halo.pdf_extract import (
    ANCHOR_KEYWORDS,
    ExtractResult,
    PageText,
    PdfExtractError,
    extract_pages,
    find_anchors,
    locate_pages,
)

# 真实年报的 smoke 测试素材。已实测：143 页、1.08 MB、员工数在 p31。
REAL_PDF = "/tmp/halo_cninfo_test/600519_2025_annual.pdf"
requires_real_pdf = pytest.mark.skipif(
    not os.path.isfile(REAL_PDF), reason=f"真实年报样本不存在: {REAL_PDF}"
)


# ----------------------------------------------------------------------
# 假 PDF
# ----------------------------------------------------------------------


class FakePage:
    def __init__(self, text: Optional[str] = None, boom: Optional[BaseException] = None) -> None:
        self._text = text
        self._boom = boom

    def extract_text(self) -> str:
        if self._boom is not None:
            raise self._boom
        return self._text or ""


class FakeReader:
    def __init__(self, pages: List[FakePage], encrypted: bool = False, decrypt_ok: bool = True):
        self.pages = pages
        self.is_encrypted = encrypted
        self._decrypt_ok = decrypt_ok

    def decrypt(self, _password: str) -> bool:
        return self._decrypt_ok


@pytest.fixture
def fake_pdf(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """装一个假 PdfReader，返回 ``(pages) -> path`` 工厂。

    仍然会在 tmp_path 下建一个**空文件**：``_open_reader`` 的存在性检查必须保留
    （真实链路里「PDF 根本没下下来」是最常见故障之一，用假 reader 测它才是自欺）。
    """

    def factory(pages: List[FakePage], **kwargs: Any):
        path = tmp_path / "x.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(pe, "PdfReader", lambda _p: FakeReader(pages, **kwargs))
        return path

    return factory


def page(n: int, text: str) -> PageText:
    return PageText(page=n, text=text)


# ----------------------------------------------------------------------
# 页码与基本提取
# ----------------------------------------------------------------------


def test_page_numbers_are_one_based(fake_pdf, tmp_path):
    """source_page 的用途是人工回原文裁决，页码必须与 PDF 阅读器一致。"""
    path = fake_pdf([FakePage("p1"), FakePage("p2"), FakePage("p3")])
    result = extract_pages(path)
    assert [p.page for p in result.pages] == [1, 2, 3]
    assert [p.text for p in result.pages] == ["p1", "p2", "p3"]
    assert result.total_pages == 3
    assert result.truncated is False


def test_single_page_failure_does_not_abort_document(fake_pdf, tmp_path, caplog):
    """143 页的年报里一页失败就整份作废，等于白丢 34,992 名员工这个字段。"""
    pages = [
        FakePage("p1"),
        FakePage(boom=RuntimeError("bad font encoding")),
        FakePage("p3"),
    ]
    path = fake_pdf(pages)
    with caplog.at_level("WARNING"):
        result = extract_pages(path)

    assert [p.page for p in result.pages] == [1, 2, 3]
    assert result.pages[1].text == ""
    assert result.pages[2].text == "p3", "第 3 页必须照常解析"
    assert result.failed_pages == [2]
    assert any("第 2 页" in r.getMessage() for r in caplog.records)


def test_failed_page_still_appears_in_result(fake_pdf, tmp_path):
    """失败页要占位：否则调用方会把「这一页没抽出来」误当成「文档到这为止」。"""
    path = fake_pdf([FakePage("p1"), FakePage(boom=ValueError("nope"))])
    result = extract_pages(path)
    assert len(result.pages) == 2
    assert result.get(2) == ""


def test_empty_text_becomes_empty_string_not_none(fake_pdf, tmp_path):
    path = fake_pdf([FakePage(None)])
    result = extract_pages(path)
    assert result.pages[0].text == ""


# ----------------------------------------------------------------------
# 打开失败的处理
# ----------------------------------------------------------------------


def test_missing_file_raises_domain_error(tmp_path):
    with pytest.raises(PdfExtractError, match="不存在"):
        extract_pages(tmp_path / "nope.pdf")


def test_corrupt_file_raises_domain_error(monkeypatch: pytest.MonkeyPatch, tmp_path):
    def boom(_path: str) -> None:
        raise pe.PdfReadError("EOF marker not found")

    path = tmp_path / "x.pdf"
    path.write_bytes(b"not a pdf at all")
    monkeypatch.setattr(pe, "PdfReader", boom)
    with pytest.raises(PdfExtractError, match="无法打开"):
        extract_pages(path)


def test_encrypted_pdf_raises_domain_error(fake_pdf, tmp_path):
    path = fake_pdf([FakePage("p1")], encrypted=True, decrypt_ok=False)
    with pytest.raises(PdfExtractError, match="加密"):
        extract_pages(path)


def test_encrypted_pdf_with_empty_password_is_readable(fake_pdf, tmp_path):
    """「仅禁止打印/复制」型加密件用空口令能解开，不该被一刀切拒绝。"""
    path = fake_pdf([FakePage("p1"), FakePage("p2")], encrypted=True, decrypt_ok=True)
    assert len(extract_pages(path).pages) == 2


# ----------------------------------------------------------------------
# 上限保护
# ----------------------------------------------------------------------


def test_max_pages_truncates(fake_pdf, tmp_path, caplog):
    """2000 页的募集说明书不能把进程吃光。"""
    path = fake_pdf([FakePage(f"p{i}") for i in range(1, 201)])
    with caplog.at_level("INFO"):
        result = extract_pages(path, max_pages=50)
    assert len(result.pages) == 50
    assert result.total_pages == 200
    assert result.truncated is True
    assert any("max_pages" in r.getMessage() for r in caplog.records)


def test_max_pages_from_env(fake_pdf, tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HALO_PDF_MAX_PAGES", "3")
    path = fake_pdf([FakePage(f"p{i}") for i in range(1, 11)])
    assert len(extract_pages(path).pages) == 3


def test_time_budget_stops_between_pages(fake_pdf, tmp_path, caplog, monkeypatch: pytest.MonkeyPatch):
    """预算只在页与页之间检查——pypdf 无法中断正在解析的那一页。"""
    path = fake_pdf([FakePage(f"p{i}") for i in range(1, 21)])
    clock = {"t": 0.0}

    def fake_monotonic() -> float:
        clock["t"] += 1.0  # 每页推进 1 秒
        return clock["t"]

    monkeypatch.setattr(pe.time, "monotonic", fake_monotonic)
    with caplog.at_level("WARNING"):
        result = extract_pages(path, time_budget=3.0)

    assert 0 < len(result.pages) < 20
    assert result.truncated is True
    assert any("超时" in r.getMessage() for r in caplog.records)


def test_invalid_env_falls_back_to_default(fake_pdf, tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HALO_PDF_MAX_PAGES", "abc")
    path = fake_pdf([FakePage("p1")])
    assert len(extract_pages(path).pages) == 1


# ----------------------------------------------------------------------
# 关键页定位
# ----------------------------------------------------------------------


def test_locate_pages_sorted_and_deduped():
    pages = [
        page(1, "封面"),
        page(12, "母公司资产负债表 编制单位"),
        page(31, "在职员工的数量合计 34,992"),
        page(56, "合并资产负债表"),
        page(57, "固定资产 存货"),
    ]
    assert locate_pages(pages, ["合并资产负债表"]) == [56]
    assert locate_pages(pages, ["母公司资产负债表", "合并资产负债表"]) == [12, 56]
    assert locate_pages(pages, ["不存在的东西"]) == []


def test_locate_pages_any_keyword_matches():
    pages = [page(31, "七、报告期末母公司和主要子公司的员工情况"), page(8, "公司简介")]
    assert locate_pages(pages, ANCHOR_KEYWORDS["employees"]) == [31]


def test_locate_pages_respects_range():
    pages = [page(1, "固定资产"), page(5, "固定资产"), page(9, "固定资产")]
    assert locate_pages(pages, ["固定资产"], start_page=5) == [5, 9]
    assert locate_pages(pages, ["固定资产"], end_page=5) == [1, 5]


def test_locate_pages_can_return_many_hits():
    """「固定资产」在茅台年报命中 15 页——这是真实关键词语义，不是 bug。"""
    pages = [page(i, "固定资产") for i in (13, 14, 57, 59, 65)]
    assert locate_pages(pages, ["固定资产"]) == [13, 14, 57, 59, 65]


def test_find_anchors_keys_and_missing_are_empty(fake_pdf, tmp_path):
    path = fake_pdf(
        [
            FakePage("封面"),
            FakePage("母公司资产负债表 2025 年 12 月 31 日 编制单位"),
            FakePage("在职员工的数量合计 34,992"),
            FakePage("合并资产负债表 2025 年 12 月 31 日 编制单位"),
        ]
    )
    anchors = find_anchors(path)
    assert set(anchors) == set(ANCHOR_KEYWORDS)
    assert anchors["consolidated_balance_sheet"] == [4]
    assert anchors["parent_balance_sheet"] == [2]
    assert anchors["employees"] == [3]


def test_find_anchors_returns_empty_list_when_absent(fake_pdf, tmp_path):
    """缺章节 = 数据缺失（store 的 pending 语义），不是解析错误，不该抛异常。"""
    path = fake_pdf([FakePage("只有目录")])
    anchors = find_anchors(path)
    assert anchors["employees"] == []


def test_find_anchors_accepts_precomputed_result():
    result = ExtractResult(pages=[page(31, "在职员工的数量合计 34,992")], total_pages=31)
    assert find_anchors(result)["employees"] == [31]


def test_find_anchors_allows_keyword_override(fake_pdf, tmp_path):
    path = fake_pdf([FakePage("在建工程")])
    anchors = find_anchors(path, keywords={"cip": ("在建工程",)})
    assert anchors == {"cip": [1]}


def test_anchor_keywords_cover_the_three_required_sections():
    assert "合并资产负债表" in ANCHOR_KEYWORDS["consolidated_balance_sheet"]
    assert "母公司资产负债表" in ANCHOR_KEYWORDS["parent_balance_sheet"]
    assert "在职员工的数量" in ANCHOR_KEYWORDS["employees"]


# ----------------------------------------------------------------------
# 真实年报 smoke
# ----------------------------------------------------------------------


@requires_real_pdf
def test_real_annual_report_page_count():
    assert extract_pages(REAL_PDF).total_pages == 143


@requires_real_pdf
def test_real_annual_report_anchor_pages():
    """人工复核要能按页码翻到原文：员工数必须在 p31，合并资产负债表必须命中。"""
    anchors = find_anchors(REAL_PDF)
    assert anchors["employees"], "未找到员工章节"
    assert anchors["consolidated_balance_sheet"], "未找到合并资产负债表"


@requires_real_pdf
def test_real_annual_report_employee_line_is_readable():
    """pypdf 要能把「在职员工的数量合计」连数字一起抽出来，否则规则抽取无从下手。"""
    result = extract_pages(REAL_PDF)
    page_no = find_anchors(result)["employees"][0]
    text = result.get(page_no)
    assert "在职员工的数量合计" in text
    assert "34,992" in text
