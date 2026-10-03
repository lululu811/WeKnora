"""舆情：互动易问答（巨潮）+ 市场热度（同花顺热榜 / 东财人气榜）。

服务模板 10.7 板块舆情风险。两条设计要点：

* **「不在榜」必须写出来**：一只票没进热榜本身就是「没有炒作关注度」的信号，
  与「没去查」完全不同 —— 留空白读者无法分辨。
* **`covered` 区分「平台不覆盖」与「近期没有问答」**：互动易实测只覆盖深市
  （沪市第二步固定返回 0 条）。把前者说成后者，报告会把「查不到」读成
  「投资者没有关切」，而这两件事的结论正好相反。
"""

import pytest

from halo import analyze as az
from halo import extdata as ed
from halo.external import ExternalError


def slot(dimension):
    return {"dimension": dimension, "label": dimension, "anchors": {},
            "missing_anchors": [], "has_anchor": True, "score": None}


ALL_SLOTS = [slot(d) for d in
             ("moat", "stag", "esg", "management", "shareholder", "valuation", "risk")]


def report(ext, thscode="600519.SH"):
    return az.render_markdown({
        "thscode": thscode, "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {}, "external": ext,
    })


def sent_section(md):
    return md[md.index("### 市场舆情"):md.index("\n## 十一、")]


# ---------------------------------------------------------------------------
# 互动易
# ---------------------------------------------------------------------------


def _irm_fetch(calls):
    def fake(subdomain, path="", params=None, **kw):
        calls.append({"path": path, "params": params, "method": kw.get("method"),
                      "params_in": kw.get("params_in")})
        if "queryKeyboardInfo" in path:
            return {"data": [{"secid": "gshk0001211"}]}
        return {"rows": [{"mainContent": "<em>提问</em>正文", "attachedContent": "回复",
                          "attachedAuthor": "公司", "pubDate": 1759017600000}]}
    return fake


def test_irm_two_step_and_params_in_query(monkeypatch):
    calls = []
    monkeypatch.setattr(ed, "fetch_json", _irm_fetch(calls))
    out = ed.cninfo_irm("002594.SZ")

    assert len(calls) == 2
    assert calls[0]["method"] == "POST"
    assert calls[1]["params_in"] == "query", "第二步参数必须在 query string 上，否则 400"
    assert calls[1]["params"]["orgId"] == "gshk0001211", "orgId 取自第一步的 secid"
    assert calls[1]["params"]["stockcode"] == "002594"
    assert out["covered"] is True
    assert out["items"][0]["question"] == "提问正文", "高亮标签要剥掉"


def test_irm_shanghai_reports_not_covered(monkeypatch):
    """沪市：第一步命中公司、第二步 0 条 —— 是平台不提供，不是没有问答。"""
    def fake(subdomain, path="", params=None, **kw):
        return {"data": [{"secid": "x"}]} if "queryKeyboardInfo" in path else {"rows": []}

    monkeypatch.setattr(ed, "fetch_json", fake)
    out = ed.cninfo_irm("600519.SH")
    assert out["items"] == []
    assert out["covered"] is False
    assert "只覆盖深市" in out["reason"]
    assert "上证 e 互动" in out["reason"], "要说清该用哪个源"


def test_irm_deep_market_empty_is_covered_but_empty(monkeypatch):
    """深市 0 条才是真的「近期没有问答」。"""
    def fake(subdomain, path="", params=None, **kw):
        return {"data": [{"secid": "x"}]} if "queryKeyboardInfo" in path else {"rows": []}

    monkeypatch.setattr(ed, "fetch_json", fake)
    out = ed.cninfo_irm("002594")
    assert out["covered"] is True
    assert out["items"] == []


def test_irm_no_company_entry(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", lambda *a, **k: {"data": []})
    out = ed.cninfo_irm("002594")
    assert out["covered"] is False
    assert "没有这家公司的条目" in out["reason"]


def test_irm_degrades_on_failure(monkeypatch):
    def boom(*a, **k):
        raise ExternalError("互动易挂了")

    monkeypatch.setattr(ed, "fetch_json", boom)
    out = ed.cninfo_irm("002594")
    assert out["covered"] is False
    assert "取数失败" in out["reason"]


# ---------------------------------------------------------------------------
# 市场热度
# ---------------------------------------------------------------------------


def _heat_fetch(ths_list=None, em_list=None, ths_fail=False, em_fail=False):
    def fake(subdomain, path="", params=None, **kw):
        if subdomain == ed.Subdomain.THS_HOT:
            if ths_fail:
                raise ExternalError("热榜挂了")
            return {"data": {"stock_list": ths_list or []}}
        if em_fail:
            raise ExternalError("人气榜挂了")
        return {"data": em_list or []}
    return fake


def test_market_heat_finds_stock_in_both(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", _heat_fetch(
        ths_list=[{"order": 42, "code": "600519", "rate": 12000.0, "rise_and_fall": 1.2,
                   "hot_rank_chg": 8, "tag": {"concept_tag": ["白酒"], "popularity_tag": "人气飙升"}}],
        em_list=[{"sc": "SH600519", "rk": 15}, {"sc": "SZ000001", "rk": 16}]))
    out = ed.market_heat("600519.SH")
    assert out["in_ths"]["rank"] == 42
    assert out["in_ths"]["concepts"] == ["白酒"]
    assert out["in_em"]["rank"] == 15, "人气榜只给带前缀代码，比对要剥前缀"
    assert out["degraded"] is False


def test_market_heat_not_in_list(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", _heat_fetch(
        ths_list=[{"order": 1, "code": "601127", "tag": {}}],
        em_list=[{"sc": "SH601127", "rk": 1}]))
    out = ed.market_heat("600519.SH")
    assert out["in_ths"] is None and out["in_em"] is None
    assert out["ths"]["total"] == 1


def test_market_heat_partial_failure(monkeypatch):
    """一个榜挂了，另一个照常 —— 各榜独立。"""
    monkeypatch.setattr(ed, "fetch_json", _heat_fetch(
        ths_fail=True, em_list=[{"sc": "SH600519", "rk": 3}]))
    out = ed.market_heat("600519.SH")
    assert out["in_em"]["rank"] == 3
    assert out["ths"] is None
    assert "同花顺热榜失败" in out["reason"]
    assert out["degraded"] is False, "还有一个榜可用就不算全降级"


def test_market_heat_fully_degraded(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", _heat_fetch(ths_fail=True, em_fail=True))
    out = ed.market_heat("600519.SH")
    assert out["degraded"] is True


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_renders_in_list_with_concepts():
    ext = {"heat": {"market_heat": {
        "in_ths": {"rank": 42, "heat": 12000, "rank_chg": 8,
                   "concepts": ["白酒", "消费升级"], "tag": "人气飙升"},
        "in_em": {"rank": 15}, "ths": {"total": 100}, "em": {"total": 50},
        "degraded": False, "reason": ""}}}
    body = sent_section(report(ext))
    assert "第 **42** 名" in body
    assert "白酒、消费升级" in body
    assert "较上一期 +8 名" in body
    assert "第 **15** 名" in body


def test_not_in_list_is_stated_not_blank():
    """不在榜本身是信号 —— 留空白读者分不清「没上榜」和「没查」。"""
    ext = {"heat": {"market_heat": {"in_ths": None, "in_em": None,
                                    "ths": {"total": 100}, "em": {"total": 50},
                                    "degraded": False, "reason": ""}}}
    body = sent_section(report(ext))
    assert "不在" in body and "热榜" in body
    assert "中性偏正" in body


def test_renders_qa_with_unanswered_marked():
    ext = {"irm": {"qa": {"covered": True, "reason": "", "items": [
        {"time": "2026-09-28", "question": "股价下挫", "answer": "", "answerer": ""},
        {"time": "2026-09-20", "question": "产能规划", "answer": "见公告", "answerer": "公司"},
    ]}}}
    body = sent_section(report(ext))
    assert "**未回复**" in body
    assert "| 见公告 |" in body
    assert "未回复的提问本身是信息" in body


def test_renders_coverage_limitation():
    ext = {"irm": {"qa": {"covered": False, "items": [],
                          "reason": "互动易只覆盖深市（实测沪市问答固定返回 0 条）"}}}
    body = sent_section(report(ext))
    assert "投资者问答不可用" in body
    assert "只覆盖深市" in body


def test_section_absent_when_nothing_fetched():
    assert "市场舆情" not in report({})


def test_unanwered_question_pipe_escaped():
    ext = {"irm": {"qa": {"covered": True, "reason": "", "items": [
        {"time": "t", "question": "A|B", "answer": "", "answerer": ""},
    ]}}}
    row = [l for l in sent_section(report(ext)).splitlines() if "A" in l and "B" in l][0]
    assert row.count("|") == 4, "三列表格应恰好 4 个竖线"


def test_unimplemented_list_no_longer_claims_sentiment_is_missing():
    md = report({})
    body = md[md.index("### 11.3 尚未接入的章节"):md.index("## 附录：")]
    assert "舆情" not in body, "舆情已接入，不该还列在清单里"
