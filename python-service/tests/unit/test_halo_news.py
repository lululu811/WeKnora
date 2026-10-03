"""个股新闻（东财搜索）与第二章的利好/利空判定槽位。

分工与七维定性维度一致：**Python 锁数据，AI 做判断**。Python 只给新闻原文
（标题/摘要/日期/来源），「利好还是利空」「影响多大」「可信度几星」都是判断而不是
事实 —— 用标题正则猜必然出错，所以留 `{{positive_factors}}` / `{{negative_factors}}`
槽位给 AI。

四种状态必须分开，因为下一步动作完全不同：
① 有新闻；② 被风控（只回 passportWeb）；③ 真没搜到；④ 压根没取 external。
把 ② 说成 ③ 会让报告把风控读成「消息面平静」。
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


def report(news_payload=None, *, external_present=True, **over):
    r = {
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
    }
    if external_present:
        r["external"] = {"news": {"stock_news": news_payload}} if news_payload is not None else {}
    r.update(over)
    return az.render_markdown(r)


def news_section(md):
    return md[md.index("### 个股新闻"):md.index("\n## 三、")]


def article(title="标题", date="2026-09-30 17:24", source="证券时报网", url="https://x/1"):
    return {"title": title, "summary": "", "date": date, "source": source, "url": url}


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def test_stock_news_parses_articles(monkeypatch):
    captured = {}

    def fake_fetch(subdomain, path="", params=None, **kw):
        captured["subdomain"] = subdomain
        captured["params"] = params
        return {"result": {"cmsArticleWebOld": [{
            "title": "<em>贵州茅台</em>发布公告", "content": "<em>摘要</em>正文",
            "date": "2026-09-30 17:24:00", "mediaName": "证券时报网",
            "url": "https://x/1",
        }]}}

    monkeypatch.setattr(ed, "fetch_json", fake_fetch)
    out = ed.stock_news("600519.SH", limit=5)

    assert captured["subdomain"] == ed.Subdomain.EM_SEARCH
    assert captured["params"]["cb"] == "jQuery_news", "JSONP 回调名必须带上"
    assert '"pageSize":5' in captured["params"]["param"]
    assert out["degraded"] is False
    assert out["items"][0]["title"] == "贵州茅台发布公告", "高亮标签必须剥掉"
    assert out["items"][0]["summary"] == "摘要正文"
    assert out["items"][0]["date"] == "2026-09-30 17:24"


def test_stock_news_uses_bare_code(monkeypatch):
    captured = {}

    def fake_fetch(subdomain, path="", params=None, **kw):
        captured["param"] = params["param"]
        return {"result": {"cmsArticleWebOld": []}}

    monkeypatch.setattr(ed, "fetch_json", fake_fetch)
    ed.stock_news("600519.SH")
    assert '"keyword":"600519"' in captured["param"], "搜索只认 6 位代码"


def test_stock_news_flags_passport_web_degradation(monkeypatch):
    """只回 passportWeb 是**风控指纹**，不是「没有新闻」。"""
    monkeypatch.setattr(ed, "fetch_json",
                        lambda *a, **k: {"result": {"passportWeb": [{"x": 1}]}})
    out = ed.stock_news("600519")
    assert out["degraded"] is True
    assert "passportWeb" in out["reason"]
    assert "间歇风控" in out["reason"]


def test_stock_news_empty_without_degradation(monkeypatch):
    """接口确实回了文章列表、只是空的 —— 这才是「没有新闻」。"""
    monkeypatch.setattr(ed, "fetch_json", lambda *a, **k: {"result": {"cmsArticleWebOld": []}})
    out = ed.stock_news("600519")
    assert out["items"] == []
    assert out["degraded"] is False


def test_stock_news_degrades_on_failure(monkeypatch):
    def boom(*a, **k):
        raise ExternalError("搜索挂了")

    monkeypatch.setattr(ed, "fetch_json", boom)
    out = ed.stock_news("600519")
    assert out["items"] == [] and out["degraded"] is True


def test_strip_tags():
    assert ed._strip_tags("<em>茅台</em>发布") == "茅台发布"
    assert ed._strip_tags(None) == ""
    assert ed._strip_tags("无标签") == "无标签"


# ---------------------------------------------------------------------------
# 渲染：四种状态
# ---------------------------------------------------------------------------


def test_renders_news_table_and_judgement_slots():
    md = report({"items": [article()], "degraded": False, "reason": ""})
    body = news_section(md)
    assert "| 日期 | 来源 | 标题 |" in body
    assert "[标题](https://x/1)" in body
    assert "{{positive_factors}}" in body, "分类槽位必须留出来"
    assert "{{negative_factors}}" in body
    assert "分类由 AI 做" in body, "分工要写清楚，否则模型会以为该由它编数字"


def test_degraded_is_not_reported_as_no_news():
    md = report({"items": [], "degraded": True, "reason": "接口只返回 ['passportWeb']"})
    body = news_section(md)
    assert "本次未搜到新闻" in body
    assert "passportWeb" in body
    assert "没有搜到该标的的新闻" not in body, "风控与「没有新闻」不能混"


def test_truly_empty_says_no_news():
    md = report({"items": [], "degraded": False, "reason": ""})
    body = news_section(md)
    assert "没有搜到该标的的新闻" in body
    assert "本次未搜到新闻" not in body


def test_external_not_fetched_says_so():
    md = report(None, external_present=False)
    body = news_section(md)
    assert "未取" in body
    assert "include_external" in body


def test_external_fetched_but_news_absent():
    md = report(None, external_present=True)   # external 有，但没有 news 桶
    body = news_section(md)
    assert "未取" in body


def test_news_title_pipe_does_not_break_table():
    md = report({"items": [article(title="A|B公告")], "degraded": False, "reason": ""})
    row = [l for l in news_section(md).splitlines() if "A" in l and "公告" in l][0]
    assert row.count("|") == 4, "三列表格应恰好 4 个竖线"


def test_news_without_url_is_plain_text():
    md = report({"items": [article(url="")], "degraded": False, "reason": ""})
    assert "[](" not in news_section(md)


def test_unimplemented_list_no_longer_claims_news_is_missing():
    """「尚未接入」清单要跟着代码走：新闻源已接，清单只能剩「第二来源」。"""
    md = az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
    })
    body = md[md.index("### 11.2 尚未接入的章节"):md.index("## 附录：")]
    assert "财联社" in body, "缺的是第二新闻源"
    assert "| 二、利好/利空因素 | 新闻源" not in body, "不该再说整个第二章没接"
