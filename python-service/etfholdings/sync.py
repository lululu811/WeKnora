"""
定期报告披露持有人 — 巨潮取公告 + PDF + pypdf 抽文本。

复用 halo 的 CninfoSource
------------------------
限速、退避、orgId 解析、PDF 流式下载全部复用 `halo.cninfo_source`，**不重复实现**，
也**不绕过限速**。ETF 公告的 category 与年报不同（基金季报走
`category_sjdbg_szsh` 等），这里仍走 halo 的 report_type→category 映射，
保证"检索的是哪一类"与"落库记的是哪一期"来自同一份定义。

PDF → 文本
----------
`pypdf` 只取文本层，不做表格结构还原 —— 与 halo 的判断一致：ETF 报告的
持有人表在文本层里是可按行正则定位的，表格还原在跨管理人版式上反而更不稳。

⚠️ 实测连通性结论（2026-10-08，务必先读再调）
-------------------------------------------
**巨潮公开公告接口查不到 ETF 定期报告。** 已穷举：
* `szse_stock.json` 官方映射表**不含基金**（6259 条全是股票）→ ETF 的
  orgId 只能走 `fallback_orgid`，而它按板块前缀猜，`510300`（沪市）落到
  `gssz0510300`——**猜错**，日志里会明说"回退前缀 orgId"。
* 用 `stock="510300,gssh0510300"` / `"510300,9900024112"`（真 orgId）、
  `column` 取 `szse`/`sse`/`fund`、`plate=fund`、`category=category_sjdbg_szsh`
  组合查询，`totalAnnouncement` **全部为 0**。

因此本模块当前的实际行为是：**固定 shape + 明确的 reason**
（`巨潮未查到该 ETF 的定期报告公告`），不抛异常、不假装成功。抽取器、
落库、读取链路本身是完整可用的 —— 一旦拿到可用的公告入口（或换用
基金业协会/交易所披露源），只需替换 `sync_one_etf` 里的定位环节。
**不要**为了"跑出数据"而绕过限速或伪造 orgId。

增量
----
只处理库内**没有**的报告期。定期报告一年四期，全量重抓会把巨潮打到限流，
且绝大多数时候是同一份 PDF 重下 —— 增量是唯一合理选择。
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from halo.cninfo_source import (
    CninfoSource,
    CninfoError,
    NoFilingFoundError,
    report_type_of,
)
from halo.store import REPORT_ANNUAL, REPORT_H1, REPORT_Q1, REPORT_Q3

from .extractor import extract_holders

logger = logging.getLogger(__name__)

# 遍历顺序按报告期从早到晚，拉到更早的期数才需要停止。
_REPORT_TYPES = (REPORT_Q1, REPORT_H1, REPORT_Q3, REPORT_ANNUAL)


def extract_pdf_text(path: str) -> str:
    """PDF → 纯文本。解析失败返回空串（不抛）。

    页与页之间插 `\n\f\n`（换页 + 换页符）而不是直接拼接：pypdf 的文本层
    **不保证页边界**，直接拼接会把上一页表格的尾巴和下一页表头连成一行，
    表格跨页断裂时就会抽出一行粘连的脏数据。
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        logger.error("未安装 pypdf，无法抽取 PDF 文本")
        return ""
    try:
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n\f\n".join(pages)
    except Exception as exc:  # noqa: BLE001 —— 解析失败不抛给调用方
        logger.warning("PDF 文本抽取失败 %s: %s", path, exc)
        return ""


def _report_period(title: str, report_type: str) -> Optional[str]:
    """从标题取报告期 `YYYY-MM-DD`（季报取季末，中报/年报取 12-31 或 06-30）。

    取不到年份返回 None —— 报告期是这个表的去重键，宁可漏一条也不能编一个。
    """
    m = re.search(r"(\d{4})\s*年", title)
    if not m:
        return None
    year = int(m.group(1))
    if report_type == REPORT_H1:
        return f"{year}-06-30"
    if report_type == REPORT_Q1:
        return f"{year}-03-31"
    if report_type == REPORT_Q3:
        return f"{year}-09-30"
    return f"{year}-12-31"


def sync_one_etf(
    code: str,
    known_periods: Sequence[str],
    *,
    dest_dir: Optional[str] = None,
    total_shares_by_period: Optional[Dict[str, float]] = None,
    max_report_types: int = 4,
) -> Dict[str, Any]:
    """单只 ETF 的增量同步。

    Returns:
        **固定 shape**：`{thscode, fetched, periods, per_period, reason}`。
        查无公告 / PDF 下载失败 / 抽取失败都走 reason 分支，**不抛异常**。
    """
    known = set(known_periods or [])
    result: Dict[str, Any] = {
        "thscode": code,
        "fetched": 0,
        "periods": [],
        "per_period": {},
        "reason": None,
    }

    try:
        src = CninfoSource()
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"初始化巨潮数据源失败: {exc}"
        return result

    dest_dir = dest_dir or os.path.join(tempfile.gettempdir(), "weknora_etf_holdings")

    for rtype in _REPORT_TYPES[:max_report_types]:
        try:
            ann = src.find_filing(code, report_type=rtype, max_scan_pages=2, required=False)
        except (CninfoError, NoFilingFoundError) as exc:
            logger.info("ETF %s 查 %s 公告失败: %s", code, rtype, exc)
            continue
        if ann is None:
            continue

        # 二次确认报告类型：halo 的 find_filing 已经按 report_type 过滤过，
        # 这里再确认一道是为了**不依赖**那个过滤一定存在。ETF 公告标题里
        # 「半年度报告」含「年度报告」三个字，若把半年报当年报处理，报告期
        # 会被算成 12-31，整期数据记错年份且后续增量永远跳过。
        if report_type_of(ann.title) != rtype:
            continue

        period = _report_period(ann.title, rtype)
        if period is None:
            result["per_period"][rtype] = "标题里取不到报告期年份，已跳过"
            continue
        if period in known:
            # 增量：库内已有该报告期，跳过下载。
            result["per_period"][period] = "已有该报告期（增量跳过）"
            continue

        try:
            path = src.download_pdf(ann, dest_dir)
        except CninfoError as exc:
            result["per_period"][period] = f"PDF 下载失败: {exc}"
            continue

        text = extract_pdf_text(path)
        if not text.strip():
            result["per_period"][period] = "PDF 无文本层（可能是扫描件），未抽取"
            continue

        rows = extract_holders(
            text,
            thscode=code,
            report_period=period,
            total_shares=(total_shares_by_period or {}).get(period),
            source_title=ann.title,
        )
        if not rows:
            result["per_period"][period] = "未定位到「前十名持有人」章节"
            continue

        result["per_period"][period] = {"rows": rows, "fetched": len(rows)}
        result["periods"].append(period)
        result["fetched"] += len(rows)
        # 新拿到一期后加入 known，避免同一份 PDF 被下一个 report_type 重复处理。
        known.add(period)

    if not result["fetched"] and not result["reason"]:
        result["reason"] = (
            "无新增报告期（库内已覆盖全部已披露定期报告）"
            if result["per_period"]
            else "巨潮未查到该 ETF 的定期报告公告"
        )
    return result


def sync_pool(
    pool: Sequence[Dict[str, str]],
    known_periods: Dict[str, Sequence[str]],
    total_shares_by_code: Optional[Dict[str, Dict[str, float]]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """池内批量增量同步。

    **始终返回固定 shape**（halo 不变式）：即使整池全失败也回
    `{ok, per_etf: {...}, message, rows}`。
    """
    per_etf: Dict[str, Any] = {}
    all_rows: List[Dict[str, Any]] = []
    total_shares_by_code = total_shares_by_code or {}

    for entry in pool or []:
        code = (entry or {}).get("code")
        if not code:
            continue
        try:
            r = sync_one_etf(
                code,
                known_periods.get(code, []),
                total_shares_by_period=total_shares_by_code.get(code),
            )
        except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整池
            r = {
                "thscode": code,
                "fetched": 0,
                "periods": [],
                "per_period": {},
                "reason": f"同步异常: {exc}",
            }
        per_etf[code] = r
        for period_info in (r.get("per_period") or {}).values():
            if isinstance(period_info, dict) and period_info.get("rows"):
                all_rows.extend(period_info["rows"])

    ok = bool(all_rows)
    return {
        "ok": ok,
        "per_etf": per_etf,
        "rows": all_rows,
        "message": (
            f"新增 {len(all_rows)} 条持有人记录"
            if ok
            else "无新增披露记录（可能本期尚未披露，或库内已覆盖）"
        ),
    }