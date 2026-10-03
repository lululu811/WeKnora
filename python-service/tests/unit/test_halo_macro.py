"""宏观：LPR（东财结构化报表）与 PMI（统计局页面抓取）。

两个源的可靠性完全不同，写法也不同：

* **LPR** 走 datacenter-web 的结构化报表，复用既有的稳定档限速 —— 稳。
* **PMI** 只能抓页面（统计局没有接口），所以只取**版式稳定的三个主指标**；
  企业规模分档统计局用过三种措辞，解析不到就留 None，不为了凑数去猜。

渲染上有两条硬要求：
* PMI 必须给**荣枯线对比** —— 「50.1」本身不说明任何事，50 以上还是以下是它的
  全部信息；
* LPR 必须给**变化方向** —— 单点利率看不出在宽松还是收紧。
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


def report(ext):
    return az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {}, "external": ext,
    })


def risk_section(md):
    return md[md.index("## 十、风险评估"):md.index("\n## 十一、")]


def pmi_html(month_title="2026年9月中国采购经理指数运行情况"):
    """复刻统计局正文的版式：全角括号 + 括号内带空格。"""
    return f"""<html><body>
    <a href="./202609/t20260930_1965449.html">{month_title}</a>
    <p>9月份，制造业采购经理指数（ PMI ）为 50.1% ，比上月上升 0.1 个百分点。</p>
    <p>非制造业商务活动指数为 50.2% ，综合PMI产出指数为 50.7% 。</p>
    <p>大型企业PMI为 50.6% ；中、小型企业PMI分别为 49.8% 和 48.9% 。</p>
    </body></html>"""


# ---------------------------------------------------------------------------
# LPR：结构化报表
# ---------------------------------------------------------------------------


def test_lpr_filters_legacy_benchmark_rows(monkeypatch):
    """同一报表里混着 2019-08 改革前的旧贷款基准利率行（LPR 字段为空）。"""
    monkeypatch.setattr(ed, "datacenter", lambda *a, **k: [
        {"TRADE_DATE": "2026-09-20 00:00:00", "LPR1Y": 3.0, "LPR5Y": 3.5},
        {"TRADE_DATE": "2015-10-24 00:00:00", "LPR1Y": None, "LPR5Y": None},
    ])
    items = ed.lpr_latest()["items"]
    assert len(items) == 1, "旧基准利率行必须剔除"
    assert items[0]["date"] == "2026-09-20"
    assert items[0]["lpr_1y"] == 3.0


def test_lpr_keeps_none_for_five_year_before_2019(monkeypatch):
    """5 年期 2019-08-20 才设立 —— 更早是「当时没这个品种」，不是「没取到」。"""
    monkeypatch.setattr(ed, "datacenter", lambda *a, **k: [
        {"TRADE_DATE": "2018-01-02", "LPR1Y": 4.3, "LPR5Y": None},
    ])
    item = ed.lpr_latest()["items"][0]
    assert item["lpr_1y"] == 4.3
    assert item["lpr_5y"] is None


def test_lpr_uses_datacenter_with_expected_report(monkeypatch):
    captured = {}

    def fake(report_name, filter_, **kw):
        captured["report"] = report_name
        captured["sort"] = kw.get("sort")
        captured["columns"] = kw.get("columns")
        return []

    monkeypatch.setattr(ed, "datacenter", fake)
    ed.lpr_latest(limit=6)
    assert captured["report"] == "RPTA_WEB_RATE"
    assert captured["sort"] == ("TRADE_DATE", "-1"), "取最新若干期"
    assert "LPR1Y" in captured["columns"]


# ---------------------------------------------------------------------------
# PMI：页面抓取
# ---------------------------------------------------------------------------


def test_pmi_parses_main_figures(monkeypatch):
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: pmi_html())
    out = ed.nbs_pmi()
    assert out["manufacturing"] == 50.1
    assert out["non_manufacturing"] == 50.2
    assert out["composite"] == 50.7
    assert out["month"] == "2026-09"
    assert out["degraded"] is False


def test_pmi_handles_fullwidth_paren_with_spaces(monkeypatch):
    """正文是 `（ PMI ）为 50.1%` —— 空白必须整个删掉才匹配得到。

    只压成单个空格会一条都匹配不到，而且不报错，表现为「PMI 永远取不到」。
    """
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: pmi_html())
    assert ed.nbs_pmi()["manufacturing"] == 50.1


def test_pmi_parses_split_size_layout(monkeypatch):
    """「中、小型企业PMI分别为 B%和C%」这种半拆版式。"""
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: pmi_html())
    out = ed.nbs_pmi()
    assert out["large"] == 50.6
    assert out["medium"] == 49.8
    assert out["small"] == 48.9


def test_pmi_parses_combined_size_layout(monkeypatch):
    """「大、中、小型企业PMI分别为 A%、B%和C%」这种全合并版式。"""
    html = """<a href="./x.html">2026年8月中国采购经理指数运行情况</a>
    <p>制造业采购经理指数（PMI）为49.4%。</p>
    <p>大、中、小型企业PMI分别为50.0%、49.5%和48.0%。</p>"""
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: html)
    out = ed.nbs_pmi()
    assert (out["large"], out["medium"], out["small"]) == (50.0, 49.5, 48.0)


def test_pmi_degrades_when_index_has_no_entry(monkeypatch):
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: "<html>没有相关条目</html>")
    out = ed.nbs_pmi()
    assert out["degraded"] is True
    assert "未找到" in out["reason"]


def test_pmi_degrades_on_fetch_failure(monkeypatch):
    def boom(*a, **k):
        raise ExternalError("统计局挂了")

    monkeypatch.setattr(ed, "fetch_text", boom)
    out = ed.nbs_pmi()
    assert out["degraded"] is True
    assert "取数失败" in out["reason"]


def test_pmi_degrades_when_article_unparsable(monkeypatch):
    """条目找得到但正文没有 PMI —— 那是页面改版，必须报出来而不是静默 None。"""
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return ('<a href="./x.html">2026年9月中国采购经理指数运行情况</a>'
                if calls["n"] == 1 else "<p>正文改版了，没有数字</p>")

    monkeypatch.setattr(ed, "fetch_text", fake)
    out = ed.nbs_pmi()
    assert out["degraded"] is True
    assert out["manufacturing"] is None
    assert "没有解析出" in out["reason"]


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_renders_pmi_with_threshold_comparison():
    md = report({"macro": {"pmi": {"month": "2026-09", "manufacturing": 50.1,
                                   "non_manufacturing": 49.2, "composite": 50.0,
                                   "degraded": False, "reason": ""}}})
    body = risk_section(md)
    assert "| 制造业 | 50.1 | 扩张（+0.1）|" in body
    assert "| 非制造业商务活动 | 49.2 | 收缩（-0.8）|" in body, "49.2 是收缩，不能只写数字"
    assert "50 为荣枯线" in body


def test_renders_lpr_with_trend():
    md = report({"datacenter": {"lpr": {"items": [
        {"date": "2026-08-20", "lpr_1y": 3.1, "lpr_5y": 3.6},
        {"date": "2026-09-20", "lpr_1y": 3.0, "lpr_5y": 3.5},
    ]}}})
    body = risk_section(md)
    assert "1 年期 3.00%" in body
    assert "5 年期 3.50%" in body
    assert "下行（宽松）" in body, "单点利率看不出在宽松还是收紧"


def test_lpr_flat_is_reported_as_flat():
    md = report({"datacenter": {"lpr": {"items": [
        {"date": "2026-08-20", "lpr_1y": 3.0, "lpr_5y": 3.5},
        {"date": "2026-09-20", "lpr_1y": 3.0, "lpr_5y": 3.5},
    ]}}})
    assert "持平" in risk_section(md)


def test_macro_section_absent_when_no_data():
    assert "宏观环境" not in risk_section(report({}))
    assert "宏观环境" not in risk_section(report({"macro": {}, "datacenter": {}}))


def test_macro_section_partial_data_still_renders():
    """只有 LPR 没有 PMI 时照样渲染 —— 两件事各自独立可取。"""
    md = report({"datacenter": {"lpr": {"items": [{"date": "2026-09-20", "lpr_1y": 3.0}]}}})
    body = risk_section(md)
    assert "宏观环境" in body
    assert "采购经理指数" not in body
    assert "1 年期 3.00%" in body


def test_degraded_pmi_is_reported_not_silent():
    md = report({"macro": {"pmi": {"degraded": True, "reason": "正文里没有解析出制造业 PMI"}}})
    body = risk_section(md)
    assert "宏观环境" in body
    assert "没有解析出制造业 PMI" in body
