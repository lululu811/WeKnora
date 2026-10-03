"""北向资金：只把**序列完整**的那一档当读数。

实测背景（2026-10，同花顺 data.hexin.cn）：

    time  262 个点（完整）
    hgt   262 个点，末值 -9.28 亿     ← 可用
    sgt    35 个点，末值 379.75 亿    ← 残缺，且末值量级异常

那个 379.75 亿对单日深股通净买入是异常值。把它当读数渲染出去**比不渲染更糟**：
读者无从判断它是真的还是残缺序列的残端。所以这里的规矩是
「取到了却不可信」必须说出来 —— 它与「完全取不到」是两件事。

判断用**覆盖率**而不是硬编码「sgt 不可信」：上游恢复时判断会自动跟上，
也不会把某个档位永久钉死。
"""

import pytest

from halo import analyze as az
from halo import extdata as ed


def slot(dimension):
    return {"dimension": dimension, "label": dimension, "anchors": {},
            "missing_anchors": [], "has_anchor": True, "score": None}


ALL_SLOTS = [slot(d) for d in
             ("moat", "stag", "esg", "management", "shareholder", "valuation", "risk")]


def report_with_northbound(nb, **over):
    r = {
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
        "external": {"ths": {"northbound": nb}},
    }
    r.update(over)
    return az.render_markdown(r)


def shareholder_section(md):
    return md[md.index("## 九、股东与资金面"):md.index("\n## 十、")]


def summary(hgt_points, sgt_points, hgt_last=-9.28, sgt_last=379.75):
    return {
        "as_of": "15:00", "points": 262,
        "lanes": {
            "hgt": {"label": "沪股通", "latest": hgt_last,
                    "coverage": round(hgt_points / 262, 3), "points": hgt_points,
                    "usable": hgt_points / 262 >= ed._NORTHBOUND_MIN_COVERAGE,
                    "reason": ""},
            "sgt": {"label": "深股通", "latest": sgt_last,
                    "coverage": round(sgt_points / 262, 3), "points": sgt_points,
                    "usable": sgt_points / 262 >= ed._NORTHBOUND_MIN_COVERAGE,
                    "reason": "" if sgt_points / 262 >= ed._NORTHBOUND_MIN_COVERAGE
                              else f"仅回传 {sgt_points}/262 个点，序列不完整，末值不可采信"},
        },
    }


# ---------------------------------------------------------------------------
# 取数与完整性判断
# ---------------------------------------------------------------------------


def test_hsgt_realtime_degrades_on_failure(monkeypatch):
    from halo.external import ExternalError

    def boom(*a, **k):
        raise ExternalError("同花顺挂了")

    monkeypatch.setattr(ed, "fetch_json", boom)
    out = ed.hsgt_realtime()
    assert out == {"time": [], "hgt": [], "sgt": []}


def test_hsgt_realtime_returns_raw_series(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", lambda *a, **k: {
        "time": ["09:30", "15:00"], "hgt": [1.0, -9.28], "sgt": [None, 379.75],
    })
    out = ed.hsgt_realtime()
    assert out["time"] == ["09:30", "15:00"]
    assert out["hgt"] == [1.0, -9.28]
    assert out["sgt"] == [None, 379.75], "原样返回，不在这里做完整性判断"


def test_hsgt_realtime_tolerates_non_dict(monkeypatch):
    monkeypatch.setattr(ed, "fetch_json", lambda *a, **k: ["不是 dict"])
    assert ed.hsgt_realtime()["time"] == []


def test_northbound_summary_flags_incomplete_lane(monkeypatch):
    monkeypatch.setattr(ed, "hsgt_realtime", lambda: {
        "time": ["t"] * 262, "hgt": [-9.28] * 262, "sgt": [379.75] * 35,
    })
    lanes = ed.northbound_summary()["lanes"]
    assert lanes["hgt"]["usable"] is True
    assert lanes["hgt"]["latest"] == -9.28
    assert lanes["sgt"]["usable"] is False, "35/262 必须判为不可用"
    assert "35/262" in lanes["sgt"]["reason"]
    assert lanes["sgt"]["latest"] == 379.75, "原值保留，由渲染层决定说不说"


def test_northbound_summary_all_unusable_when_no_data(monkeypatch):
    monkeypatch.setattr(ed, "hsgt_realtime",
                        lambda: {"time": [], "hgt": [], "sgt": []})
    out = ed.northbound_summary()
    assert out["points"] == 0
    assert all(not l["usable"] for l in out["lanes"].values())
    assert out["lanes"]["hgt"]["reason"] == "未取到数据"


def test_northbound_coverage_is_the_judge_not_the_lane_name(monkeypatch):
    """判据是覆盖率，不是「sgt 这个名字」—— 上游恢复后它应当自动变成可用。"""
    monkeypatch.setattr(ed, "hsgt_realtime", lambda: {
        "time": ["t"] * 262, "hgt": [-9.28] * 262, "sgt": [12.5] * 262,
    })
    lanes = ed.northbound_summary()["lanes"]
    assert lanes["sgt"]["usable"] is True
    assert lanes["sgt"]["latest"] == 12.5


def test_northbound_ignores_non_numeric_points(monkeypatch):
    """全是 None 的序列即便长度够也不算可用 —— 没有读数。"""
    monkeypatch.setattr(ed, "hsgt_realtime", lambda: {
        "time": ["t"] * 262, "hgt": [None] * 262, "sgt": [1.0] * 262,
    })
    assert ed.northbound_summary()["lanes"]["hgt"]["usable"] is False


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_renders_usable_lane_as_number():
    md = report_with_northbound(summary(262, 35))
    body = shareholder_section(md)
    assert "### 北向资金（当日）" in body
    assert "| 沪股通 | -9.28 亿 | 100% |" in body


def test_unusable_lane_gets_no_number_in_table():
    """残缺序列的末值**不得**出现在表格里 —— 一旦进表就和真实读数长得一样。"""
    body = shareholder_section(report_with_northbound(summary(262, 35)))
    table = body[:body.index("\n\n", body.index("|:--"))]
    assert "深股通" not in table
    assert "379.75" not in table
    assert "深股通" in body, "但必须说明它为什么不可用"
    assert "不作为读数" in body


def test_all_unusable_says_so_without_numbers():
    md = report_with_northbound(summary(0, 0, hgt_last=None, sgt_last=None))
    body = shareholder_section(md)
    assert "当日北向读数不可用" in body
    assert "| 沪股通 |" not in body
    assert "|:--|--:|--:|" not in body, "没有可用读数就不该有表格"


def test_section_absent_when_nothing_fetched():
    """完全取不到就不渲染这一节。

    只查第九章：整篇报告在第十一章的「尚未接入」清单里本来就会提到北向
    （日频历史那部分确实没接），那是另一件事。
    """
    assert "北向资金" not in shareholder_section(report_with_northbound({}))
    md = az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
    })
    assert "### 北向资金（当日）" not in md
    assert "### 融资融券（两融）" not in md


def test_unimplemented_list_drops_what_was_integrated():
    """「尚未接入」清单必须跟着代码走。

    它列的是「模板有、报告没有」的章节，所以某章一旦接上就得从这里删掉 ——
    否则这份清单自己会变成一句谎话，而这正是它存在的意义所在。
    """
    body = md_between(az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {},
    }), "### 11.2 尚未接入的章节", "## 附录：")
    assert "融资动态" not in body, "两融已接入，不该还列在这里"
    assert "财联社" in body, "新闻源已接，清单里剩的是第二来源"
    assert "HKEX" in body, "北向只剩日频历史没接，要说清是哪一部分"


def md_between(md: str, start: str, end: str) -> str:
    i = md.index(start)
    return md[i:md.index(end, i + len(start))]


def test_renders_upstream_context():
    """必须写清上游为什么不可靠、权威源在哪 —— 否则读者会把当日情绪当持仓依据。"""
    body = shareholder_section(report_with_northbound(summary(262, 35)))
    assert "2024-08" in body
    assert "HKEX" in body
    assert "尚未接入" in body
