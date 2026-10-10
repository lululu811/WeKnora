#!/usr/bin/env python3
"""
子区间稳定性诊断 —— 回答"符号换向"到底是 regime 差异还是过拟合残留。

背景：composite 全区间 IC = -0.0618，但拆开看样本内 -0.2339 / 样本外 +0.0962，
符号相反。三种可能，处理方式完全不同：

  A. **特定子区间异常**（如 2022-2024 波动市里宽度信号失效）
     → 可以加 regime 过滤，只在信号有效的市况下启用

  B. **随机游走**（各子区间 IC 忽正忽负，无规律）
     → 不可救，框架是噪声，放弃择时

  C. **20 日窗口太短**（换更长窗口符号就稳）
     → 调预测窗口即可，不必换维度

这个脚本做两件事：
  1. 按年切片看每个维度的 IC，找异常区间
  2. 扫 20/40/60/120 日预测窗口，看符号是否随窗口稳定

用法：
    cd python-service
    uv run --with duckdb python scripts/diagnose_regime_shift.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from zettaranc.market_state import DIMENSION_WEIGHTS, compute_market_state  # noqa: E402
from scripts.backtest_market_state import (  # noqa: E402
    _bisect_left,
    forward_returns,
    load_breadth,
    load_index,
    spearman,
)

BENCH_INDEX = "000300.SH"


def build_series(index_rows: List[Dict], breadth: Dict[str, Dict]):
    asc = list(reversed(index_rows))
    bdates = sorted(breadth.keys())
    states, closes, dates = [], [], []
    for i in range(120, len(asc)):
        win = asc[max(0, i - 119): i + 1][::-1]
        d = asc[i]["trade_date"]
        bp = _bisect_left(bdates, d)
        b_hist = [breadth[x] for x in bdates[max(0, bp - 60):bp]]
        st = compute_market_state(win, breadth.get(d), b_hist)
        st["date"] = d
        states.append(st)
        closes.append(asc[i]["close"])
        dates.append(d)
    return states, closes, dates


def ic_for(values: List[float], fwd: List[Optional[float]], lo: int, hi: int) -> Optional[float]:
    xs, ys = [], []
    for i in range(max(0, lo), min(hi, len(values) - 1)):
        if fwd[i] is None:
            continue
        xs.append(values[i])
        ys.append(fwd[i])
    if len(xs) < 30:
        return None
    return spearman(xs, ys)


def main() -> int:
    print("=" * 78)
    print("子区间稳定性诊断 —— 符号换向的根因")
    print("=" * 78)

    breadth = load_breadth()
    index_rows = load_index(BENCH_INDEX)
    states, closes, dates = build_series(index_rows, breadth)
    print(f"\n状态序列 {len(states)} 天：{dates[0]} ~ {dates[-1]}")

    dims = list(DIMENSION_WEIGHTS.keys())

    # ── 1. 按年切片 ──
    print("\n" + "=" * 78)
    print("1. 按年切片的 IC（20 日窗口）")
    print("=" * 78)
    fwd20 = forward_returns(closes, 20)
    years = sorted({d[:4] for d in dates})
    # years 是字符串，切片边界要用 int+1
    year_bounds = {y: (f"{y}-01-01", f"{int(y) + 1}-01-01") for y in years}

    header = f"{'维度':<18}" + "".join(f"{y:>9}" for y in years)
    print("\n" + header)
    print("-" * len(header))
    for dim in dims:
        vals = [s["dimensions"][dim]["score"] for s in states]
        cells = []
        for y in years:
            y0, y1 = year_bounds[y]
            lo = _bisect_left(dates, y0)
            hi = _bisect_left(dates, y1)
            ic = ic_for(vals, fwd20, lo, hi)
            cells.append(f"{ic:>9.3f}" if ic is not None else f"{'n/a':>9}")
        print(f"{dim:<18}" + "".join(cells))
    comp = [s["composite"] for s in states]
    cells = []
    for y in years:
        y0, y1 = year_bounds[y]
        lo = _bisect_left(dates, y0)
        hi = _bisect_left(dates, y1)
        ic = ic_for(comp, fwd20, lo, hi)
        cells.append(f"{ic:>9.3f}" if ic is not None else f"{'n/a':>9}")
    print("-" * len(header))
    print(f"{'composite':<18}" + "".join(cells))

    # 各维度符号一致性
    print("\n各维度逐符号一致性（负号越多越像均值回归，正负混杂=噪声）")
    for dim in dims + ["composite"]:
        src = [s["composite"] for s in states] if dim == "composite" else [s["dimensions"][dim]["score"] for s in states]
        signs = []
        for y in years:
            y0, y1 = year_bounds[y]
            lo = _bisect_left(dates, y0)
            hi = _bisect_left(dates, y1)
            ic = ic_for(src, fwd20, lo, hi)
            if ic is not None:
                signs.append("+" if ic > 0 else "-")
        same = len(set(signs)) == 1 if signs else False
        print(f"  {dim:<18} {''.join(signs)}  {'一致' if same else '不一致'}")

    # ── 2. 窗口扫描 ──
    print("\n" + "=" * 78)
    print("2. 预测窗口扫描（符号是否随窗口变稳）")
    print("=" * 78)
    print(f"\n{'维度':<18}" + "".join(f"{h:>9}日" for h in (20, 40, 60, 120)))
    print("-" * 66)
    fwd_cache = {h: forward_returns(closes, h) for h in (20, 40, 60, 120)}
    for dim in dims + ["composite"]:
        src = [s["composite"] for s in states] if dim == "composite" else [s["dimensions"][dim]["score"] for s in states]
        cells = []
        for h in (20, 40, 60, 120):
            ic = ic_for(src, fwd_cache[h], 0, len(states))
            cells.append(f"{ic:>10.3f}" if ic is not None else f"{'n/a':>10}")
        print(f"{dim:<18}" + "".join(cells))

    # ── 3. 结论 ──
    print("\n" + "=" * 78)
    print("结论判定")
    print("=" * 78)

    comp_by_year = {}
    for y in years:
        y0, y1 = year_bounds[y]
        lo = _bisect_left(dates, y0)
        hi = _bisect_left(dates, y1)
        ic = ic_for(comp, fwd20, lo, hi)
        if ic is not None:
            comp_by_year[y] = ic

    signs = [1 if v > 0 else -1 for v in comp_by_year.values()]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    print(f"\ncomposite 逐年 IC：")
    for y, ic in comp_by_year.items():
        print(f"  {y}: {ic:+.4f}")

    win60 = ic_for(comp, fwd_cache[60], 0, len(states))
    win120 = ic_for(comp, fwd_cache[120], 0, len(states))

    if flips >= 2:
        print("\n⚠️  逐年符号翻转 >=2 次 —— 判定为【B 随机游走 / 噪声】")
        print("   逐年 IC 无稳定方向，regime 过滤无从下手（不知道该按什么过滤）。")
        print("   建议：放弃择时用途，只保留状态描述，或整体换维度。")
    elif flips == 1:
        print("\n⚠️  逐年符号翻转 1 次 —— 倾向【A 特定区间异常】")
        print("   存在一个明显分界，可考虑 regime 过滤（只在该区间外启用）。")
        print(f"   但需注意：这也可能是过拟合残留，1 次翻转的证据强度有限。")
    else:
        print("\n✓  逐年符号一致")

    print(f"\ncomposite 全区间 IC：20日={ic_for(comp, fwd20, 0, len(states)):.4f}"
          f"  60日={win60:.4f}  120日={win120:.4f}")
    if win60 is not None and win120 is not None and abs(win120) > abs(win60) * 1.5:
        print("   → 长窗口 IC 明显更强，信号偏慢，20 日窗口确实太短（【C】）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
