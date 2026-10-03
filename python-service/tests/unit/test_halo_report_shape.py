"""报告骨架的章节形状与「待判分 / 缺失」的表达。

这份报告是给人和 agent 看的，所以它有两种读者都要能读懂的约束：

* 人：章节顺序与 halo-skill 的 V5.0 模板一致，从模板抄下来的阅读习惯能直接复用；
* agent：分数该留槽位的地方留 `{{xxx_score}}`，该说缺失的地方说清缺什么。

三件事必须能区分开，否则报告会骗人：
1. **Python 算出来的确定值**（HALO 六维、成长性）—— 直接填；
2. **待判分**（6 个定性维度 + 估值）—— 留槽位，不填 0、不留空；
3. **拿不到**（缺数据源）—— 写清缺什么，并汇总进附录。
"""

import pytest

from halo import analyze as az


def slot(dimension, label, anchors=None, missing=None):
    anchors = anchors or {}
    missing = missing or []
    return {
        "dimension": dimension,
        "label": label,
        "anchors": anchors,
        "missing_anchors": missing,
        "has_anchor": not missing,
        "score": None,
    }


ALL_SLOTS = [
    slot("moat", "护城河", {"gross_margin": 0.91}),
    slot("stag", "滞胀防御", {"tangible_pct": 0.12}),
    slot("esg", "ESG", {"employees_total": 34000}),
    slot("management", "管理层", {"roe": 0.31}),
    slot("shareholder", "股东资金面", {}, ["main_fund_flow"]),
    slot("valuation", "估值", {"pe_ttm": 22.0}),
    slot("risk", "风险", {"hard_risk_facts": {"executive_penalty": 1}}),
]


def base_result(**over):
    r = {
        "thscode": "600519.SH",
        "period": "2025-12-31",
        "asset_type": "light",
        "asset_type_basis": "fixed_asset_ratio",
        "halo": {"ok": True, "score": 3.85, "rating": "中等",
                 "dimensions": {"有形资产密集度": {"raw": 12.0, "unit": "%", "score": 2, "weight": 0.2}}},
        "growth": {"score": 5.0, "rating": "中等", "complete": True, "missing": [],
                   "sub_scores": {"营收增长": {"score": 5, "applied": ["rev_yoy=8%"]}}},
        "facts": [],
        "announcements": [],
        "ai_slots": ALL_SLOTS,
        "narratives": {},
    }
    r.update(over)
    return r


# ---------------------------------------------------------------------------
# 第零章：核心评分卡片
# ---------------------------------------------------------------------------


def test_score_card_fills_python_computed_rows():
    md = az.render_markdown(base_result())
    assert "## 第零章 执行摘要" in md
    assert "**3.85/5.0**" in md, "HALO 分是确定值，必须直接填"
    assert "**5.00/10**" in md, "成长性同理"


def test_score_card_marks_pending_dimensions_not_zero():
    """待判分必须写「待判分」：填 0 会被读成「很差」，留空会被读成「没这一项」。"""
    md = az.render_markdown(base_result())
    assert "| 护城河 | 待判分 |" in md
    assert "| 0 |" not in md


def test_score_card_excludes_valuation():
    """模板的卡片是 8 行，估值不在其中（它归第十一章）。同一件事不该有两个位置。

    只查表格行，不查整段：卡片下面那句说明会提到「含估值」，那是解释综合分要
    收齐哪些维度，不是给估值开一行。
    """
    card = md_between(az.render_markdown(base_result()), "### 核心评分卡片", "## 一、公司概况")
    assert "| 估值" not in card


def test_score_card_flags_dimensions_without_anchors():
    md = az.render_markdown(base_result())
    assert "股东资金面 ⚠️ 无锚点" in md


def test_score_card_says_halo_uncomputable_when_missing():
    r = base_result(halo={"ok": False, "reason": "缺 fixed_assets、total_assets"})
    md = az.render_markdown(r)
    assert "| **HALO 六维** | ⚠️ 不可计算 |" in md
    assert "| **成长性** | **5.00/10** |" in md, "HALO 不可计算不该牵连成长性"


# ---------------------------------------------------------------------------
# 五~十：六个定性维度各一章
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dimension,heading",
    [
        ("moat", "## 五、低淘汰率"),
        ("stag", "## 六、滞胀防御"),
        ("esg", "## 七、ESG"),
        ("management", "## 八、管理层质量"),
        ("shareholder", "## 九、股东与资金面"),
        ("risk", "## 十、风险评估"),
    ],
)
def test_each_dimension_gets_its_own_chapter(dimension, heading):
    md = az.render_markdown(base_result())
    assert heading in md
    body = md_between(md, heading, "\n## ")
    assert f"{{{{{dimension}_score}}}}" in body, "该维度必须有分数槽位"
    assert f"{{{{{dimension}_analysis}}}}" in body, "该维度必须有分析槽位"


def test_dimension_chapter_shows_anchors():
    body = md_between(az.render_markdown(base_result()), "## 五、低淘汰率", "\n## ")
    assert "`gross_margin` = 0.91" in body, "锚点必须写在旁边，判分不该凭印象"


def test_dimension_missing_from_slots_still_renders_chapter():
    """槽位缺失也要出章节并说明 —— 直接跳过会让读者以为模板里没有这一章。"""
    r = base_result(ai_slots=[s for s in ALL_SLOTS if s["dimension"] != "esg"])
    md = az.render_markdown(r)
    assert "## 七、ESG" in md
    assert "未在本次分析中生成槽位" in md


def test_risk_chapter_carries_governance_facts():
    """硬风险事实是治理风险的依据，放在风险章里而不是散在别处。"""
    r = base_result(facts=[
        {"field": "executive_penalty", "value": 1, "unit": "次", "source_page": 88,
         "raw_text": "报告期内公司收到监管函", "value_text": None},
    ])
    body = md_between(az.render_markdown(r), "## 十、风险评估", "\n## 十一、")
    assert "治理诚信事实" in body
    assert "executive_penalty" in body
    assert "p88" in body, "来源页必须保留，否则无法回溯原文"


def test_governance_section_absent_without_facts():
    body = md_between(az.render_markdown(base_result()), "## 十、风险评估", "\n## 十一、")
    assert "治理诚信事实" not in body


# ---------------------------------------------------------------------------
# 附录：缺失项汇总
# ---------------------------------------------------------------------------


def test_appendix_lists_anchor_gaps():
    md = az.render_markdown(base_result())
    assert "## 附录：数据来源与缺失项汇总" in md
    assert "股东资金面 锚点 | 缺 main_fund_flow" in md


def test_appendix_lists_halo_and_growth_gaps():
    r = base_result(
        halo={"ok": False, "reason": "缺 revenue、capex、ocf"},
        growth=None,
    )
    md = az.render_markdown(r)
    assert "HALO 六维 | 缺 revenue、capex、ocf" in md
    assert "成长性 | 本地 financials 数据源不可用" in md


def test_appendix_reports_nothing_missing_when_complete():
    """全都有时要说「无」，而不是留一张空表 —— 空表看起来像忘了填。"""
    slots = [slot(s["dimension"], s["label"], {"x": 1}) for s in ALL_SLOTS]
    r = base_result(ai_slots=slots, announcements=[{"title": "t", "date": "d"}])
    md = az.render_markdown(r)
    assert "（无：所有必需输入都已取到。）" in md


def test_appendix_flags_unfetched_announcements():
    md = az.render_markdown(base_result())
    assert "近期公告 | 未取" in md


# ---------------------------------------------------------------------------
# 第十一章：不猜综合分
# ---------------------------------------------------------------------------


def test_comprehensive_lists_every_slot_including_valuation():
    """估值不在第零章卡片里，但必须在综合评分表里 —— 它参与复算。"""
    body = md_between(az.render_markdown(base_result()), "## 十一、", "## 附录：")
    assert "| 估值 | {{valuation_score}} | 待判 |" in body


def test_comprehensive_lists_unimplemented_template_chapters():
    """模板里本地没数据的章节要显式列出来，而不是静默缺席。"""
    body = md_between(az.render_markdown(base_result()), "## 十一、", "## 附录：")
    for need in ("利好/利空因素", "北向资金", "融资动态", "目标价"):
        assert need in body, f"第十一章应列出尚未接入的 {need}"
    assert "宁缺勿造" in body


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def md_between(md: str, start: str, end: str) -> str:
    """取 md 里 start 之后、end 之前的一段。找不到就抛，避免断言悄悄失去作用。"""
    i = md.index(start)
    j = md.index(end, i + len(start))
    return md[i:j]
