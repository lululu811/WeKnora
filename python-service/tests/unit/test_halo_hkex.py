"""沪深股通日频（港交所官方每日统计）与第九章的日频小节。

北向自 2024-08 收紧盘中披露后，同花顺的分钟序列只剩「当日情绪」；**权威日频在
港交所**。这个源有三个实测过的坑，每一个都会产出「看起来正常但是错的」读数：

1. **同一文件里单位不统一**：`tradingTable` 是百万元，`top10Table` 是元。
   若统一按百万元算 top10，藥明康德的 23.72 亿会变成 2.37 万亿元 —— 比全市场还大。
2. **哨兵值 999,999,999**：每日额度余额（DQB）未披露时填这个数，必须当成「没有」。
3. **假期文件是存在的**：A 股休市而港股开市的日子（如 2026-10-02），北向块
   `tradingDay=0`、数值是「-」，而南向块 `tradingDay=1`、数值是 0.00。
   只看「有没有 tradingDay=1 的块」会停在这种日子上，产出「as_of 是假期、北向全空」
   的假读数 —— 比取不到更糟。
"""

import json

import pytest

from halo import analyze as az
from halo import extdata as ed
from halo.external import ExternalError


def slot(dimension):
    return {"dimension": dimension, "label": dimension, "anchors": {},
            "missing_anchors": [], "has_anchor": True, "score": None}


ALL_SLOTS = [slot(d) for d in
             ("moat", "stag", "esg", "management", "shareholder", "valuation", "risk")]


def block(market, trading_day, turnover=None, top10=None, dqb=None):
    """造一个港交所市场块。turnover 单位是**百万元**（文件原值）。"""
    rows = [[turnover if turnover is not None else "-"], ["5,269,396"],
            [dqb if dqb is not None else "-"], ["1,954.44"]]
    content = [{"style": 1, "table": {
        "classname": "tradingTable",
        "schema": [["Total Turnover", "Total Trade Count", "DQB", "ETF Turnover"]],
        "tr": [{"td": [r]} for r in rows],
    }}]
    if top10:
        content.append({"style": 1, "table": {
            "classname": "top10Table",
            "schema": [["Rank", "Stock Code", "Stock Name", "Total Turnover"]],
            # 注意：td 是「一个含多格的列表」
            "tr": [{"td": [[r, c, n, t]]} for r, c, n, t in top10],
        }})
    return {"id": 0, "date": "2026-09-30", "market": market,
            "tradingDay": trading_day, "content": content}


def js(blocks):
    """复刻港交所文件：BOM + `tabData = [...]`。"""
    return "\ufefftabData = " + json.dumps(blocks, ensure_ascii=False)


def report(ext, thscode="600519.SH"):
    return az.render_markdown({
        "thscode": thscode, "period": "2025-12-31",
        "halo": {"ok": False, "reason": "x"}, "growth": None, "facts": [],
        "announcements": [], "ai_slots": ALL_SLOTS, "narratives": {}, "external": ext,
    })


def nb_section(md):
    return md[md.index("### 沪深股通日频"):md.index("\n## 十、")]


# ---------------------------------------------------------------------------
# 单元格解析
# ---------------------------------------------------------------------------


def test_hkex_number_strips_commas():
    assert ed._hkex_number("101,257.88") == 101257.88


def test_hkex_number_treats_sentinel_as_missing():
    """999,999,999 是「未披露」，不是「额度还剩 999,999,999 百万」。"""
    assert ed._hkex_number("999,999,999") is None
    assert ed._hkex_number(999999999) is None


def test_hkex_number_handles_dash_and_garbage():
    assert ed._hkex_number("-") is None
    assert ed._hkex_number("") is None
    assert ed._hkex_number(None) is None
    assert ed._hkex_number("N/A") is None


def test_parse_hkex_js_strips_bom_and_wrapper():
    blocks = ed._parse_hkex_js(js([block("SSE Northbound", 1, "100")]))
    assert blocks and blocks[0]["market"] == "SSE Northbound"


def test_parse_hkex_js_tolerates_garbage():
    assert ed._parse_hkex_js("") == []
    assert ed._parse_hkex_js("不是数据") == []
    assert ed._parse_hkex_js("tabData = [坏的") == []


# ---------------------------------------------------------------------------
# 交易日回退
# ---------------------------------------------------------------------------


def test_skips_weekend_without_requesting(monkeypatch):
    """周末没有文件，别浪费一次限速请求。"""
    asked = []

    def fake(subdomain, path="", **kw):
        asked.append(path)
        return js([block("SSE Northbound", 1, "100")])

    monkeypatch.setattr(ed, "fetch_text", fake)
    monkeypatch.setattr(ed, "date", _FakeDate("2026-10-04"))   # 周日
    ed.hkex_northbound(max_lookback=4)
    assert not any("20261004" in p or "20261003" in p for p in asked), "周末不该请求"


def test_holiday_file_is_skipped(monkeypatch):
    """假期文件存在但北向 tradingDay=0 —— 必须继续回退，不能停在这天。"""
    holiday = js([block("SSE Northbound", 0), block("SSE Southbound", 1, "0.00")])
    real = js([block("SSE Northbound", 1, "101,257.88")])
    calls = {"n": 0}

    def fake(subdomain, path="", **kw):
        calls["n"] += 1
        return holiday if calls["n"] == 1 else real

    monkeypatch.setattr(ed, "fetch_text", fake)
    monkeypatch.setattr(ed, "date", _FakeDate("2026-10-02"))   # 周五，国庆假期
    out = ed.hkex_northbound(max_lookback=5)
    assert out["degraded"] is False
    assert out["as_of"] == "2026-09-30", "不能把假期日期写成 as_of"


def test_southbound_only_day_does_not_count(monkeypatch):
    """A 股休市港股开市的日子：南向 tradingDay=1 且有 0.00，但北向没数据。"""
    blocks = [block("SSE Northbound", 0), block("SSE Southbound", 1, "0.00"),
              block("SZSE Northbound", 0), block("SZSE Southbound", 1, "0.00")]
    assert ed._has_northbound_turnover(blocks) is False


def test_northbound_with_turnover_counts(monkeypatch):
    blocks = [block("SSE Northbound", 1, "101,257.88")]
    assert ed._has_northbound_turnover(blocks) is True


def test_degrades_when_nothing_found(monkeypatch):
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: js([block("SSE Northbound", 0)]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-10-05"))   # 周一
    out = ed.hkex_northbound(max_lookback=3)
    assert out["degraded"] is True
    assert out["as_of"] is None
    assert "tradingDay=0" in out["reason"]


def test_fetch_error_continues_lookback(monkeypatch):
    real = js([block("SSE Northbound", 1, "100")])
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExternalError("404")
        return real

    monkeypatch.setattr(ed, "fetch_text", fake)
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))   # 周三
    assert ed.hkex_northbound(max_lookback=3)["degraded"] is False


# ---------------------------------------------------------------------------
# 单位与结构
# ---------------------------------------------------------------------------


def test_trading_table_converted_from_millions(monkeypatch):
    """tradingTable 是百万元 → 必须乘 1e6。"""
    monkeypatch.setattr(ed, "fetch_text",
                        lambda *a, **k: js([block("SSE Northbound", 1, "101,257.88")]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))
    totals = ed.hkex_northbound()["markets"]["SSE Northbound"]["totals"]
    assert totals["Total Turnover"] == 101257.88 * 1_000_000


def test_top10_kept_in_yuan(monkeypatch):
    """top10Table 已经是元，**不能**再乘 1e6（否则比全市场还大）。"""
    blk = block("SSE Northbound", 1, "101,257.88",
                top10=[("1", "603259", "藥明康德", "2,371,589,492")])
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: js([blk]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))
    top = ed.hkex_northbound()["markets"]["SSE Northbound"]["top10"]
    assert top[0]["turnover"] == 2_371_589_492
    assert top[0]["code"] == "603259"


def test_top10_td_is_one_list_of_cells(monkeypatch):
    """td 是「一个含多格的列表」，不是「多个单格列表」——按后者解析会永远是 0 只。"""
    blk = block("SSE Northbound", 1, "100",
                top10=[("1", "603259", "藥明康德", "2,371,589,492")])
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: js([blk]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))
    assert len(ed.hkex_northbound()["markets"]["SSE Northbound"]["top10"]) == 1


def test_names_have_fullwidth_spaces_stripped(monkeypatch):
    blk = block("SSE Northbound", 1, "100",
                top10=[("1", "603259", "藥明康德\u3000\u3000\u3000", "1,000")])
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: js([blk]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))
    assert ed.hkex_northbound()["markets"]["SSE Northbound"]["top10"][0]["name"] == "藥明康德"


def test_dqb_sentinel_becomes_none(monkeypatch):
    blk = block("SSE Northbound", 1, "100", dqb="999,999,999")
    monkeypatch.setattr(ed, "fetch_text", lambda *a, **k: js([blk]))
    monkeypatch.setattr(ed, "date", _FakeDate("2026-09-30"))
    assert ed.hkex_northbound()["markets"]["SSE Northbound"]["totals"]["DQB"] is None


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def daily(turnovers, top10=None, as_of="2026-09-30"):
    markets = {}
    for market, value in turnovers.items():
        markets[market] = {
            "totals": {"Total Turnover": value},
            "top10": top10.get(market, []) if top10 else [],
        }
    return {"hkex": {"northbound_daily": {
        "as_of": as_of, "degraded": False, "reason": "", "markets": markets}}}


def test_renders_turnover_and_total():
    md = report(daily({"SSE Northbound": 1.01258e11, "SZSE Northbound": 1.06684e11}))
    body = nb_section(md)
    assert "| 沪股通 | 1,012.58 |" in body
    assert "| 深股通 | 1,066.84 |" in body
    assert "| **北向合计** | **2,079.42** |" in body


def test_highlights_analyzed_stock_in_top10():
    """本标的是否在北向十大活跃股里，是本节最该被看到的信息。"""
    top10 = {"SSE Northbound": [
        {"rank": "1", "code": "603259", "name": "藥明康德", "turnover": 2.37e9},
        {"rank": "2", "code": "600519", "name": "貴州茅台", "turnover": 1.5e9},
    ]}
    body = nb_section(report(daily({"SSE Northbound": 1e11}, top10)))
    assert "本标的出现在北向十大活跃股" in body
    assert "沪股通第 2 名" in body
    assert "★ 600519" in body


def test_no_callout_when_not_in_top10():
    top10 = {"SSE Northbound": [
        {"rank": "1", "code": "603259", "name": "藥明康德", "turnover": 2.37e9},
    ]}
    body = nb_section(report(daily({"SSE Northbound": 1e11}, top10)))
    assert "本标的出现在" not in body
    # 只查数据行：表头图例里本来就有「★ 为本标的」，那是说明不是标记。
    assert "| ★ " not in body


def test_section_absent_when_not_fetched():
    assert "沪深股通日频" not in report({})


def test_degraded_reason_is_shown():
    md = report({"hkex": {"northbound_daily": {
        "as_of": None, "degraded": True, "reason": "最近 7 天没有港交所北向成交额",
        "markets": {}}}})
    body = nb_section(md)
    assert "最近 7 天没有港交所北向成交额" in body


def test_missing_northbound_but_southbound_present():
    """只有南向数据时也要明说没有北向，而不是渲染一张空表。"""
    body = nb_section(report(daily({"SSE Southbound": 4.6e10})))
    assert "当日没有北向数据" in body


class _FakeDate:
    """替换 extdata.date，让回退逻辑在固定日期上可测。"""

    def __init__(self, iso):
        import datetime
        self._d = datetime.date.fromisoformat(iso)

    def today(self):
        return self._d
