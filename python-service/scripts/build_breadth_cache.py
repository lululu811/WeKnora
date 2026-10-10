#!/usr/bin/env python3
"""预计算全市场宽度序列并落盘缓存。

宽度指标（创新高占比 / 站上均线占比）需要对 2000 万行做窗口函数，回测时反复
重算要几十秒。宽度只依赖日线收盘价和日期，一天算一次就够，所以这里预计算成
CSV，回测直接读。

产出：
    python-service/data/market_breadth.csv
    date,n_stocks,pct_new_high_120,pct_above_ma60,pct_above_ma20,total_turnover

用法：
    cd python-service
    uv run --with duckdb python scripts/build_breadth_cache.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from datasources.config import config  # noqa: E402

OUT_PATH = SERVICE_ROOT / "data" / "market_breadth.csv"

# 窗口起点：需要 120 根 K 线才有 120 日新高，且 2016-01 起的日线足够铺满
WARMUP_START = "2016-01-01"
OUTPUT_START = "2017-01-01"

SQL = f"""
WITH base AS (
    SELECT
        thscode,
        date,
        close,
        turnover,
        ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY date) AS rn
    FROM v_daily_qfq
    WHERE date >= DATE '{WARMUP_START}'
),
rolled AS (
    SELECT
        thscode,
        date,
        close,
        turnover,
        rn,
        MAX(close) OVER (
            PARTITION BY thscode ORDER BY date
            ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
        ) AS hh120,
        AVG(close) OVER (
            PARTITION BY thscode ORDER BY date
            ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
        ) AS ma60,
        AVG(close) OVER (
            PARTITION BY thscode ORDER BY date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20
    FROM base
)
SELECT
    date,
    COUNT(*) AS n_stocks,
    AVG(CASE WHEN close >= hh120 THEN 1.0 ELSE 0.0 END) AS pct_new_high_120,
    AVG(CASE WHEN close > ma60 THEN 1.0 ELSE 0.0 END) AS pct_above_ma60,
    AVG(CASE WHEN close > ma20 THEN 1.0 ELSE 0.0 END) AS pct_above_ma20,
    SUM(turnover) AS total_turnover
FROM rolled
WHERE date >= DATE '{OUTPUT_START}' AND rn > 120
GROUP BY date
ORDER BY date
"""


def main() -> int:
    try:
        import duckdb
    except ImportError:
        print("需要 duckdb：uv run --with duckdb python scripts/build_breadth_cache.py")
        return 1

    con = duckdb.connect(config.get_db_path("market"), read_only=True)
    try:
        con.execute("SET memory_limit='4GB'")
        con.execute("SET threads=4")
        print("预计算全市场宽度序列（约 2000 万行窗口函数，稍等）...", flush=True)
        rows = con.execute(SQL).fetchall()
    finally:
        con.close()

    if not rows:
        print("未取到任何宽度数据，检查 market 库与日期区间")
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            "date,n_stocks,pct_new_high_120,pct_above_ma60,pct_above_ma20,total_turnover\n"
        )
        for row in rows:
            fh.write(
                f"{row[0]},{row[1]},{row[2]:.6f},{row[3]:.6f},{row[4]:.6f},{row[5]:.2f}\n"
            )

    print(f"已写入 {OUT_PATH}")
    print(f"  交易日 {len(rows)} 个：{rows[0][0]} ~ {rows[-1][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
