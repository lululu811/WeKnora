"""finance_panel 测试。

**不连真实 DuckDB**：所有行情序列都是合成数据，SQL 拼装由 service 层负责，
这里只测纯函数规则与 SQLite 落库去重。
"""

import os
import sys
import tempfile

import pytest

# 允许从 python-service 根目录直接 pytest finance_panel/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from finance_panel import pulse  # noqa: E402
from finance_panel.pulse import (  # noqa: E402
    DEFAULT_HIGH,
    DEFAULT_LOW,
    DEFAULT_WINDOW,
    VOLUME_DOWN,
    VOLUME_NORMAL,
    VOLUME_UP,
    VERDICT_DISTRIBUTE,
    VERDICT_PUSH,
    classify_volume,
    classify_verdict,
    compute_pulse,
    is_missing,
)


def make_series(volumes, closes=None, start_day=1):
    """合成 [(date, volume, qfq_close), ...]，升序。

    `closes` 省略时给一条平直线（10.0），这样倍数组和涨跌组互不干扰 ——
    测四象限时才能一次只变一个维度。
    """
    if closes is None:
        closes = [10.0] * len(volumes)
    return [(f"2026-01-{start_day + i:02d}", v, closes[i]) for i, v in enumerate(volumes)]


# ---------- 中枢与倍数 ----------

def test_center_is_mean_of_preceding_window_excluding_today():
    """中枢 = 前 window 日均量，**不含当日**。

    前 20 日成交量 100..119（均值 109.5），当日 220 → 倍数 220/109.5 ≈ 2.0091。
    若中枢误含当日 220，均值会变成约 114.76，倍数变成 1.917 —— 方向错了：
    放量被自己的成交量稀释。
    """
    volumes = [100 + i for i in range(20)] + [220]
    res = compute_pulse("T.SZ", make_series(volumes), window=20)
    # multiple 统一保留 4 位小数，故容差取 1e-4
    assert res["multiple"] == pytest.approx(220 / 109.5, abs=1e-4)
    assert res["multiple"] == 2.0091


def test_multiple_uses_full_window_mean():
    """6 条中枢的均值 = 35，当日 300 → 8.5714（保留 4 位）。"""
    volumes = [10, 20, 30, 40, 50, 60] + [300]
    res = compute_pulse("T.SZ", make_series(volumes), window=6)
    assert res["multiple"] == pytest.approx(300 / 35, abs=1e-4)
    assert res["multiple"] == 8.5714


def test_pct_change_uses_qfq_close():
    closes = [10.0] * 20 + [11.0]
    res = compute_pulse("T.SZ", make_series([100] * 21, closes))
    assert res["pct_change"] == pytest.approx(10.0, abs=1e-6)


# ---------- 0.618 / 1.382 边界 ----------

def test_boundary_exactly_low_is_normal():
    """倍数正好 = 0.618 → normal（严格小于才算缩量）。"""
    volumes = [100.0 / DEFAULT_LOW] * DEFAULT_WINDOW + [100.0]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert res["multiple"] == pytest.approx(DEFAULT_LOW, abs=1e-6)
    assert res["volume_state"] == VOLUME_NORMAL


def test_boundary_just_below_low_is_down():
    volumes = [100.0 / DEFAULT_LOW] * DEFAULT_WINDOW + [100.0 * (DEFAULT_LOW - 1e-4)]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert res["volume_state"] == VOLUME_DOWN


def test_boundary_exactly_high_is_normal():
    """倍数正好 = 1.382 → normal。"""
    volumes = [100.0 / DEFAULT_HIGH] * DEFAULT_WINDOW + [100.0]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert res["multiple"] == pytest.approx(DEFAULT_HIGH, abs=1e-6)
    assert res["volume_state"] == VOLUME_NORMAL


def test_boundary_just_above_high_is_up():
    volumes = [100.0 / DEFAULT_HIGH] * DEFAULT_WINDOW + [100.0 * (DEFAULT_HIGH + 1e-4)]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert res["volume_state"] == VOLUME_UP


def test_classify_volume_boundaries_directly():
    assert classify_volume(0.617999) == VOLUME_DOWN
    assert classify_volume(DEFAULT_LOW) == VOLUME_NORMAL
    assert classify_volume(DEFAULT_HIGH) == VOLUME_NORMAL
    assert classify_volume(1.382001) == VOLUME_UP


# ---------- 四象限 ----------

def test_four_quadrants():
    """放量上涨=推升 / 放量下跌=出货 / 缩量上涨=推升 / 缩量下跌=出货。"""
    base = [100.0] * 20
    up_close = [10.0] * 20 + [11.0]
    down_close = [10.0] * 20 + [9.0]

    q1 = compute_pulse("T.SZ", make_series(base + [300.0], up_close))    # 放量涨
    q2 = compute_pulse("T.SZ", make_series(base + [300.0], down_close))  # 放量跌
    q3 = compute_pulse("T.SZ", make_series(base + [30.0], up_close))     # 缩量涨
    q4 = compute_pulse("T.SZ", make_series(base + [30.0], down_close))   # 缩量跌

    assert (q1["volume_state"], q1["verdict"]) == (VOLUME_UP, VERDICT_PUSH)
    assert (q2["volume_state"], q2["verdict"]) == (VOLUME_UP, VERDICT_DISTRIBUTE)
    assert (q3["volume_state"], q3["verdict"]) == (VOLUME_DOWN, VERDICT_PUSH)
    assert (q4["volume_state"], q4["verdict"]) == (VOLUME_DOWN, VERDICT_DISTRIBUTE)


def test_normal_volume_still_returns_verdict():
    """normal 时 verdict 照样给，展不展示由前端决定。"""
    volumes = [100.0] * 20 + [105.0]
    res = compute_pulse("T.SZ", make_series(volumes, [10.0] * 20 + [10.5]))
    assert res["volume_state"] == VOLUME_NORMAL
    assert res["verdict"] == VERDICT_PUSH


def test_flat_close_gives_none_verdict():
    """平盘 → verdict=None（不是空串）。"""
    res = compute_pulse("T.SZ", make_series([100.0] * 21))
    assert res["pct_change"] == pytest.approx(0.0, abs=1e-9)
    assert res["verdict"] is None


def test_flat_tolerance_absorbs_float_noise():
    """qfq 双精度残差不该被读成"涨 0.0% 然后判推升"。"""
    closes = [10.0] * 20 + [10.0 + 1e-13]
    res = compute_pulse("T.SZ", make_series([100.0] * 21, closes))
    assert res["verdict"] is None


# ---------- 缺失标记（缺失 ≠ 0）----------

def test_missing_when_window_insufficient():
    """少一条就不给结果。"""
    res = compute_pulse("T.SZ", make_series([100.0] * DEFAULT_WINDOW))
    assert is_missing(res)
    assert "样本不足" in res["reason"]


def test_missing_when_series_empty():
    res = compute_pulse("T.SZ", [])
    assert is_missing(res)
    assert res["reason"]


def test_missing_when_center_is_zero():
    """中枢为 0 → 除零，返回缺失，**不是** multiple=0。"""
    res = compute_pulse("T.SZ", make_series([0.0] * DEFAULT_WINDOW + [500.0]))
    assert is_missing(res)
    assert "中枢为 0" in res["reason"]
    assert "multiple" not in res


def test_missing_when_today_volume_none():
    volumes = [100.0] * 20 + [None]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert is_missing(res)


def test_missing_when_window_contains_none_volume():
    """窗口内任一条 None → 整个判缺失，不用部分数据凑中枢。"""
    volumes = [100.0] * 19 + [None, 300.0]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert is_missing(res)
    assert "中枢窗口" in res["reason"]


def test_missing_when_close_missing():
    volumes = [100.0] * 21
    closes = [10.0] * 20 + [None]
    res = compute_pulse("T.SZ", make_series(volumes, closes))
    assert is_missing(res)
    assert "收盘价" in res["reason"]


def test_missing_when_prev_close_zero():
    closes = [0.0] * 20 + [10.0]
    res = compute_pulse("T.SZ", make_series([100.0] * 21, closes))
    assert is_missing(res)
    assert "涨跌幅不可计算" in res["reason"]


def test_missing_result_always_carries_thscode():
    """缺失项必须带 thscode，否则 UI 无法对齐回请求列表。"""
    res = compute_pulse("301190.SZ", make_series([100.0] * 5))
    assert res["thscode"] == "301190.SZ"


def test_missing_is_never_zero():
    """核心不变式：缺失绝不以 0 参与统计。"""
    for series in ([], [100.0] * 3, [0.0] * 21):
        res = compute_pulse("T.SZ", make_series(series))
        assert is_missing(res)
        assert res.get("multiple") is None


# ---------- 除权日 ----------

def test_ex_dividend_day_qfq_gives_real_change_not_fake_crash():
    """除权日：原始价 10 → 5 是 -50% 假暴跌，qfq 价 10 → 10 才是 0%。

    这是整个模块用 qfq_close 而不是原始 close 的原因，也是回归测试。
    """
    volumes = [100.0] * 21
    raw_closes = [10.0] * 20 + [5.0]
    qfq_closes = [10.0] * 20 + [10.0]

    real = compute_pulse("T.SZ", make_series(volumes, qfq_closes))
    fake = compute_pulse("T.SZ", make_series(volumes, raw_closes))

    assert real["pct_change"] == pytest.approx(0.0, abs=1e-9)
    assert real["verdict"] is None
    assert fake["pct_change"] == pytest.approx(-50.0, abs=1e-6)
    assert fake["verdict"] == VERDICT_DISTRIBUTE


def test_post_ex_dividend_qfq_series_stays_continuous():
    """复权后整段序列连续，除权前后倍数照常可比。"""
    volumes = [100.0] * 20 + [100.0]
    qfq = [5.0] * 10 + [10.0] * 10 + [10.5]     # 前复权序列，连续
    res = compute_pulse("T.SZ", make_series(volumes, qfq))
    assert res["multiple"] == pytest.approx(1.0, abs=1e-6)
    assert res["pct_change"] == pytest.approx(5.0, abs=1e-6)


# ---------- 参数校验 ----------

def test_rejects_non_positive_window():
    res = compute_pulse("T.SZ", make_series([100.0] * 30), window=0)
    assert is_missing(res)


def test_custom_thresholds_override_defaults():
    """自定义阈值真的生效：倍数 1.5 在默认 (0.618, 1.382) 下是放量，
    但把 high 抬到 1.6 后它就落回 normal —— 说明走的是入参而非硬编码默认。
    """
    volumes = [100.0] * DEFAULT_WINDOW + [150.0]
    res = compute_pulse("T.SZ", make_series(volumes))
    assert res["multiple"] == pytest.approx(1.5, abs=1e-6)
    assert res["volume_state"] == VOLUME_UP  # 默认阈值

    custom = compute_pulse("T.SZ", make_series(volumes), low=1.4, high=1.6)
    assert custom["volume_state"] == VOLUME_NORMAL  # 抬高 high 后不再是放量


def test_result_shape_contains_required_keys():
    res = compute_pulse("T.SZ", make_series([100.0] * 21))
    for key in ("thscode", "date", "multiple", "pct_change", "volume_state", "verdict"):
        assert key in res


def test_classify_verdict_signs():
    assert classify_verdict(1.0) == VERDICT_PUSH
    assert classify_verdict(-1.0) == VERDICT_DISTRIBUTE
    assert classify_verdict(0.0) is None


def test_pulse_module_exposes_default_thresholds():
    assert (DEFAULT_WINDOW, DEFAULT_LOW, DEFAULT_HIGH) == (5, 0.618, 1.382)


# ---------- calendar store ----------

def test_calendar_store_dedupes_on_date_and_title():
    from finance_panel import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "panel.sqlite")
        store.init_db(db)

        batch = [
            {"date": "2026-10-10", "title": "CPI 数据公布", "category": "经济数据"},
            {"date": "2026-10-11", "title": "FOMC 利率决议", "category": "货币政策"},
        ]
        store.upsert_events(batch, db_path=db)
        assert len(store.query_events(db_path=db)) == 2

        # 同一条目重复同步不产生新行（东财每次 sync 都会重发全量）
        store.upsert_events(batch, db_path=db)
        rows = store.query_events(db_path=db)
        assert len(rows) == 2

        # 同 date+title 但 category 变了 → 更新而不是新增
        store.upsert_events(
            [{"date": "2026-10-10", "title": "CPI 数据公布", "category": "宏观"}], db_path=db
        )
        rows = store.query_events(db_path=db)
        assert len(rows) == 2
        assert [r for r in rows if r["date"] == "2026-10-10"][0]["category"] == "宏观"


def test_calendar_store_same_title_different_date_kept():
    """去重键含 date：同标题不同日期是两条事件，都要留。"""
    from finance_panel import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "panel.sqlite")
        store.init_db(db)
        store.upsert_events(
            [
                {"date": "2026-10-10", "title": "议息会议", "category": "货币政策"},
                {"date": "2026-10-11", "title": "议息会议", "category": "货币政策"},
            ],
            db_path=db,
        )
        assert len(store.query_events(db_path=db)) == 2


def test_calendar_store_skips_events_without_title_or_date():
    from finance_panel import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "panel.sqlite")
        store.init_db(db)
        written = store.upsert_events(
            [
                {"date": "", "title": "无日期", "category": "x"},
                {"date": "2026-10-10", "title": "", "category": "x"},
                {"date": "2026-10-10", "title": "有效", "category": "x"},
            ],
            db_path=db,
        )
        assert written == 1
        rows = store.query_events(db_path=db)
        assert [r["title"] for r in rows] == ["有效"]


def test_calendar_store_range_filter():
    from finance_panel import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "panel.sqlite")
        store.init_db(db)
        store.upsert_events(
            [
                {"date": "2026-10-09", "title": "B", "category": "经济数据"},
                {"date": "2026-10-10", "title": "A", "category": "经济数据"},
                {"date": "2026-10-20", "title": "C", "category": "经济数据"},
            ],
            db_path=db,
        )
        rows = store.query_events(
            start_date="2026-10-10", end_date="2026-10-20", db_path=db
        )
        assert [r["date"] for r in rows] == ["2026-10-10", "2026-10-20"]
        # 同日按 title 升序，保证前端渲染稳定
        all_rows = store.query_events(db_path=db)
        assert [r["title"] for r in all_rows] == ["B", "A", "C"]


def test_calendar_store_query_without_init_returns_empty():
    """表还没建时读接口返回空列表，不抛异常（sync 失败的首个读请求）。"""
    from finance_panel import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "missing.sqlite")
        assert store.query_events(db_path=db) == []


def test_store_db_path_env_override(monkeypatch, tmp_path):
    from finance_panel import store

    target = tmp_path / "custom.sqlite"
    monkeypatch.setenv("FINANCE_PANEL_DB", str(target))
    assert store.resolve_db_path() == str(target)


def test_store_default_path_is_hithink_finance_dir(monkeypatch):
    from finance_panel import store

    monkeypatch.delenv("FINANCE_PANEL_DB", raising=False)
    resolved = store.resolve_db_path()
    assert resolved.endswith(os.path.join(".hithink-finance", "finance_panel.sqlite"))
    assert "~" not in resolved
