#!/usr/bin/env python3
"""
方向自适应策略回测 —— 用近 60 日 composite 方向决定当期正用还是反用。

背景：固定方向都不行（高分满仓 -7.77%，低分满仓 +13.91% 但方向是事后选的）。
逐年 composite IC 反号 4 次（-0.52/+0.07/-0.22/+0.33/-0.15）说明方向本身
随时间变化，所以试试**用历史方向自适应**。

规则（不看未来）：
  取过去 60 个交易日的 composite 与指数收益，算 Spearman IC
  IC > +threshold  → 顺势（高分满仓）
  IC < -threshold  → 逆势（低分满仓）
  否则             → 半仓
  当期方向只用**截至前一交易日**的数据

这个策略最大的风险是：60 日 IC 本身也是噪声，噪声的导数还是噪声。
所以必须看逐年表现——如果逐年仍反号，说明自适应也没用。

用法：
    cd python-service
    uv run --with duckdb python scripts/backtest_adaptive.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from scripts.backtest_market_state import (  # noqa: E402
    _bisect_left,
    annualize_sharpe,
    build_state_series,
    load_breadth,
    load_index,
    max_drawdown,
    spearman,
)

LOOKBACK = 60      # 判定方向的回看窗口
THRESHOLD = 0.05   # |IC| 超过它才敢定方向，否则半仓


def adaptive_backtest(states, closes, dates, lookback=LOOKBACK, threshold=THRESHOLD):
    """
    返回 (净值曲线, 每日方向, 方向切换次数)。

    严格无前视：i 日的方向只用 [i-lookback, i-1] 的数据判定。
    """
    n = len(closes)
    scores = [s["composite"] for s in states]

    nav_bh, nav_ad, dirs = 1.0, 1.0, []
    nav_bh_series, nav_ad_series = [], []
    rets_bh, rets_ad = [], []
    switches = 0
    prev_dir = None

    for i in range(lookback, n - 1):
        # ── 用过去 lookback 天判定当期方向（不含 i 日）──
        hist_scores = scores[i - lookback:i]
        hist_close = closes[i - lookback:i + 1]
        hist_rets = [
            hist_close[k + 1] / hist_close[k] - 1.0
            for k in range(len(hist_close) - 1)
            if hist_close[k] > 0
        ]
        ic = spearman(hist_scores, hist_rets) if len(hist_rets) >= 30 else None

        if ic is None or abs(ic) < threshold:
            direction = 0.0  # 半仓
        elif ic > 0:
            direction = 1.0   # 顺势
        else:
            direction = -1.0  # 逆势

        if prev_dir is not None and direction != 0.0 and prev_dir != 0.0 and direction != prev_dir:
            switches += 1
        if direction != 0.0:
            prev_dir = direction

        s = scores[i]
        if direction > 0:
            exposure = 1.0 if s >= 60 else (0.0 if s <= 40 else 0.5)
        elif direction < 0:
            exposure = 1.0 if s <= 40 else (0.0 if s >= 60 else 0.5)
        else:
            exposure = 0.5

        r = closes[i + 1] / closes[i] - 1.0
        rets_bh.append(r)
        rets_ad.append(r * exposure)
        nav_bh *= 1 + r
        nav_ad *= 1 + r * exposure
        nav_bh_series.append(nav_bh)
        nav_ad_series.append(nav_ad)
        dirs.append((dates[i], direction, round(exposure, 2)))

    return nav_bh_series, nav_ad_series, dirs, switches, rets_bh, rets_ad


def main() -> int:
    print("=" * 72)
    print("方向自适应策略回测")
    print(f"规则：近 {LOOKBACK} 日 composite-收益 IC 决定当期方向，|IC|<{THRESHOLD} 半仓")
    print("=" * 72)

    breadth = load_breadth()
    index_rows = load_index("000300.SH")
    states, closes = build_state_series(index_rows, breadth)
    dates = [s["date"] for s in states]
    print(f"\n序列 {len(states)} 天：{dates[0]} ~ {dates[-1]}")

    nav_bh, nav_ad, dirs, switches, rets_bh, rets_ad = adaptive_backtest(states, closes, dates)

    print(f"\n方向切换次数：{switches}")
    seg = {"顺势": 0, "逆势": 0, "半仓": 0}
    for _, d, _ in dirs:
        seg["顺势" if d > 0 else "逆势" if d < 0 else "半仓"] += 1
    print(f"方向分布：顺势 {seg['顺势']} 天 / 逆势 {seg['逆势']} 天 / 半仓 {seg['半仓']} 天")

    print(f"\n{'':<16}{'基准满仓':>12}{'方向自适应':>13}")
    print("-" * 44)
    print(f"{'总收益':<16}{(nav_bh[-1] - 1) * 100:>11.2f}%{(nav_ad[-1] - 1) * 100:>12.2f}%")
    print(f"{'最大回撤':<16}{max_drawdown(nav_bh) * 100:>11.2f}%{max_drawdown(nav_ad) * 100:>12.2f}%")
    print(f"{'年化夏普':<16}{annualize_sharpe(rets_bh):>11.2f}{annualize_sharpe(rets_ad):>12.2f}")

    # 逐年
    print(f"\n{'年份':<8}{'基准':>10}{'自适应':>10}{'超额':>10}{'顺势天':>8}{'逆势天':>8}")
    print("-" * 56)
    ad_by_year: Dict[str, float] = {}
    for y in sorted({d[:4] for d in dates}):
        idxs = [k for k, (d, _, _) in enumerate(dirs) if d[:4] == y]
        if not idxs:
            continue
        # 该年内的收益乘积。dirs[k] 对应的收益是 closes[LOOKBACK+k] -> closes[LOOKBACK+k+1]
        prod_bh = prod_ad = 1.0
        n_bh = 0
        for k in idxs:
            j = LOOKBACK + k
            if j >= len(closes) - 1:
                continue
            r = closes[j + 1] / closes[j] - 1.0
            _, _, e = dirs[k]
            prod_bh *= 1 + r
            prod_ad *= 1 + r * e
            n_bh += 1
        if n_bh < 10:
            continue
        tb = (prod_bh - 1) * 100
        ta = (prod_ad - 1) * 100
        ad_by_year[y] = ta - tb
        fwd_days = sum(1 for dt_, dir_, _ in dirs if dt_[:4] == y and dir_ > 0)
        rev_days = sum(1 for dt_, dir_, _ in dirs if dt_[:4] == y and dir_ < 0)
        print(f"{y:<8}{tb:>9.2f}%{ta:>9.2f}%{ta - tb:>9.2f}%{fwd_days:>8}{rev_days:>8}")

    signs = [1 if v > 0 else -1 for v in ad_by_year.values()]
    wins = sum(1 for v in ad_by_year.values() if v > 0)
    print(f"\n逐年跑赢基准：{wins}/{len(ad_by_year)} 年")
    if wins <= len(ad_by_year) / 2:
        print("⚠️  逐年超额多数为负 —— 自适应也没用，方向不可预测这一点没被解决。")
    else:
        print("✓ 多数年份跑赢，但需注意样本只有 5 年，且参数（60日/0.05）未做样本外检验。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
