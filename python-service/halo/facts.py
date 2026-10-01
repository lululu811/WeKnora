"""治理诚信与 ESG 环境事实抽取。

为什么这些要做成事实字段而不是 AI 槽位
--------------------------------------
「近三年是否被证券监管机构处罚」「内控审计意见是否非标」是**硬事实**，年报
里是证监会格式准则规定的固定章节，写成「□适用 √不适用」这样的是非题。让
AI 去搜索、去判断，等于把一个正则能百分之百确定的事实变成一个概率问题。

实测两份真实年报（巨潮原文）：

    茅台 2025  (五) 近三年受证券监管机构处罚的情况说明 → □适用 √不适用
                 十、…受到处罚及整改情况            → √适用 □不适用  ← 高管被留置
                 内部控制审计报告意见类型：标准的无保留意见
    宝钢 2024  同样三处，格式完全一致

ESG 环境则**按行业分层**：宝钢年报里有吨级量化排放（颗粒物 4315.8 吨、
二氧化硫 6213.4 吨、氮氧化物 18771.4 吨），茅台年报里碳排放/污染物命中数为
0。所以环境类只对真有披露的公司抽，抽不到就如实报「无环境披露」，
而不是给轻资产公司编一个可比但空洞的指标。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .extractor import EXTRACT_RULE
from .pdf_extract import ExtractResult
from .store import SCOPE_CONSOLIDATED, STATUS_PENDING

#: □/√ 二选一的勾选项。返回一个标签及其是否被勾上。
_CHECKBOX_RE = re.compile(r"([√□])\s*(适用|不适用|是|否)")


def _parse_checkbox(line: str) -> Optional[bool]:
    """解析「□适用 √不适用」这类是非题。

    Returns:
        True=该事项存在/适用，False=不适用，None=这一行不是勾选项。
    """
    m = _CHECKBOX_RE.search(line)
    if not m:
        return None
    mark, label = m.group(1), m.group(2)
    if label in ("适用", "是"):
        return mark == "√"
    return mark == "□"   # 「不适用/否」被勾上


# ---------------------------------------------------------------------------
# 各事实的定位与抽取规则
# ---------------------------------------------------------------------------

#: (字段, 章节标题关键词, 勾选项标签)
_CHECKBOX_FACTS = (
    (
        "internal_control_nonstandard",
        "是否被出具内部控制非标准审计意见",
        ("是", "否"),
    ),
    (
        "regulatory_penalty_3y",
        "近三年受证券监管机构处罚",
        ("适用", "不适用"),
    ),
    (
        "executive_penalty",
        "涉嫌违法违规、受到处罚及整改",
        ("适用", "不适用"),
    ),
)

#: 内控审计意见类型：「内部控制审计报告意见类型：标准的无保留意见」
_IC_OPINION_RE = re.compile(
    r"内部控制审计报告意见类型[：:]\s*([^\s，。；;]{2,20})"
)

#: 审计意见类型。判定限定在「一、审计意见」标题之后 —— 因为「保留意见」
#: 这几个字在「二、形成审计意见的基础」这类标题里也会出现，全文计数会把
#: 标题当成结论。
_AUDIT_SECTION_RE = re.compile(r"一、审计意见(.{0,2500}?)(?:\n\s*二、|\Z)", re.S)
_AUDIT_OPINION_RULES = (
    ("无法表示意见", "无法表示意见"),
    ("否定意见", "否定意见"),
    ("保留意见", "保留意见"),
    ("带强调事项", "带强调事项段"),
)

#: ESG 排放量（吨），单位统一为 ton。
#:
#: 必须区分**污染许可总量**与**实际排放量** —— 年报里两者并列出现、数值差
#: 好几倍。实测宝钢 2024 年报 p58：
#:
#:     报告期内公司废气中主要污染许可总量：颗粒物为 21595.9 吨、…氮氧化物为 55974.8 吨
#:     公司 2024 年实际排放量颗粒物 4315.8 吨、二氧化硫 6213.4 吨，氮氧化物 18771.4 吨
#:
#: 许可总量是「允许排多少」，实际排放量是「排了多少」。拿前者当环境表现，
#: 会把排放严重的企业说成排放轻微 —— 方向正好相反。
#:
#: 实现上以「实际排放」为**锚点**，在锚点之后的一段文本里取各污染物数值。
#: 不用「实际排放[^]{0,N}污染物」这种正则连接，因为各污染物的间距随公司
#: 表述而变（实测宝钢从「实际排放」到「氮氧化物」隔了 25 个字符，上限设成
#: 20 就会漏，设成 60 又可能串到下一句）。
_EMISSION_ANCHOR_RE = re.compile(r"实际排放")
_EMISSION_WINDOW = 220  # 锚点后取多长一段
_EMISSION_ITEMS = (
    ("emission_nox", "氮氧化物"),
    ("emission_so2", "二氧化硫"),
    ("emission_particulate", "颗粒物"),
)

_NUM_RE = r"([\d,]+\.?\d*)"


def _to_float(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _rec(
    field: str,
    *,
    value: Optional[float] = None,
    value_text: Optional[str] = None,
    unit: Optional[str] = None,
    page: int,
    raw: str,
) -> Dict[str, Any]:
    """构造一条事实记录（主键三列由调用方补）。"""
    return {
        "field": field,
        "value": value,
        "value_text": value_text,
        "unit": unit,
        "scope": SCOPE_CONSOLIDATED,
        "source_page": page,
        "raw_text": raw[:500],
        "extract_by": EXTRACT_RULE,
        "status": STATUS_PENDING,
        "confidence": 1.0,
    }


def extract_governance_facts(pages: ExtractResult) -> List[Dict[str, Any]]:
    """抽取治理诚信类事实。

    全部来自年报正文的固定章节，**无对账口径**（本地库里没有这些字段），
    因此 status 一律 pending，可信度由 :mod:`halo.reconcile` 的管线验证机制
    决定 —— 和财务事实走同一套逻辑。
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def add(rec: Dict[str, Any]) -> None:
        if rec["field"] in seen:
            return
        seen.add(rec["field"])
        out.append(rec)

    for page in pages.pages:
        text = page.text
        if not text:
            continue
        lines = text.split("\n")

        # --- 是非题类 ---
        for i, line in enumerate(lines):
            for field, keyword, _labels in _CHECKBOX_FACTS:
                if field in seen or keyword not in line:
                    continue
                # 答案不一定在紧邻的下一行。实测茅台 2025 年报：
                #   [8] 十、…涉嫌违法违规、受到处罚及整改
                #   [9] 情况                      ← 标题被折成两行
                #   [10] √适用 □不适用            ← 答案在这里，隔了两行
                # 只看 line+1 会漏掉「高管被留置」这个最该被抽到的风险信号。
                answer = None
                raw = line
                for j in range(i, min(i + 4, len(lines))):
                    answer = _parse_checkbox(lines[j])
                    if answer is not None:
                        raw = " ".join(lines[i:j + 1])
                        break
                if answer is None:
                    continue
                add(_rec(
                    field, value=1.0 if answer else 0.0, unit="bool",
                    page=page.page, raw=raw,
                ))

        # --- 内控审计意见类型 ---
        if "internal_control_opinion" not in seen:
            m = _IC_OPINION_RE.search(text)
            if m:
                add(_rec(
                    "internal_control_opinion", value_text=m.group(1),
                    page=page.page, raw=m.group(0),
                ))

        # --- 审计意见类型（限定在「一、审计意见」章节内）---
        if "audit_opinion" not in seen:
            m = _AUDIT_SECTION_RE.search(text)
            if m:
                section = m.group(1)
                opinion = None
                for token, label in _AUDIT_OPINION_RULES:
                    if token in section:
                        opinion = label
                        break
                if opinion is None and (
                    "我们认为" in section and "公允反映" in section
                ):
                    opinion = "标准无保留意见"
                if opinion:
                    add(_rec(
                        "audit_opinion", value_text=opinion,
                        page=page.page, raw=section[:200].replace("\n", " "),
                    ))

    return out


def extract_emission_facts(pages: ExtractResult) -> List[Dict[str, Any]]:
    """抽取 ESG 环境排放事实。**抽不到就一条都不返回**。

    调用方据此判定「该股无环境披露」——这本身是个有效结论，不该用别的
    指标去填（轻资产公司套一个「排放强度」看着可比，实则毫无意义）。

    优先取「实际排放」锚点之后的一段。没有锚点时不退回全文匹配：全文第一
    处命中极可能是许可总量，把许可额度当成实际排放是**方向性错误**，宁可
    报缺失。
    """
    out: List[Dict[str, Any]] = []
    for page in pages.pages:
        if not page.text:
            continue
        anchor = _EMISSION_ANCHOR_RE.search(page.text)
        if not anchor:
            continue
        seg = page.text[anchor.start(): anchor.start() + _EMISSION_WINDOW]
        for field, token in _EMISSION_ITEMS:
            m = re.search(token + r"[为是]?\s*([\d,]+\.?\d*)\s*吨", seg)
            if not m:
                continue
            val = _to_float(m.group(1))
            if val is not None:
                out.append(_rec(
                    field, value=val, unit="ton", page=page.page, raw=m.group(0),
                ))
    return out


def extract_facts(pages: ExtractResult) -> Dict[str, Any]:
    """一次性抽出全部非财务事实，并给出可披露的环境披露状态。"""
    governance = extract_governance_facts(pages)
    emissions = extract_emission_facts(pages)
    facts = governance + emissions
    return {
        "facts": facts,
        "governance": {f["field"]: f for f in governance},
        "emissions": {f["field"]: f for f in emissions},
        "has_environment_disclosure": bool(emissions),
    }
