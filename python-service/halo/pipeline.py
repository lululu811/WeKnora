"""HALO 年报事实链路编排。

把「巨潮检索 → 下载 → 解析 → 抽取 → 双通道合并 → 对账 → 落库」串成一步。

同步与异步
----------
巨潮的 urllib 请求和 pypdf 的逐页解析都是同步阻塞的，**单份年报实测要几十秒**
（茅台 2025 年报 143 页）。直接写在 async 端点里会把 FastAPI 的事件循环整个
占住，一个请求进来期间所有其它请求都排队。所以这里把重活整体包进
``asyncio.to_thread``，并提供 ``*_async`` 入口供端点直接 await。

依赖注入
--------
``reconciler`` 与 ``llm_extractor`` 都是可选的、调用方注入的依赖，而不是在这里
import 具体实现。理由有二：

* LLM 通道本服务没有客户端，实现属于未来；留成注入点意味着规则通道今天就能
  独立跑通，LLM 接上时不用改编排。
* 对账要读 DuckDB，测试里要换成假数据源；import 具体实现就没法测。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Protocol

from .cninfo_source import CninfoSource, LocalFiling, NoFilingFoundError
from .extractor import Fact, extract_pages as extract_rule_pages, merge_channels
from .facts import extract_facts
from .facts_finance import extract_finance_facts
from .pdf_extract import ExtractResult, extract_pages, find_anchors
from .store import (
    REPORT_ANNUAL,
    REPORT_H1,
    REPORT_Q1,
    REPORT_Q3,
    STATUS_PENDING,
    STATUS_VERIFIED,
    FactStore,
)

logger = logging.getLogger(__name__)


# Reconciler 是对账能力的协议：给定股票、报告期与已抽取记录，返回**更新后**的
# 记录。真实实现是 reconcile.py 里的 reconcile_records + apply_reconcile，
# 端点层一般不注入本参数（走默认实现），测试才注入假的。
class Reconciler(Protocol):
    async def __call__(  # type: ignore[misc]
        self, thscode: str, period: str, report_type: str, records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:  # pragma: no cover - 协议声明
        ...


# report_type → 报告期截止日。store 的 period 键是这个值，不是「披露年份」：
# 2025 年报在 2026-04 披露，用披露年份会把 2025 年报记成 2026，评分内核取数
# 直接错一整年。
_PERIOD_SUFFIX = {
    REPORT_ANNUAL: "12-31",
    REPORT_H1: "06-30",
    REPORT_Q1: "03-31",
    REPORT_Q3: "09-30",
}


def normalize_thscode(code: str) -> str:
    """把 6 位代码补成 hithink 的带后缀形式（600519 -> 600519.SH）。

    三处用的格式本来就不一致，而**格式不一致的后果是静默的**：

    * 巨潮检索与年报 PDF 只认 6 位；
    * DuckDB（financials / market）一律用带后缀的 ``600519.SH``；
    * 事实库的 thscode 若存成 6 位，对账时按 ``600519.SH`` 去 join 就会
      查不到参照，**不报错**，只是把每条记录都判成「对不了」——看起来像
      「这只股票没数据」，实际是格式错了。

    统一成带后缀存储，对账、评分、查询三处才不会各查各的。
    """
    c = (code or "").strip().upper()
    if "." in c:
        return c
    if not c.isdigit() or len(c) != 6:
        return c
    if c[0] in ("6", "9"):
        return f"{c}.SH"     # 沪市主板 / 科创板(688) / B股
    if c[0] in ("0", "2", "3"):
        return f"{c}.SZ"     # 深市主板 / 创业板(300)
    if c[0] in ("4", "8"):
        return f"{c}.BJ"     # 北交所
    return c


def period_of(year: int, report_type: str) -> str:
    suffix = _PERIOD_SUFFIX.get(report_type)
    if suffix is None:
        raise ValueError(f"未知 report_type：{report_type}")
    return f"{year}-{suffix}"


def default_dest_dir() -> str:
    """PDF 落盘目录。

    与事实库同目录但独立子目录：PDF 大（一份 1–10 MB）且可重新下载，事实库小
    且是成果物，混在一起会让清理缓存时误删事实库。
    """
    base = os.path.expanduser(
        os.getenv("HALO_CACHE_DIR", "~/.hithink-finance/halo-pdfs")
    )
    return base


@dataclass
class SyncResult:
    thscode: str
    report_type: str
    period: Optional[str] = None
    cached: bool = False
    pages_extracted: int = 0
    anchors: Dict[str, List[int]] = dc_field(default_factory=dict)
    records: List[Dict[str, Any]] = dc_field(default_factory=list)
    written: int = 0
    status_summary: Dict[str, int] = dc_field(default_factory=dict)
    missing_fields: List[str] = dc_field(default_factory=list)
    note: str = ""


# HALO 六维评分需要的字段集合。列在这里而不是散落各处，是为了让「缺了什么」
# 能被一次性算出来如实上报——缺字段是要说给用户听的，不是静默少一项。
REQUIRED_FIELDS = (
    "fixed_assets",
    "construction_in_progress",
    "inventory",
    "intangible_assets",
    "goodwill",
    "employees_total",
)


def _cache_hit(store: FactStore, thscode: str, period: str, report_type: str) -> bool:
    rows = store.query(
        thscode, period=period, report_type=report_type, only_verified=True
    )
    return bool(rows)


#: 表格跨页：表标题与表体常被分页隔开。实测茅台 2025 年报「合并资产负债表」
#: 的标题在 p56，资产数据在 p57，负债与权益续页在 p58 —— 只扫锚点命中页的
#: 话一条都抽不到，pipeline 退化成「全靠母公司表凑数」的样子。
#:
#: 这个 bug 有迷惑性：结果**不是空**，而是拿到旁边母公司资产负债表的数当合并
#: 口径用（茅台的 p59 恰好也有存货/固定资产/在建工程/无形资产），于是
#: fixed_assets 之类字段有值、看起来正常，差的是一整套报表口径。所以必须后延。
#:
#: 后延不会把母公司数据误标成合并：scope 是由**每一页自己的**表标题决定的
#: 状态机，p59 页内含「母公司资产负债表」标题，抽出来自然是 parent。
TABLE_SPILL_PAGES = 2


def _pages_to_scan(pages: ExtractResult, anchor_pages: set) -> List[int]:
    """锚点页 + 其后若干页（表格跨页补偿）。"""
    available = pages.page_map()
    out = set()
    for p in anchor_pages:
        for delta in range(TABLE_SPILL_PAGES + 1):
            if p + delta in available:
                out.add(p + delta)
    return sorted(out)


#: 抽取锚点。在 pdf_extract.ANCHOR_KEYWORDS 基础上补了利润表 —— 净利润这个
#: 字段必须有明确的表上下文才抽得对（见 FIELD_ANCHORS）。
HALO_ANCHORS: Dict[str, tuple] = {
    "consolidated_balance_sheet": ("合并资产负债表",),
    "parent_balance_sheet": ("母公司资产负债表",),
    "consolidated_income_statement": ("合并利润表", "合并利润表（续）"),
    "parent_income_statement": ("母公司利润表",),
    "employees": ("在职员工的数量", "员工情况"),
}

#: 锚点分组：同一组内的锚点页一起扫描。
_ANCHOR_GROUPS: Dict[str, tuple] = {
    "balance": ("consolidated_balance_sheet", "parent_balance_sheet"),
    "income": ("consolidated_income_statement", "parent_income_statement"),
    "employees": ("employees",),
}

#: 字段 → 允许出现的锚点组。
#:
#: 限定的原因是**同词不同义**。实测茅台 2025 年报里「净利润」单独命中 8 页，
#: 其中 p33 是现金分红比例表：
#:
#:     通股股东的净利润的比率（%） 42.56
#:
#: 那是**比率**，不是金额。但它长得和正常数字毫无区别，字段名还正好叫
#: 「净利润」——不限定锚点就会以一个完全合理的形态落库。而 net_profit
#: 虽然有 DuckDB 对账口径（1% 阈值）能兜住，但把安全性寄托在「恰好这个字段
#: 有尺子可量」上不成立：任何新增的、暂时没有对账口径的字段都会重蹈覆辙。
FIELD_ANCHORS: Dict[str, tuple] = {
    "fixed_assets": ("balance",),
    "construction_in_progress": ("balance",),
    "inventory": ("balance",),
    "intangible_assets": ("balance",),
    "goodwill": ("balance",),
    "total_assets": ("balance",),
    "net_profit": ("income",),
    "employees_total": ("employees",),
}


def _extract_by_anchors(pages: ExtractResult, anchors: Dict[str, List[int]]) -> List[Fact]:
    """按锚点分组扫描，并只保留该组允许的字段。

    每个分组内**必须**走 ``extract_pages`` 批量入口而不是逐页调
    ``extract_by_rule``：年报的表头元信息（表标题、单位）只在标题页出现，
    数据在续页。逐页调用等于每页都从默认状态重来，于是

    * 母公司表续页（p60）不含「母公司资产负债表」标题 → 回落成合并口径，
      把母公司数据混进合并数据；
    * 合并表续页（p57）不含「单位：元」→ 单位判空，折算基数随之丢失。

    两者都不报错，只是安静地给出错误的记录。

    兜底分支（没锚定到任何页）退化为全量扫描且不限制字段：这时宁可多抽，
    也要让规则通道有机会工作——它的失败模式是「抽不到」，不会凭空造数。
    """
    rule_facts: List[Fact] = []
    any_anchor = False
    for group, names in _ANCHOR_GROUPS.items():
        anchor_pages: set = set()
        for name in names:
            anchor_pages.update(anchors.get(name) or [])
        if not anchor_pages:
            continue
        any_anchor = True
        allowed = {f for f, groups in FIELD_ANCHORS.items() if group in groups}
        scan = _pages_to_scan(pages, anchor_pages)
        facts, _end_scope, _end_unit = extract_rule_pages(
            [(p, pages.get(p)) for p in scan]
        )
        rule_facts.extend(f for f in facts if f.field in allowed)

    if not any_anchor:
        facts, _s, _u = extract_rule_pages(
            [(p, t) for p, t in sorted(pages.page_map().items())]
        )
        rule_facts.extend(facts)
    return rule_facts


def sync_filing(
    code: str,
    *,
    report_type: str = REPORT_ANNUAL,
    force: bool = False,
    store: FactStore,
    source: Optional[CninfoSource] = None,
    reconciler: Optional[Reconciler] = None,
    llm_extractor: Optional[Any] = None,
    dest_dir: Optional[str] = None,
    year: Optional[int] = None,
) -> SyncResult:
    """抓取 → 解析 → 抽取 → 合并 → 对账 → 落表。**同步**，请用 ``sync_filing_async``。"""
    source = source or CninfoSource()
    dest_dir = dest_dir or default_dest_dir()
    bare_code = code            # 巨潮只认 6 位
    code = normalize_thscode(code)

    # 先定位再决定要不要下载：只有真的没有缓存时才付出下载+解析的代价。
    filing: LocalFiling = source.fetch_filing_pdf(
        bare_code, dest_dir, report_type=report_type, year=year
    )
    if filing.year is None:
        # 标题里没有年份就无法确定报告期，编一个会让 period 错到别的年份去。
        # 此时不落库，只把 PDF 路径如实返回，让人去核。
        return SyncResult(
            thscode=code,
            report_type=report_type,
            note=f"公告标题未含年份（{filing.announcement.title}），无法确定报告期，未落库",
        )

    period = period_of(filing.year, report_type)

    if not force and _cache_hit(store, code, period, report_type):
        return SyncResult(
            thscode=code,
            report_type=report_type,
            period=period,
            cached=True,
            status_summary=store.status_summary(code),
            note="已有可信记录，未重新抽取（年报一年只变一次；需要强制重跑请用 force）",
        )

    pages: ExtractResult = extract_pages(filing.path)
    anchors = find_anchors(pages, keywords=HALO_ANCHORS)

    # 规则通道：按锚点分组扫描（表格跨页补偿见 TABLE_SPILL_PAGES，字段-锚点
    # 绑定见 FIELD_ANCHORS）。锚点定位让「143 页里只解析那几页」成为可能，
    # 也让 source_page 落在真正有数据的那一页。
    rule_facts: List[Fact] = _extract_by_anchors(pages, anchors)

    llm_facts: List[Fact] = []
    if llm_extractor is not None:
        for fact in rule_facts:
            text = pages.get(fact.source_page)
            if not text:
                continue
            try:
                llm_facts.extend(llm_extractor.extract(fact.source_page, text) or [])
            except Exception as exc:  # noqa: BLE001
                # LLM 通道失败不能让整条链路挂掉：规则通道的结果照样有用，
                # 只是少了交叉验证。降级并如实记日志。
                logger.warning("LLM 通道在第 %s 页失败：%s", fact.source_page, exc)

    records = merge_channels(rule_facts, llm_facts)
    for r in records:
        r["thscode"] = code
        r["period"] = period
        r["report_type"] = report_type

    # 治理诚信与 ESG 事实走**独立通道**：它们不是资产负债表科目，规则抽取
    # 的字段表里没有，也不该往里塞。它们来自年报的证监会固定章节（是非题），
    # 确定性更强，单独抽完直接落表，供评分内核的「事实」层与风险锚点使用。
    governance_records = []
    for r in extract_facts(pages)["facts"] + extract_finance_facts(pages)["facts"]:
        r["thscode"] = code
        r["period"] = period
        r["report_type"] = report_type
        governance_records.append(r)
    # 主营构成（分部收入/成本/毛利率）走独立通道：它们的 field 名带
    # segment_ 前缀且 value_text 是业务名，落表后由 analyze 聚合成 narratives。
    finance_extra = extract_finance_facts(pages)["segments"]
    for r in finance_extra:
        r["thscode"] = code
        r["period"] = period
        r["report_type"] = report_type
        governance_records.append(r)
    records = records + governance_records

    records = await_run(
        _maybe_reconcile(records, code, period, report_type, reconciler)
    )

    written = store.upsert(records) if records else 0

    present = {r["field"] for r in records}
    return SyncResult(
        thscode=code,
        report_type=report_type,
        period=period,
        cached=False,
        pages_extracted=len(pages.page_map()),
        anchors=anchors,
        records=records,
        written=written,
        status_summary=store.status_summary(code),
        missing_fields=[f for f in REQUIRED_FIELDS if f not in present],
    )


async def _maybe_reconcile(
    records: List[Dict[str, Any]],
    thscode: str,
    period: str,
    report_type: str,
    reconciler: Optional[Reconciler],
) -> List[Dict[str, Any]]:
    """对账，把结论合并回记录。

    三条都不能做的事，一件都不能做错：

    * 对账**失败不能让链路挂掉**。它只是第二道校验，尺子坏了不代表抽出来的数
      就不该留下——记录保持抽取原状态（pending/disputed），评分内核照样不会用。
    * 对账**只改 status，不改 value**。这一条由 reconcile.apply_reconcile 守住
      （它是唯一会动记录的函数），本层不再重复实现，避免出现第二个改值的地方。
    * financials 数据源没起来时**如实跳过并记日志**，不能假装对过了。
    """
    if not records:
        return records
    if reconciler is not None:
        return await reconciler(thscode, period, report_type, records)

    from .reconcile import finalize_status, get_financials_source, reconcile_records

    src = get_financials_source()
    if src is None:
        logger.info("financials 数据源未就绪，跳过对账（记录保持抽取原状态）")
        return records
    try:
        results = await reconcile_records(
            src, records, thscode=thscode, period=period, report_type=report_type
        )
        return finalize_status(records, results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("对账失败，保留抽取原状态：%s", exc)
        return records


def await_run(coro):  # type: ignore[no-untyped-def]
    """在同步编排里跑协程。

    端点是 async 的，但对账实现是 async 的（要 await DuckDB）。这里用一个私有
    事件循环把协程跑完，保持 sync_filing 整体同步的简洁。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("sync_filing 不能在运行中的事件循环里调用，请改用 sync_filing_async")


async def sync_filing_async(*args: Any, **kwargs: Any) -> SyncResult:
    """``sync_filing`` 的异步包装。端点一律用这个，别在事件循环里直接调同步版。"""
    return await asyncio.to_thread(sync_filing, *args, **kwargs)


def query_facts(
    code: str,
    store: FactStore,
    *,
    period: Optional[str] = None,
    report_type: Optional[str] = None,
    fields: Optional[List[str]] = None,
    scope: Optional[str] = None,
    only_verified: bool = True,
) -> Dict[str, Any]:
    rows = store.query(
        code,
        period=period,
        report_type=report_type,
        fields=fields,
        scope=scope,
        only_verified=only_verified,
    )
    return {
        "thscode": code,
        "latest_period": store.latest_period(code, report_type),
        "status_summary": store.status_summary(code),
        "count": len(rows),
        "facts": rows,
        "required_fields": list(REQUIRED_FIELDS),
        "missing_fields": [f for f in REQUIRED_FIELDS if not any(r["field"] == f for r in rows)],
    }
