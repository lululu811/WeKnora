"""年报 PDF 逐页取文 + 关键页定位。

为什么不用 pdfplumber / PyMuPDF
------------------------------
只依赖 pypdf：HALO 要的只是「某页的文字 + 该页的页码」，不需要表格坐标、线框、
图片。引入更重的解析库意味着新依赖 + 新版本对齐，而 PDF 解析器的输出**不可能**
是权威源——它只是把巨潮原文搬过来，**任何数字都必须能回原文核对**。所以本模块
的产出是 ``(页码, 原始文本)``，页码对齐 PDF 阅读器（1 起始），人拿这个页码打开
PDF 就能看到同一行字。这条是设计的核心：抽取结果的可信度来自「可复核」，不是
来自「解析器多聪明」。

页码为什么从 1 开始
------------------
落库字段 ``store.source_page`` 的唯一用途是人工回原文裁决。PDF 阅读器（含
pypdf 自己的 0 起始下标）之外的任何口径都会让人翻错页。用 1 起始，页码与
「翻到第 31 页」这句话严格一致。

容错原则
--------
一份 143 页的年报里，pypdf 在个别页（尤其是含异常字体/编码的表格页）抛异常是
常态。**一页失败不能毁掉整份文件**——那等于让 34,992 名员工这个字段白白丢失。
所以单页失败记 warning 并返回空串，同时把失败页号记进 :class:`ExtractResult`，
让下游能据此决定这条记录该不该标 ``verified``（对应 store 的数据可信度约定）。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)


class PdfExtractError(Exception):
    """PDF 无法打开或整体不可解析（区别于单页失败）。"""


@dataclass(frozen=True)
class PageText:
    """单页文本。

    Attributes:
        page: 页码，**1 起始**，与 PDF 阅读器一致（可复核性要求，见模块 docstring）。
        text: 该页文本；解析失败时为空串（此时页码仍会出现在结果里，
            以便调用方知道「这一页存在但没抽出来」，而不是误以为文档到此为止）。
    """

    page: int
    text: str


@dataclass
class ExtractResult:
    """一次解析的完整结果，含成功/失败的如实统计。

    把 ``failed_pages`` 与 ``truncated`` 显式带出来，是为了让下游的
    ``store.status`` 决策有依据：漏页的年报里抽到的数字，不该被标成
    ``verified``——store 的 docstring 明确要求「取不到就标缺失，不估算」。
    """

    pages: List[PageText] = field(default_factory=list)
    total_pages: int = 0
    failed_pages: List[int] = field(default_factory=list)
    truncated: bool = False

    def page_map(self) -> Dict[int, str]:
        """页码 → 文本，便于按页取用。"""
        return {p.page: p.text for p in self.pages}

    def get(self, page_no: int) -> str:
        """取指定页文本，不存在返回空串。"""
        return self.page_map().get(page_no, "")


# 关键锚点关键词。括号里的说明是**实测**结论（贵州茅台 2025 年报，143 页）：
#   合并资产负债表 → 命中 1 页（真正的表在 p56；审计报告 p53 写的是
#                     「合并及母公司资产负债表」，中间有「及」字，故不误命中）
#   母公司资产负债表 → 命中 2 页（p59 是真表；p53 是审计报告正文里的**提及**）
#   在职员工的数量   → 命中 1 页（p31）
# 因此 locate_pages 一律返回**列表**且不做排序启发：p53 那个假阳性由调用方
# （规则抽取侧，凭「单位：元」等表头标记）裁决，本模块不去猜哪一页才是表。
ANCHOR_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "consolidated_balance_sheet": ("合并资产负债表",),
    "parent_balance_sheet": ("母公司资产负债表",),
    "employees": ("在职员工的数量", "员工情况"),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 不是合法数字，回退默认值 %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 不是合法数字，回退默认值 %s", name, default)
        return default


def _open_reader(pdf_path: str | os.PathLike[str]) -> PdfReader:
    """打开 PDF，失败转 :class:`PdfExtractError`。

    加密件单独处理：pypdf 遇到加密 PDF 不报错，``extract_text`` 时才抛 cryptic
    异常。巨潮上理论上不会有加密年报（法定披露要求可检索），但真遇到时给出
    「这是加密件」比让调用方看 pypdf 的原始堆栈有用得多。
    """
    path = os.fspath(pdf_path)
    if not os.path.isfile(path):
        raise PdfExtractError(f"PDF 不存在: {path}")
    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfExtractError(f"PDF 无法打开: {path}: {exc}") from exc
    try:
        if reader.is_encrypted:
            # 空口令能解开大部分「仅禁止复制/打印」型加密件。
            try:
                ok = reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 — 解密失败一律按打不开处理
                raise PdfExtractError(f"PDF 已加密且无法用空口令解密: {path}: {exc}") from exc
            if not ok:
                raise PdfExtractError(f"PDF 已加密（需口令）: {path}")
    except AttributeError:
        # 老版本 pypdf 没有 is_encrypted；不因为这点就拒绝处理。
        logger.debug("PdfReader 无 is_encrypted 属性，跳过加密检查: %s", path)
    return reader


def _page_count(reader: PdfReader, path: str) -> int:
    try:
        return len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — 损坏件在此处就会炸
        raise PdfExtractError(f"无法读取 PDF 页数（文件可能损坏）: {path}: {exc}") from exc


def extract_pages(
    pdf_path: str | os.PathLike[str],
    *,
    max_pages: Optional[int] = None,
    time_budget: Optional[float] = None,
) -> ExtractResult:
    """逐页提取文本。

    Args:
        pdf_path: 本地 PDF 路径。
        max_pages: 最多解析前多少页，默认 ``$HALO_PDF_MAX_PAGES``（500）。
            年报正文的事实表都在前 150 页内，设上限是为了挡住「一份 2000 页的
            募集说明书/更正后重述版」把进程和内存吃光。
        time_budget: 墙钟预算（秒），默认 ``$HALO_PDF_TIME_BUDGET``（300）。
            **诚实的边界**：pypdf 无法中断正在解析的那一页，所以预算只在**页与页
            之间**检查，最坏情况会超出预算一整页的解析时间。这是 pypdf 同步 API
            的硬限制，不是这里偷懒；要真正硬超时只能把解析放进子进程。

    Returns:
        :class:`ExtractResult`，页码 1 起始。单页失败返回空串并计入
        ``failed_pages``，不会中断整份文档。

    Raises:
        PdfExtractError: 文件不存在 / 无法打开 / 页数读不出。
    """
    path = os.fspath(pdf_path)
    if max_pages is None:
        max_pages = _env_int("HALO_PDF_MAX_PAGES", 500)
    if time_budget is None:
        time_budget = _env_float("HALO_PDF_TIME_BUDGET", 300.0)

    reader = _open_reader(path)
    total = _page_count(reader, path)
    if max_pages is not None and max_pages > 0 and total > max_pages:
        logger.info(
            "PDF %s 共 %d 页，超过 max_pages=%d，只解析前 %d 页",
            path, total, max_pages, max_pages,
        )

    result = ExtractResult(total_pages=total, truncated=bool(max_pages > 0 and total > max_pages))
    limit = min(total, max_pages) if max_pages and max_pages > 0 else total
    started = time.monotonic()

    for index in range(limit):
        # 页与页之间检查预算；单页内部无法中断（见 time_budget 说明）。
        if time_budget > 0 and time.monotonic() - started > time_budget:
            logger.warning(
                "PDF %s 解析超时（预算 %.1fs），已取 %d/%d 页，标记 truncated",
                path, time_budget, len(result.pages), total,
            )
            result.truncated = True
            break
        page_no = index + 1  # 1 起始，对齐 PDF 阅读器
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            # 一页失败 ≠ 整份文件失败。这一页留空串并记账，让下游把相关记录
            # 标成 disputed/pending，而不是静默当成「这一行没有数字」。
            logger.warning("PDF %s 第 %d 页解析失败（已跳过该页）: %s", path, page_no, exc)
            text = ""
            result.failed_pages.append(page_no)
        result.pages.append(PageText(page=page_no, text=text))

    logger.info(
        "PDF %s 解析完成: %d/%d 页可用, %d 页失败, truncated=%s",
        path, len(result.pages), total, len(result.failed_pages), result.truncated,
    )
    return result


def locate_pages(
    pages: Iterable[PageText],
    keywords: Sequence[str],
    *,
    start_page: int = 1,
    end_page: Optional[int] = None,
) -> List[int]:
    """按关键词定位命中页码（升序、去重）。

    Args:
        pages: 逐页文本。
        keywords: 任一命中即算。
        start_page: 只看第几页起（跳目录/封面）。
        end_page: 只看到第几页止（None 表示到底）。

    Returns:
        命中页码列表。**可能多页**：「固定资产」在茅台年报里命中 15 页（附注里
        每处提及都算），这是真实的关键词语义，不是 bug。调用方需要「表在哪一页」
        时应结合 :data:`ANCHOR_KEYWORDS` 那种更具体的锚点。
    """
    hits: List[int] = []
    for item in pages:
        if item.page < start_page:
            continue
        if end_page is not None and item.page > end_page:
            break
        if any(kw in item.text for kw in keywords):
            hits.append(item.page)
    return hits


def find_anchors(
    source: ExtractResult | str | os.PathLike[str],
    *,
    keywords: Optional[Dict[str, Tuple[str, ...]]] = None,
    start_page: int = 1,
    max_pages: Optional[int] = None,
) -> Dict[str, List[int]]:
    """定位三类关键锚点：合并/母公司资产负债表、员工情况。

    Args:
        source: :class:`ExtractResult`、已解析好的结果，或一个 PDF 路径。
        keywords: 覆盖默认的 :data:`ANCHOR_KEYWORDS`。
        start_page: 起始页。
        max_pages: 仅在传路径时才用（控制解析上限）。

    Returns:
        ``{锚点名: 命中页码列表}``。某个锚点没命中就给空列表，**不抛异常**——
        某只小盘公司的年报可能压根没有员工情况章节，这属于「数据缺失」，
        属于 store 的 ``pending`` 语义，不是解析错误。
    """
    table = keywords if keywords is not None else ANCHOR_KEYWORDS
    result = source if isinstance(source, ExtractResult) else extract_pages(source, max_pages=max_pages)
    return {name: locate_pages(result.pages, kws, start_page=start_page) for name, kws in table.items()}
