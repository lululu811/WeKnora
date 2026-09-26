"""Shared fixtures for the python-service test suite.

Unit tests import the analysis modules directly.
E2E tests talk to a running instance over HTTP; point them elsewhere with
WEKNORA_PY_SERVICE_URL (defaults to the local dev instance on :50052).
"""

import os
import sys
from pathlib import Path

import pytest
import requests

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

BASE_URL = os.getenv("WEKNORA_PY_SERVICE_URL", "http://127.0.0.1:50052").rstrip("/")
TIMEOUT = float(os.getenv("WEKNORA_PY_SERVICE_TIMEOUT", "120"))


class Client:
    """Thin HTTP wrapper. Every call returns (status_code, parsed_body)."""

    def __init__(self, base_url: str, timeout: float = TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()

    def _call(self, method: str, path: str, **kwargs):
        resp = self.session.request(
            method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"_raw": resp.text}
        return resp.status_code, body

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, payload=None):
        return self._call("POST", path, json=payload)

    # --- domain helpers -------------------------------------------------

    def query(self, db: str, sql: str, limit=None, params=None):
        payload = {"db": db, "sql": sql}
        if limit is not None:
            payload["limit"] = limit
        if params is not None:
            payload["params"] = params
        return self.post("/query/", payload)

    def query_rows(self, db: str, sql: str, limit=None, params=None):
        status, body = self.query(db, sql, limit, params)
        assert status == 200, f"query failed: {status} {body}"
        assert body.get("success"), f"query reported failure: {body}"
        return body["data"]

    def bar_count(self, thscode: str) -> int:
        rows = self.query_rows(
            "market",
            "SELECT count(*) AS c FROM v_daily_qfq "
            f"WHERE thscode = '{thscode}'",
        )
        return rows[0]["c"]

    def latest_indicator_row(self, thscode: str):
        """Raw (un-COALESCEd) indicator row for the latest date, or None."""
        rows = self.query_rows(
            "indicators",
            "SELECT * FROM v_indicators_daily "
            f"WHERE thscode = '{thscode}' ORDER BY date DESC LIMIT 1",
        )
        return rows[0] if rows else None


@pytest.fixture(scope="session")
def client():
    c = Client(BASE_URL)
    try:
        c.get("/health")
    except requests.RequestException as exc:  # pragma: no cover - env problem
        pytest.skip(f"python-service not reachable at {BASE_URL}: {exc}")
    return c


def newest_first(chronological_closes, **extra):
    """Build rows in the shape fetch_market_data returns: rows[0] is the NEWEST bar.

    `chronological_closes[0]` is the OLDEST close.
    """
    rows = []
    n = len(chronological_closes)
    for i, close in enumerate(chronological_closes):
        row = {
            "date": f"d{i:02d}",
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "vol": 1_000_000.0,
            "ma5": 0.0, "ma10": 0.0, "ma20": 0.0,
            "ma60": 0.0, "ma120": 0.0, "ma250": 0.0,
            "cmf": 0.0, "mfi": 50.0, "obv": 0.0, "vwap": 0.0,
            "dif": 0.0, "dea": 0.0, "macd_hist": 0.0,
            "k": 50.0, "d": 50.0, "j": 50.0,
            "rsi6": 50.0, "rsi14": 50.0,
            "stoch_k": 50.0, "stoch_d": 50.0,
            "cci": 0.0, "willr": -50.0,
            "adx": 20.0, "di_plus": 0.0, "di_minus": 0.0,
            "st_dir": 0.0, "st_val": 0.0, "psar": 0.0,
            "aroon_up": 50.0, "aroon_down": 50.0,
            "vi_plus": 1.0, "vi_minus": 1.0,
            "bb_upper": close * 1.05, "bb_mid": close, "bb_lower": close * 0.95,
            "bb_width": close * 0.10, "atr": close * 0.02,
            "dc_upper": close * 1.05, "dc_lower": close * 0.95,
            "kc_upper": close * 1.05, "kc_mid": close, "kc_lower": close * 0.95,
            "zscore": 0.0, "lin_slope": 0.0,
            "cdl_hammer": 0.0, "cdl_shooting_star": 0.0, "cdl_doji": 0.0,
            "cdl_engulfing": 0.0, "cdl_harami": 0.0,
            "cdl_morning_star": 0.0, "cdl_evening_star": 0.0,
            "cdl_piercing": 0.0, "cdl_dark_cloud": 0.0,
            "cdl_3white": 0.0, "cdl_3black": 0.0,
        }
        row.update(extra)
        rows.append(row)
    assert n == len(rows)
    return rows[::-1]


def zigzag(peaks, troughs, n=40):
    """Chronological close series through the given (bar, price) turning points."""
    points = sorted(peaks + troughs)
    prices = {}
    for (b0, v0), (b1, v1) in zip(points, points[1:]):
        for b in range(b0, b1 + 1):
            prices[b] = v0 + (v1 - v0) * (b - b0) / (b1 - b0)
    for b in range(points[-1][0], n):
        prices[b] = points[-1][1]
    return [prices.get(b, points[0][1]) for b in range(n)]
