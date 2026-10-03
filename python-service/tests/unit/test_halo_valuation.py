"""机构一致预期 EPS（同花顺 F10）与第十一章的估值分析。

一致预期是**输入**，不是估值结论。所以渲染上只给机构预期与区间，并把**机构数一起
列出来** —— 1-2 家机构的「一致预期」不是一致预期，均值单独看会被当成权威数字。

三个实测过的坑，都写进了测试：
* URL 只认纯 6 位代码（带前缀安静地 404 到空表，不报错）；
* 页面是 GBK（按 UTF-8 解会把表头整片吞掉而数字还在）；
* 不引入 lxml —— `pd.read_html` 会破坏「零依赖服务」这条性质。
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

EPS_TABLE = """<table class="m_table">
<tr><th>年度</th><th>预测机构数</th><th>最小值</th><th>均值</th><th>最大值</th><th>行业平均数</th></tr>
<tr><td>2026</td><td>48</td><td>64.70</td><td>67.53</td><td>77.05</td><td>7.71</td></tr>
<tr><td>2027</td><td>47</td><td>67.23</td><td>71.44</td><td>81.01</td><td>8.28</td></tr>
</table>"""


def report(ext):
    return az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {}, "external": ext,
    })


def valuation_section(md):
    return md[md.index("### 11.2 估值分析"):md.index("### 11.3 尚未接入")]


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def test_consensus_eps_parses_table(monkeypatch):
    captured = {}

    def fake(subdomain, path="", params=None, **kw):
        captured["subdomain"] = subdomain
        captured["path"] = path
        captured["encoding"] = kw.get("encoding")
        return EPS_TABLE

    monkeypatch.setattr(ed, "fetch_text", fake)
    out = ed.consensus_eps("600519.SH")

    assert captured["subdomain"] == ed.Subdomain.THS_BASIC
    assert captured["path"] == "600519/worth.html", "URL 只认纯 6 位代码"
    assert captured["encoding"] == "gbk", "页面是 GBK，按 UTF-8 解会吞掉表头"
    assert out["degraded"] is False
    assert len(out["items"]) == 2, "表头行必须被跳过"
    assert out["items"][0] == {"year": "2026", "analysts": 48, "low": 64.70,
                               "mean": 67.53, "high": 77.05, "industry_avg": 7.71}


def test_consensus_eps_strips_prefix_in_url(monkeypatch):
    captured = {}

    def fake(subdomain, path="", params=None, **kw):
        captured["path"] = path
        return EPS_TABLE

    monkeypatch.setattr(ed, "fetch_text", fake)
    ed.consensus_eps("SH600519")
    assert captured["path"] == "600519/worth.html"


def test_consensus_eps_respects_max_years(monkeypatch):
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: EPS_TABLE)
    assert len(ed.consensus_eps("600519", max_years=1)["items"]) == 1


def test_consensus_eps_degrades_without_table(monkeypatch):
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: "<html>改版了</html>")
    out = ed.consensus_eps("600519")
    assert out["degraded"] is True
    assert "没有找到一致预期表" in out["reason"]


def test_consensus_eps_degrades_when_no_year_rows(monkeypatch):
    """表找到了但一行数据都没有 —— 也要报出来，而不是返回空 items 当成功。"""
    empty = "<table><tr><th>年度</th><th>预测机构数</th></tr></table>"
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: empty)
    out = ed.consensus_eps("600519")
    assert out["degraded"] is True
    assert "没有解析出任何年份行" in out["reason"]


def test_consensus_eps_degrades_on_fetch_failure(monkeypatch):
    def boom(*a, **k):
        raise ExternalError("同花顺挂了")

    monkeypatch.setattr(ed, "fetch_text", boom)
    out = ed.consensus_eps("600519")
    assert out["items"] == [] and out["degraded"] is True


def test_consensus_eps_skips_rows_without_year(monkeypatch):
    """非四位年份开头的行（说明行、合计行）不能当数据行。"""
    html = EPS_TABLE.replace("<tr><td>2027</td>", "<tr><td>合计</td>")
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: html)
    items = ed.consensus_eps("600519")["items"]
    assert [i["year"] for i in items] == ["2026"]


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_renders_eps_table_with_analyst_counts():
    md = report({"ths-basic": {"consensus_eps": {"items": [
        {"year": "2026", "analysts": 48, "low": 64.70, "mean": 67.53, "high": 77.05},
    ], "degraded": False, "reason": ""}}})
    body = valuation_section(md)
    assert "| 2026 | 48 | 67.53 | 64.7 ~ 77.05 |" in body
    assert "预测机构数" in body, "机构数必须列出来，否则均值会被当权威数字"


def test_thin_coverage_is_flagged():
    """1-2 家机构的「一致预期」不是一致预期。"""
    md = report({"ths-basic": {"consensus_eps": {"items": [
        {"year": "2026", "analysts": 48, "low": 64.7, "mean": 67.53, "high": 77.05},
        {"year": "2028", "analysts": 2, "low": 69.14, "mean": 75.18, "high": 83.83},
    ], "degraded": False, "reason": ""}}})
    body = valuation_section(md)
    assert "2028 的预测机构数不足 3 家" in body
    assert "2026" not in body.split("⚠️")[1], "覆盖充分的年份不该被点名"


def test_valuation_absent_when_not_fetched():
    md = report({})
    assert "### 11.2 估值分析" not in md


def test_degraded_eps_is_reported():
    md = report({"ths-basic": {"consensus_eps": {"items": [], "degraded": True,
                                                "reason": "页面里没有找到一致预期表"}}})
    body = valuation_section(md)
    assert "未取到一致预期" in body
    assert "没有找到一致预期表" in body


def test_target_price_is_declared_not_invented():
    """缺的是折现率假设与估值模型 —— 明说，不猜一个目标价出来。"""
    md = report({"ths-basic": {"consensus_eps": {"items": [
        {"year": "2026", "analysts": 48, "low": 64.7, "mean": 67.53, "high": 77.05},
    ], "degraded": False, "reason": ""}}})
    body = valuation_section(md)
    assert "目标价 / DCF 尚未接入" in body
    assert "不替你猜一个目标价" in body


def test_unimplemented_list_marks_target_price_as_the_gap():
    md = report({})
    body = md[md.index("### 11.3 尚未接入的章节"):md.index("## 附录：")]
    assert "目标价 / DCF" in body
