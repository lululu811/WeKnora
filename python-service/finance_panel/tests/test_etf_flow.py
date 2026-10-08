"""ETF 份额变动线测试。

**不连网、不连真实 DuckDB**：份额序列是合成数据，SQLite 用 tmp_path 临时文件，
行情层只用假的 `src` 对象测组装逻辑。所有断言锁的是「缺失 ≠ 0」与信号阈值。
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from finance_panel import etf_flow as flow  # noqa: E402
from finance_panel import store as panel_store  # noqa: E402
from finance_panel.etf_flow import (  # noqa: E402
    SIGNAL_SHARE_PCT,
    SIGNAL_TURNOVER_MULTIPLE,
    compute_flow,
    is_huijin,
    mark_signal,
    pct_change,
    reconcile_status,
)
from finance_panel.etf_shares import _parse_fmba, load_pool, pool_codes  # noqa: E402


def make_shares(series, granularity="quarterly"):
    """合成的升序份额观测。`series` = [shares, ...]。"""
    return [
        {
            "trade_date": f"2026-{3 * (i + 1):02d}-{'28' if (i + 1) % 2 else '30'}",
            "shares_outstanding": v,
            "share_change": None if i == 0 else v - series[i - 1],
            "granularity": granularity,
        }
        for i, v in enumerate(series)
    ]


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "panel.sqlite")


# ---------- 百分比与缺失 ----------

def test_pct_change_basic():
    assert pct_change(110.0, 100.0) == pytest.approx(10.0)


def test_pct_change_base_zero_returns_none_not_inf():
    """基准为 0 → None。返回 inf/除零异常都会让下游排序炸掉。"""
    assert pct_change(100.0, 0.0) is None
    assert pct_change(None, 100.0) is None
    assert pct_change(100.0, None) is None


def test_compute_flow_empty_series_is_missing():
    """没有份额观测 → missing，**绝不给 0**。"""
    r = compute_flow("510300.SH", shares=[])
    assert r["missing"] is True
    assert "无 ETF 份额观测" in r["reason"]
    assert r.get("share_change_pct") is None


def test_compute_flow_single_observation_has_no_prev():
    """只有一条观测时单期变动无法计算 → null + 明确 basis，不当 0。"""
    r = compute_flow("510300.SH", shares=make_shares([100.0]))
    assert r["missing"] is False
    assert r["share_change_pct"] is None
    assert r["change_basis"] == "no_prev_observation"


def test_compute_flow_latest_share_missing():
    """最新一条份额为 None → missing reason 指明日期。"""
    series = make_shares([100.0, 110.0])
    series[-1]["shares_outstanding"] = None
    r = compute_flow("510300.SH", shares=series)
    assert r["missing"] is True
    assert "份额缺失" in r["reason"]


# ---------- 单期与窗口累计 ----------

def test_compute_flow_pct_and_window():
    series = make_shares([100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
    r = compute_flow("510300.SH", shares=series, window=5)
    # 单期：150 / 140 - 1
    assert r["share_change_pct"] == pytest.approx((150 / 140 - 1) * 100, rel=1e-3)
    # 窗口：150 vs 6 条之前的第 1 条（150/100-1）
    assert r["change_5d_pct"] == pytest.approx(50.0, rel=1e-3)
    assert r["granularity"] == "quarterly"
    assert r["observations"] == 6


def test_compute_flow_window_insufficient_reports_reason():
    """窗口不足时给 reason，而不是拿现有几条算一个假的"5 日"变动。"""
    r = compute_flow("510300.SH", shares=make_shares([100.0, 110.0]), window=5)
    assert r["change_5d_pct"] is None
    assert "观测点不足" in (r["reason"] or "")


# ---------- 信号阈值 ----------

def test_signal_threshold_strictly_greater():
    """严格大于：正好 2.0% 不触发（贴阈值的量通常是"到关键位没突破"）。"""
    assert mark_signal({"share_change_pct": 2.0}) is False
    assert mark_signal({"share_change_pct": 2.01}) is True
    assert mark_signal({"share_change_pct": -2.01}) is True


def test_signal_by_turnover_multiple():
    assert mark_signal({"share_change_pct": 0.1}, multiple=1.382) is False
    assert mark_signal({"share_change_pct": 0.1}, multiple=1.4) is True


def test_signal_missing_metric_still_allows_other():
    """份额变动缺失时，成交放大仍可独立触发信号。"""
    assert mark_signal({"share_change_pct": None}, multiple=2.0) is True
    # 两项都缺 → 无信号（无信号 ≠ 反信号）
    assert mark_signal({"share_change_pct": None}, multiple=None) is False
    # 缺失结果本身不产生信号
    assert mark_signal({"missing": True, "share_change_pct": 99.0}) is False


def test_compute_flow_sets_signal_end_to_end():
    r = compute_flow("510300.SH", shares=make_shares([100.0, 110.0]))
    assert r["signal"] is True  # +10% > 2%


def test_default_thresholds_are_documented_values():
    assert SIGNAL_SHARE_PCT == 2.0
    assert SIGNAL_TURNOVER_MULTIPLE == 1.382


# ---------- 对账三态 ----------

def test_reconcile_status_pending_when_no_cross_data():
    """缺交叉数据是 pending，**不是 disputed** —— 否则真冲突被淹没。"""
    assert reconcile_status(10.0, 100.0, None) == "pending"
    assert reconcile_status(None, 100.0, 1000.0) == "pending"


def test_reconcile_status_verified_within_tolerance():
    # 占比 10%，份额 100 / 总量 1000 = 10% → 对得上
    assert reconcile_status(10.0, 100.0, 1000.0) == "verified"


def test_reconcile_status_disputed_keeps_raw_values():
    """对不上 → disputed。函数**只判状态不改数字**（没有返回值可改）。"""
    assert reconcile_status(50.0, 100.0, 1000.0) == "disputed"


def test_is_huijin():
    assert is_huijin("中央汇金投资有限责任公司") is True
    assert is_huijin("中国证券金融股份有限公司") is True
    assert is_huijin("华夏基金") is False
    assert is_huijin(None) is False


# ---------- 东财文本解析 ----------

def test_parse_fmba_html_table():
    html = (
        "var gmbd_apidata={ content:\"<table><thead><tr><th>日期</th>"
        "<th>期间申购（亿份）</th><th>期间赎回（亿份）</th><th>期末总份额（亿份）</th>"
        "<th>期末净资产（亿元）</th></tr></thead><tbody>"
        "<tr><td>2026-06-30</td><td class='tor'>61.27</td><td class='tor'>320.41</td>"
        "<td class='tor'>189.15</td><td class='tor'>948.72</td></tr>"
        "<tr><td>2026-03-31</td><td class='tor'>59.87</td><td class='tor'>499.88</td>"
        "<td class='tor'>448.29</td><td class='tor'>1,999.14</td></tr>"
        "</tbody></table>\",arryear:[2026]}"
    )
    rows = _parse_fmba(html)
    assert len(rows) == 2
    # 亿份 → 份（×1e8）
    assert rows[0]["shares_outstanding"] == pytest.approx(189.15e8)
    # 千分位也要能解析
    assert rows[1]["shares_outstanding"] == pytest.approx(448.29e8)
    assert rows[0]["granularity"] == "quarterly"


def test_parse_fmba_dash_becomes_none_not_zero():
    """东财用 '---' 表示无数据 → None，**绝不能变成 0 份**。"""
    html = (
        "var gmbd_apidata={ content:\"<table><tbody>"
        "<tr><td>2026-06-30</td><td>--</td><td>--</td><td>---</td><td>--</td></tr>"
        "</tbody></table>\",arryear:[]}"
    )
    rows = _parse_fmba(html)
    assert len(rows) == 1
    assert rows[0]["shares_outstanding"] is None


def test_parse_fmba_garbage_returns_empty():
    assert _parse_fmba("not a table") == []


# ---------- SQLite 落库 ----------

def test_upsert_shares_dedups_and_computes_change(db):
    panel_store.upsert_shares(
        [
            {"thscode": "510300.SH", "trade_date": "2026-03-31", "shares_outstanding": 100.0},
            {"thscode": "510300.SH", "trade_date": "2026-06-30", "shares_outstanding": 110.0},
        ],
        db_path=db,
    )
    series = panel_store.query_shares("510300.SH", db_path=db)
    assert len(series) == 2  # 去重后仍是 2 行
    assert series[0]["trade_date"] == "2026-03-31"
    assert series[1]["share_change"] == pytest.approx(10.0)


def test_upsert_shares_incremental_continues_change(db):
    """增量续抓的第一行要接上库内最后一条算变动，而不是永远 None。"""
    panel_store.upsert_shares(
        [{"thscode": "510300.SH", "trade_date": "2026-03-31", "shares_outstanding": 100.0}],
        db_path=db,
    )
    panel_store.upsert_shares(
        [{"thscode": "510300.SH", "trade_date": "2026-06-30", "shares_outstanding": 130.0}],
        db_path=db,
    )
    series = panel_store.query_shares("510300.SH", db_path=db)
    assert series[-1]["share_change"] == pytest.approx(30.0)


def test_latest_share_dates_drives_incremental(db):
    assert panel_store.latest_share_dates(["510300.SH"], db_path=db) == {"510300.SH": None}
    panel_store.upsert_shares(
        [{"thscode": "510300.SH", "trade_date": "2026-06-30", "shares_outstanding": 100.0}],
        db_path=db,
    )
    assert panel_store.latest_share_dates(["510300.SH"], db_path=db)["510300.SH"] == "2026-06-30"


def test_missing_share_does_not_poison_later_change(db):
    """一条 None 不能让后续所有变动变成 None（基准必须跳过缺失）。"""
    panel_store.upsert_shares(
        [
            {"thscode": "510300.SH", "trade_date": "2026-03-31", "shares_outstanding": 100.0},
            {"thscode": "510300.SH", "trade_date": "2026-06-30", "shares_outstanding": 110.0},
        ],
        db_path=db,
    )
    panel_store.upsert_shares(
        [
            {"thscode": "510300.SH", "trade_date": "2026-09-30", "shares_outstanding": None},
            {"thscode": "510300.SH", "trade_date": "2026-12-31", "shares_outstanding": 130.0},
        ],
        db_path=db,
    )
    series = panel_store.query_shares("510300.SH", db_path=db)
    last = series[-1]
    assert last["trade_date"] == "2026-12-31"
    # 基准是 110（跳过中间的 None），不是"相对一个不存在的值"
    assert last["share_change"] == pytest.approx(20.0)


def test_query_shares_unknown_code_returns_empty(db):
    """未同步过的代码返回空列表，不抛异常。"""
    assert panel_store.query_shares("999999.SH", db_path=db) == []


def test_query_holdings_unknown_code_returns_empty(db):
    assert panel_store.query_holdings("999999", db_path=db) == []


# ---------- 追踪池 ----------

def test_pool_yaml_has_expected_codes():
    codes = pool_codes()
    for expected in ("510300", "510310", "510050", "159919",
                     "510500", "512100", "588000", "159915"):
        assert expected in codes, f"追踪池缺 {expected}"


def test_pool_yaml_has_names():
    for item in load_pool():
        assert item["code"] and item["name"], f"池内条目缺注释: {item}"


# ---------- 组装层（假 DuckDB） ----------

class _FakeSrc:
    """最小 src 替身：只回放预置行，不连真实 DuckDB。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, sql, params=None):
        return self._rows


def test_compute_flows_sorts_by_abs_pct_and_nills_last(db):
    # 库内一律存**池内裸代码**（真实写入路径：端点把 pool_codes 的裸代码注入
    # 每行再 upsert）；compute_flows 也按裸代码查。带后缀的种子数据是在模拟
    # 一个不存在于真实路径的口径。
    panel_store.upsert_shares(
        [
            {"thscode": "510300", "trade_date": "2026-03-31", "shares_outstanding": 100.0},
            {"thscode": "510300", "trade_date": "2026-06-30", "shares_outstanding": 101.0},
            {"thscode": "510500", "trade_date": "2026-03-31", "shares_outstanding": 100.0},
            {"thscode": "510500", "trade_date": "2026-06-30", "shares_outstanding": 120.0},
        ],
        db_path=db,
    )
    # 假行情行：fetch_quotes 需要的字段 —— 每日一根，最后一根是"当日"
    quote_rows = []
    for code, name, turns in (
        ("510300.SH", "沪深300ETF华泰柏瑞", [2.0e9, 3.3e9]),
        ("510500.SH", "中证500ETF南方", [1.0e9, 1.0e9]),
    ):
        for i, turn in enumerate(turns):
            quote_rows.append(
                {
                    "thscode": code,
                    "name": name,
                    "trade_date": f"2026-09-{30 + i:02d}",
                    "close": 4.389,
                    "turnover": turn,
                }
            )

    src = _FakeSrc(quote_rows)
    pool = [{"code": "510300", "name": "x"}, {"code": "510500", "name": "y"}]

    result = asyncio.run(flow.compute_flows(src, pool, db_path=db))
    assert result["ok"] is True
    assert len(result["items"]) == 2
    # 100→120 (+20%) 必须排在 100→101 (+1%) 前面
    assert result["items"][0]["thscode"].startswith("510500")
    assert result["items"][0]["signal"] is True  # +20% > 2%
    # 510300 份额 +1% 未过阈值，但成交放大 3.3/2.0=1.65 > 1.382 → 仍触发
    assert result["items"][1]["signal"] is True
    assert result["items"][1]["turnover_multiple"] == pytest.approx(1.65, rel=1e-3)


def test_compute_flows_empty_pool_returns_fixed_shape():
    result = asyncio.run(flow.compute_flows(_FakeSrc([]), []))
    assert result["ok"] is False
    assert result["items"] == []
    assert result["missing"] == []
    assert "追踪池为空" in result["reason"]


def test_compute_flows_missing_shares_goes_to_missing_not_zero(db):
    """库内没有份额 → 进 missing，不进 items（0 顶替会读出假结论）。"""
    src = _FakeSrc([])
    result = asyncio.run(flow.compute_flows(src, [{"code": "510300", "name": "x"}], db_path=db))
    assert result["ok"] is True
    assert result["items"] == []
    assert result["missing"][0]["thscode"] == "510300"