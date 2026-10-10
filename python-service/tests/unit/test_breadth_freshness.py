"""`_load_breadth_for` 的宽度缓存新鲜度检测。

用临时 CSV 注入（`csv_path` 参数），覆盖三种情形：同步 / 缓存落后指数 / 整体同步
中断。**不连 DuckDB** —— 这个函数的全部价值在"什么时候该喊 stale"，与数据本身无关。

真库验证在 `tests/e2e/test_market_api.py::TestMarketStateInSnapshot`。
"""

import csv
import datetime as dt
from pathlib import Path

import pytest

import main as svc


def _write_breadth(path: Path, dates: list) -> Path:
    """写一个假的 market_breadth.csv。"""
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "date", "n_stocks", "pct_new_high_120",
            "pct_above_ma60", "pct_above_ma20", "total_turnover",
        ])
        for d in dates:
            w.writerow([d, 5000, 0.02, 0.50, 0.45, 1.2e12])
    return path


def _recent_business_days(n: int, end: dt.date) -> list:
    """从 end 往前取 n 个工作日（跳过周末），返回 YYYY-MM-DD 升序列表。"""
    out, cur = [], end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur -= dt.timedelta(days=1)
    return list(reversed(out))


@pytest.fixture
def cache(tmp_path):
    """建一个 70 个交易日的假缓存，返回构造路径的函数。"""
    def _make(end: dt.date):
        return _write_breadth(tmp_path / "market_breadth.csv", _recent_business_days(70, end))
    return _make


class TestBreadthMissing:
    def test_no_file_returns_neutral(self, tmp_path):
        current, history, fresh = svc._load_breadth_for(
            "2026-09-30", csv_path=str(tmp_path / "nope.csv")
        )
        assert current is None
        assert history == []
        assert fresh["stale"] is True
        assert "不存在" in fresh["note"]

    def test_empty_file_returns_neutral(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text(
            "date,n_stocks,pct_new_high_120,pct_above_ma60,pct_above_ma20,total_turnover\n"
        )
        current, history, fresh = svc._load_breadth_for("2026-09-30", csv_path=str(p))
        assert current is None
        assert history == []
        assert fresh["stale"] is True
        assert fresh["note"]

    def test_date_before_cache_returns_neutral(self, cache):
        p = cache(dt.date(2026, 9, 30))
        current, history, fresh = svc._load_breadth_for("2019-01-02", csv_path=str(p))
        assert current is None
        assert fresh["stale"] is True
        assert "没有该日期" in fresh["note"]


class TestBreadthFreshness:
    def test_same_day_is_fresh(self, cache):
        p = cache(dt.date(2026, 9, 30))
        current, history, fresh = svc._load_breadth_for("2026-09-30", csv_path=str(p))
        assert current is not None
        assert current["pct_above_ma60"] == 0.50
        assert fresh["last_date"] == "2026-09-30"
        assert fresh["lag_trading_days"] == 0
        assert fresh["stale"] is False

    def test_returns_sixty_day_history(self, cache):
        p = cache(dt.date(2026, 9, 30))
        _, history, _ = svc._load_breadth_for("2026-09-30", csv_path=str(p))
        assert len(history) == 60

    def test_lag_computed_when_cache_behind(self, cache):
        """缓存只到 09-30，请求 10-20 → 应报 stale 并说明落后天数。"""
        p = cache(dt.date(2026, 9, 30))
        _, _, fresh = svc._load_breadth_for("2026-10-20", csv_path=str(p))
        assert fresh["last_date"] == "2026-09-30"
        assert fresh["lag_trading_days"] == 20
        assert fresh["stale"] is True
        assert "落后" in fresh["note"]
        assert "build_breadth_cache" in fresh["note"]

    def test_small_lag_not_stale(self, cache):
        """差 2 天 < 阈值 5 天，不该报。"""
        p = cache(dt.date(2026, 9, 30))
        _, _, fresh = svc._load_breadth_for("2026-10-02", csv_path=str(p))
        assert fresh["lag_trading_days"] == 2
        assert fresh["stale"] is False

    def test_boundary_at_threshold(self, cache):
        """恰好等于阈值不算 stale（严格大于才报）。"""
        p = cache(dt.date(2026, 9, 30))
        # 2026-10-05 - 2026-09-30 = 5 天 == _BREADTH_STALE_DAYS
        _, _, fresh = svc._load_breadth_for("2026-10-05", csv_path=str(p))
        assert fresh["lag_trading_days"] == svc._BREADTH_STALE_DAYS
        assert fresh["stale"] is False

    def test_absolute_staleness_detected(self, cache):
        """缓存与指数**同步**滞后时，相对滞后也能查出来。

        这正是实测遇到的：2026-10-06 时 index 库与宽度缓存都停在 09-30，
        两者的 lag=0 判为新鲜，但相对今天已落后 6 天。
        """
        p = cache(dt.date(2020, 1, 20))
        _, _, fresh = svc._load_breadth_for("2020-01-20", csv_path=str(p))
        assert fresh["stale"] is True
        assert fresh["days_behind_today"] > svc._BREADTH_ABSOLUTE_MAX_DAYS
        assert "距今" in fresh["note"]

    def test_note_mentions_lag_even_when_fresh(self, cache):
        """没报 stale 时 note 也要说清落后多少天。"""
        p = cache(dt.date.today() - dt.timedelta(days=1))
        _, _, fresh = svc._load_breadth_for(
            (dt.date.today() - dt.timedelta(days=1)).isoformat(), csv_path=str(p)
        )
        assert fresh["note"]
        assert "距今" in fresh["note"]

    def test_freshness_keys_always_present(self, cache):
        p = cache(dt.date(2026, 9, 30))
        _, _, fresh = svc._load_breadth_for("2026-09-30", csv_path=str(p))
        for k in (
            "source", "last_date", "lag_trading_days",
            "days_behind_today", "stale", "note",
        ):
            assert k in fresh, f"缺字段 {k}"

    def test_source_reports_real_path_when_injected(self, cache):
        p = cache(dt.date(2026, 9, 30))
        _, _, fresh = svc._load_breadth_for("2026-09-30", csv_path=str(p))
        assert fresh["source"] == "market_breadth.csv"
