"""两条 sync 链路的**固定 shape** 测试。

halo 不变式：sync 失败也必须返回固定形状 + reason，绝不抛异常、绝不半截结构。
这里全部用 monkeypatch 断网，**不发任何外网请求**。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etfholdings import sync as holdings_sync  # noqa: E402
from finance_panel import etf_shares as shares_mod  # noqa: E402


# ---------- A 线：份额 sync ----------


def test_sync_pool_empty_is_fixed_shape():
    r = shares_mod.sync_pool([])
    assert r["ok"] is False
    assert r["per_etf"] == {}
    assert r["total_rows"] == 0


def test_sync_pool_fetch_failure_is_reason_not_exception(monkeypatch):
    """抓取失败 → 每只 ETF 给 reason，整池仍回固定 shape。"""
    monkeypatch.setattr(shares_mod, "fetch_shares", lambda code, since=None: [])
    r = shares_mod.sync_pool(["510300", "510500"])
    assert set(r["per_etf"]) == {"510300", "510500"}
    assert r["ok"] is False
    assert "抓取失败" in r["per_etf"]["510300"]["reason"]


def test_sync_pool_raises_are_caught_per_etf(monkeypatch):
    """单只抛异常不能让整池炸 —— 必须降级成该 ETF 的 reason。"""
    def boom(code, since=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(shares_mod, "fetch_shares", boom)
    r = shares_mod.sync_pool(["510300", "510500"])
    assert r["ok"] is False
    assert "network down" in r["per_etf"]["510300"]["reason"]
    assert "network down" in r["per_etf"]["510500"]["reason"]


def test_sync_pool_already_latest_says_so(monkeypatch):
    """库里已有最新日期 → 明确说"已最新"，与"抓取失败"区分开。"""
    monkeypatch.setattr(shares_mod, "fetch_shares", lambda code, since=None: [])
    r = shares_mod.sync_pool(["510300"], last_dates={"510300": "2026-06-30"})
    assert "已最新" in r["per_etf"]["510300"]["reason"]


def test_sync_pool_ok_when_rows_returned(monkeypatch):
    def fake(code, since=None):
        return [{"trade_date": "2026-09-30", "shares_outstanding": 1.0, "granularity": "quarterly"}]

    monkeypatch.setattr(shares_mod, "fetch_shares", fake)
    r = shares_mod.sync_pool(["510300"])
    assert r["ok"] is True
    assert r["per_etf"]["510300"]["fetched"] == 1


def test_load_pool_missing_file_returns_empty(tmp_path):
    assert shares_mod.load_pool(str(tmp_path / "nope.yaml")) == []


# ---------- B 线：披露 sync ----------


def test_sync_one_etf_no_filing_is_reason(monkeypatch):
    """巨潮查无公告 → reason，不是异常。"""
    monkeypatch.setattr(holdings_sync.CninfoSource, "__init__", lambda self: None)
    monkeypatch.setattr(
        holdings_sync.CninfoSource, "find_filing",
        lambda self, code, report_type=None, max_scan_pages=2, required=False: None,
    )
    r = holdings_sync.sync_one_etf("510300", [])
    assert r["fetched"] == 0
    assert r["periods"] == []
    assert "巨潮未查到" in r["reason"]


def test_sync_one_etf_skips_known_periods(monkeypatch):
    """增量：库里已有的报告期直接跳过，不重复下 PDF。"""
    downloaded = []
    monkeypatch.setattr(holdings_sync.CninfoSource, "__init__", lambda self: None)

    class _Ann:
        title = "华泰柏瑞沪深300ETF2026年第一季度报告"
        pdf_url = "http://x/y.PDF"

    # 只对 Q1 返回命中（真实 find_filing 按 report_type 过滤），
    # 其余 report_type 返回 None；再叠加一层 sync 自身的 report_type 二次确认。
    def _find(self, code, report_type=None, max_scan_pages=2, required=False):
        return _Ann() if report_type == "q1" else None

    monkeypatch.setattr(holdings_sync.CninfoSource, "find_filing", _find)
    monkeypatch.setattr(
        holdings_sync.CninfoSource, "download_pdf",
        lambda self, ann, dest, **kw: downloaded.append(ann.title) or "/tmp/x.pdf",
    )
    r = holdings_sync.sync_one_etf("510300", ["2026-03-31"])
    assert r["fetched"] == 0
    assert downloaded == [], "已知报告期不应再下载 PDF"


def test_sync_one_etf_pdf_parse_failure_is_reason(monkeypatch):
    """PDF 无文本层 → reason，不抛。"""
    class _Ann:
        title = "华泰柏瑞沪深300ETF2026年第一季度报告"
        pdf_url = "http://x/y.PDF"

    monkeypatch.setattr(holdings_sync.CninfoSource, "__init__", lambda self: None)
    monkeypatch.setattr(
        holdings_sync.CninfoSource, "find_filing",
        lambda self, code, report_type=None, max_scan_pages=2, required=False: _Ann(),
    )
    monkeypatch.setattr(
        holdings_sync.CninfoSource, "download_pdf", lambda self, ann, dest, **kw: "/tmp/x.pdf"
    )
    # 标题是"第一季度报告"，只有 q1 会被二次确认放行
    monkeypatch.setattr(holdings_sync, "extract_pdf_text", lambda p: "   ")
    r = holdings_sync.sync_one_etf("510300", [])
    assert r["fetched"] == 0
    assert "无文本层" in str(r["per_period"].values())


def test_sync_pool_empty_pool_is_fixed_shape():
    r = holdings_sync.sync_pool([], {})
    assert r["ok"] is False
    assert r["per_etf"] == {}
    assert r["rows"] == []
    assert r["message"]


def test_sync_pool_per_etf_failure_isolated(monkeypatch):
    def boom(code, known, **kw):
        raise RuntimeError("cninfo blocked")

    monkeypatch.setattr(holdings_sync, "sync_one_etf", boom)
    r = holdings_sync.sync_pool(
        [{"code": "510300"}, {"code": "510500"}], {"510300": [], "510500": []}
    )
    assert r["ok"] is False
    assert "cninfo blocked" in r["per_etf"]["510300"]["reason"]
    assert "cninfo blocked" in r["per_etf"]["510500"]["reason"]


def test_sync_pool_collects_rows(monkeypatch):
    def fake(code, known, **kw):
        return {
            "thscode": code,
            "fetched": 1,
            "periods": ["2026-06-30"],
            "per_period": {
                "2026-06-30": {"rows": [{"thscode": code, "report_period": "2026-06-30",
                                         "holder_name": "中央汇金投资有限责任公司",
                                         "hold_share": 1.0, "hold_pct": 2.0,
                                         "status": "pending"}], "fetched": 1}
            },
            "reason": None,
        }

    monkeypatch.setattr(holdings_sync, "sync_one_etf", fake)
    r = holdings_sync.sync_pool([{"code": "510300"}], {"510300": []})
    assert r["ok"] is True
    assert len(r["rows"]) == 1
    assert "新增 1 条" in r["message"]


def test_extract_pdf_text_handles_missing_file():
    """不存在的 PDF → 空串，不抛。"""
    assert holdings_sync.extract_pdf_text("/nonexistent/x.pdf") == ""