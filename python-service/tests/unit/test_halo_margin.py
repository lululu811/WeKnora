"""两融取数与渲染。

两融在**稳定档**（datacenter-web），与易封的 push2 系（资金流）不是一回事 ——
所以它跟股东户数一起放在 datacenter 桶，资金流那档被封时它照样能取到。

渲染上有一条刻意的取舍：两融**不是**判分的必需锚点，取不到就不渲染那一节，
而不是写一行「无数据」。到处都写「无数据」会把真正重要的缺失（缺锚点、缺数据源）
淹没掉 —— 后者才是需要显式标注的。
"""

import pytest

from halo import analyze as az
from halo import extdata as ed


def slot(dimension, label, anchors=None, missing=None):
    return {
        "dimension": dimension, "label": label,
        "anchors": anchors or {}, "missing_anchors": missing or [],
        "has_anchor": not missing, "score": None,
    }


ALL_SLOTS = [slot(d, d) for d in
             ("moat", "stag", "esg", "management", "shareholder", "valuation", "risk")]


def report_with_margin(rows, **over):
    r = {
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None,
        "facts": [], "announcements": [], "ai_slots": ALL_SLOTS,
        "narratives": {},
        "external": {"datacenter": {"margin_trading": rows}},
    }
    r.update(over)
    return az.render_markdown(r)


def shareholder_section(md):
    return md[md.index("## 九、股东与资金面"):md.index("\n## 十、")]


# ---------------------------------------------------------------------------
# 取数：只返回原值
# ---------------------------------------------------------------------------


def test_margin_trading_parses_datacenter_rows(monkeypatch):
    captured = {}

    def fake_datacenter(report_name, filter_, **kw):
        captured["report"] = report_name
        captured["filter"] = filter_
        captured["sort"] = kw.get("sort")
        return [{
            "DATE": "2026-09-30 00:00:00", "RZYE": 1.95e10, "RZMRE": 3.0e9,
            "RZCHE": 2.0e9, "RQYE": 2.1e8, "RZRQYE": 1.97e10,
        }]

    monkeypatch.setattr(ed, "datacenter", fake_datacenter)
    rows = ed.margin_trading("600519.SH", limit=5)

    assert captured["report"] == "RPTA_WEB_RZRQ_GGMX"
    assert captured["filter"] == '(SCODE="600519")', "巨潮/东财只认 6 位代码"
    assert captured["sort"] == ("DATE", "-1"), "必须按日期倒序取最新"
    assert rows[0]["date"] == "2026-09-30", "日期只留 YYYY-MM-DD"
    assert rows[0]["rzye"] == 1.95e10


def test_margin_trading_keeps_raw_units(monkeypatch):
    """不做亿元换算：换算是展示层的事，取数层多一次换算就多一个口径。"""
    monkeypatch.setattr(ed, "datacenter",
                        lambda *a, **k: [{"DATE": "2026-09-30", "RZYE": 1.95e10}])
    assert ed.margin_trading("600519")[0]["rzye"] == 1.95e10


def test_margin_trading_tolerates_missing_fields(monkeypatch):
    """字段缺失留 None，不填 0 —— 0 会被读成「余额为零」。"""
    monkeypatch.setattr(ed, "datacenter",
                        lambda *a, **k: [{"DATE": "2026-09-30", "RZYE": None}])
    row = ed.margin_trading("600519")[0]
    assert row["rzye"] is None
    assert row["rqye"] is None


def test_margin_trading_degrades_on_empty(monkeypatch):
    monkeypatch.setattr(ed, "datacenter", lambda *a, **k: [])
    assert ed.margin_trading("600519") == []


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_margin_section_rendered_when_available():
    md = report_with_margin([
        {"date": "2026-09-24", "rzye": 1.80e10, "rqye": 2.0e8, "rzrqye": 1.82e10},
        {"date": "2026-09-30", "rzye": 1.95e10, "rqye": 2.1e8, "rzrqye": 1.97e10},
    ])
    body = shareholder_section(md)
    assert "### 融资融券（两融）" in body
    assert "| 融资余额 | 195.00 亿 |" in body
    assert "| 融券余额 | 2.10 亿 |" in body
    assert "+15.00 亿" in body, "窗口内变化必须给方向，单点余额看不出进退"


def test_margin_section_absent_when_not_fetched():
    """取不到就不渲染那一节 —— 它不是必需锚点，写「无数据」会淹没真正的缺失。"""
    md = report_with_margin([])
    assert "融资融券" not in shareholder_section(md)
    md2 = az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
    })
    assert "融资融券" not in md2


def test_margin_single_row_has_no_change_line():
    """只有一天数据时不给变化 —— 没有基准可比，编一个「+0」是假的。"""
    md = report_with_margin([{"date": "2026-09-30", "rzye": 1.95e10}])
    body = shareholder_section(md)
    assert "| 融资余额 | 195.00 亿 |" in body
    assert "融资余额变化" not in body


def test_margin_change_line_requires_both_ends():
    """端点缺 rzye 时同样不给变化，而不是拿 None 参与运算。"""
    md = report_with_margin([
        {"date": "2026-09-24", "rzye": None},
        {"date": "2026-09-30", "rzye": 1.95e10},
    ])
    assert "融资余额变化" not in shareholder_section(md)


def test_margin_rows_are_sorted_regardless_of_input_order():
    """接口顺序不保证，窗口的首尾必须按日期取。"""
    md = report_with_margin([
        {"date": "2026-09-30", "rzye": 1.95e10},
        {"date": "2026-09-24", "rzye": 1.80e10},
    ])
    body = shareholder_section(md)
    assert "数据日期：2026-09-30" in body
    assert "+15.00 亿" in body


def test_margin_rows_without_date_are_skipped():
    md = report_with_margin([{"date": "", "rzye": 1.0}, {"date": "2026-09-30", "rzye": 2.0}])
    body = shareholder_section(md)
    assert "数据日期：2026-09-30" in body
    assert "近 2 个交易日" not in body
