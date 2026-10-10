#!/usr/bin/env python3
"""
市场状态回测 — 检验 composite 是"状态描述"还是"择时信号"

分三段，每段回答一个不同的问题：

  段 1  宽度 standalone（2017-03 ~ 2026-09，2320 日）
        10 年历史，且各宽度指标与未来收益的秩相关。宽度是本框架的主输入，
        先单独验证它有没有信息，避免 composite 掩盖问题。

  段 2  composite 样本内（2021-09-13 ~ 2024-12-31）
        等权重，未调参。分档统计未来 20 日收益、IC、分组多空收益。

  段 3  composite 样本外（2025-01-01 ~ 2026-09-30）
        **调参时绝不看这段**。参数在段 2 定死（等权重，无需定），直接检验衰减。

另出净值对比：动态择时 vs 基准满仓，含总收益/最大回撤/夏普/胜率。

用法：
    cd python-service
    uv run --with duckdb python scripts/backtest_market_state.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from zettaranc.market_state import DIMENSION_WEIGHTS, compute_market_state  # noqa: E402
from datasources.config import config  # noqa: E402

BREADTH_CSV = SERVICE_ROOT / "data" / "market_breadth.csv"
BENCH_INDEX = "000300.SH"   # 沪深300：比上证宽，避免大盘蓝筹口径偏差
LOOKAHEAD = 20              # 未来 20 个交易日


# ──────────────────── 数据加载 ────────────────────


def load_breadth() -> Dict[str, Dict]:
    """读宽度缓存，返回 date -> row 的字典。"""
    if not BREADTH_CSV.exists():
        raise SystemExit(
            f"缺少 {BREADTH_CSV}，先跑："
            "uv run --with duckdb python scripts/build_breadth_cache.py"
        )
    out: Dict[str, Dict] = {}
    with BREADTH_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["date"]] = {
                "n_stocks": int(row["n_stocks"]),
                "pct_new_high_120": float(row["pct_new_high_120"]),
                "pct_above_ma60": float(row["pct_above_ma60"]),
                "pct_above_ma20": float(row["pct_above_ma20"]),
                "total_turnover": float(row["total_turnover"]),
            }
    return out


def load_index(index_code: str) -> List[Dict]:
    """
    读宽基指数日线，返回**倒序**（最新在前）的行列表。

    倒序是 zettaranc 各分析模块的既有契约（trend.py 模块 docstring 声明），
    market_state.compute_market_state 依赖这个顺序。
    """
    import duckdb

    con = duckdb.connect(config.get_db_path("index"), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT trade_date, open, high, low, close, volume, turnover
            FROM v_index_daily
            WHERE thscode = ? AND close IS NOT NULL
            ORDER BY trade_date DESC
            """,
            [index_code],
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "trade_date": str(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]) if r[5] is not None else 0.0,
            "turnover": float(r[6]) if r[6] is not None else 0.0,
        }
        for r in rows
    ]


# ──────────────────── 统计工具 ────────────────────


def forward_returns(closes_asc: List[float], horizon: int) -> List[Optional[float]]:
    """
    正序收盘价 → 未来 horizon 日收益率序列。

    最后 horizon 个位置为 None（没有未来数据）。对��长度保持一致，
    这样 scores[i] 和 fwd[i] 永远指向同一天。
    """
    n = len(closes_asc)
    out: List[Optional[float]] = [None] * n
    for i in range(n - horizon):
        base = closes_asc[i]
        if base > 0:
            out[i] = closes_asc[i + horizon] / base - 1.0
    return out


def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """秩相关。xs/ys 等长且无 None。"""
    if len(xs) < 3:
        return None

    def ranks(vals: List[float]) -> List[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        rk = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    mx, my = _mean(rx), _mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(rx)))
    dx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    dy = math.sqrt(sum((v - my) ** 2 for v in ry))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def max_drawdown(equity: List[float]) -> float:
    peak, mdd = equity[0] if equity else 1.0, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def annualize_sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mu, sd = _mean(returns), statistics.stdev(returns)
    if sd == 0:
        return 0.0
    return (mu / sd) * math.sqrt(252)


# ──────────────────── 段 1：宽度 standalone ────────────────────


def breadth_standalone(breadth: Dict[str, Dict], closes_asc: List[float]) -> None:
    print("\n" + "=" * 72)
    print("段 1  宽度指标 standalone（检验宽度本身有没有信息）")
    print("=" * 72)

    dates = sorted(breadth.keys())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    fwd = forward_returns(closes_asc, LOOKAHEAD)

    metrics = ["pct_above_ma60", "pct_above_ma20", "pct_new_high_120"]
    print(f"\n{'指标':<20}{'样本':>7}{'IC(20日)':>10}{'五分位多空差':>14}")
    print("-" * 72)

    for m in metrics:
        xs, ys = [], []
        for d in dates:
            i = date_to_idx[d]
            # 只在指数数据区间内取样（宽度比指数长 2 年）
            if i >= len(closes_asc) - LOOKAHEAD:
                continue
            if fwd[i] is None:
                continue
            xs.append(breadth[d][m])
            ys.append(fwd[i])
        if len(xs) < 50:
            print(f"{m:<20}{len(xs):>7}{'样本不足':>10}")
            continue

        ic = spearman(xs, ys)

        # 五分位：最高档减最低档的未来收益
        pairs = sorted(zip(xs, ys))
        k = len(pairs) // 5
        bottom = _mean([p[1] for p in pairs[:k]])
        top = _mean([p[1] for p in pairs[-k:]])

        print(f"{m:<20}{len(xs):>7}{ic:>10.4f}{(top - bottom) * 100:>13.2f}%")

    print("\n解读：IC 绝对值 <0.05 视为无信息，>0.08 算可用。")
    print("多空差 = 最高五分位未来20日均收益 - 最低五分位。")


# ──────────────────── composite 序列 ────────────────────


def build_state_series(
    index_rows: List[Dict], breadth: Dict[str, Dict]
) -> Tuple[List[Dict], List[float]]:
    """
    逐日计算市场状态。

    index_rows 是倒序（最新在前），所以倒着遍历得到时间正序，与 closes_asc 对齐。
    breadth_history 传入宽度历史（正序），供量能维度算均额分母。
    """
    asc_rows = list(reversed(index_rows))
    breadth_dates = sorted(breadth.keys())
    states: List[Dict] = []
    closes: List[float] = []

    for i, row in enumerate(asc_rows):
        if i < 120:
            continue
        window = asc_rows[max(0, i - 119): i + 1][::-1]  # 切回倒序契约
        date = row["trade_date"]
        b = breadth.get(date)
        # 截至当日的宽度历史（正序，不含未来）
        bpos = _bisect_left(breadth_dates, date)
        b_hist = [breadth[d] for d in breadth_dates[max(0, bpos - 60):bpos]]
        state = compute_market_state(window, b, b_hist)
        state["date"] = date
        state["close"] = row["close"]
        states.append(state)
        closes.append(row["close"])

    return states, closes


def _bisect_left(arr: List[str], target: str) -> int:
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def report_segment(
    label: str,
    states: List[Dict],
    closes: List[float],
    start: str,
    end: str,
) -> Optional[Dict]:
    """按日期区间切片后出 IC / 分档 / 净值对比。"""
    sel = [
        (s, c) for s, c in zip(states, closes)
        if start <= s["date"] <= end
    ]
    if len(sel) < 60:
        print(f"\n[{label}] 样本不足（{len(sel)} 天），跳过")
        return None

    scores = [s["composite"] for s, _ in sel]
    c_sel = [c for _, c in sel]

    print("\n" + "=" * 72)
    print(f"{label}   {sel[0][0]['date']} ~ {sel[-1][0]['date']}   {len(sel)} 个交易日")
    print("=" * 72)

    # IC
    fwd = forward_returns(c_sel, LOOKAHEAD)
    xs = [scores[i] for i in range(len(scores) - LOOKAHEAD) if fwd[i] is not None]
    ys = [fwd[i] for i in range(len(scores) - LOOKAHEAD) if fwd[i] is not None]
    ic = spearman(xs, ys) if xs else None
    print(f"\ncomposite 与未来 {LOOKAHEAD} 日收益 IC = {ic:.4f}" if ic else "\nIC 不可用")

    # 五分位
    print(f"\n{'分档':<10}{'天数':>7}{'未来20日均收益':>18}")
    print("-" * 48)
    pairs = sorted(
        ((scores[i], fwd[i]) for i in range(len(scores) - LOOKAHEAD) if fwd[i] is not None),
        key=lambda p: p[0],
    )
    k = max(1, len(pairs) // 5)
    buckets = [pairs[:k], pairs[k:2 * k], pairs[2 * k:3 * k], pairs[3 * k:4 * k], pairs[4 * k:]]
    names = ["最低20%", "次低20%", "中间20%", "次高20%", "最高20%"]
    for nm, bkt in zip(names, buckets):
        if bkt:
            print(f"{nm:<10}{len(bkt):>7}{_mean([p[1] for p in bkt]) * 100:>17.2f}%")
    if len(buckets[0]) and len(buckets[-1]):
        spread = (_mean([p[1] for p in buckets[-1]]) - _mean([p[1] for p in buckets[0]])) * 100
        print(f"\n多空差（最高 - 最低）= {spread:+.2f}%")

    # 净值对比
    nav_bh, nav_tm, rets_bh, rets_tm = [], [], [], []
    eq_bh = eq_tm = 1.0
    for i in range(len(scores) - 1):
        r = c_sel[i + 1] / c_sel[i] - 1.0
        rets_bh.append(r)
        # 分数 >=60 满仓，<=40 空仓，中间半仓
        s = scores[i]
        exposure = 1.0 if s >= 60 else (0.0 if s <= 40 else 0.5)
        rets_tm.append(r * exposure)
        eq_bh *= 1 + r
        eq_tm *= 1 + r * exposure
        nav_bh.append(eq_bh)
        nav_tm.append(eq_tm)

    total_bh = nav_bh[-1] - 1 if nav_bh else 0.0
    total_tm = nav_tm[-1] - 1 if nav_tm else 0.0
    win_bh = sum(1 for r in rets_bh if r > 0) / len(rets_bh) * 100 if rets_bh else 0.0
    win_tm = sum(1 for r in rets_tm if r > 0) / len(rets_bh) * 100 if rets_bh else 0.0

    print(f"\n{'':<14}{'基准满仓':>12}{'动态择时':>12}")
    print("-" * 40)
    print(f"{'总收益':<14}{total_bh * 100:>11.2f}%{total_tm * 100:>11.2f}%")
    print(f"{'最大回撤':<14}{max_drawdown(nav_bh) * 100:>11.2f}%{max_drawdown(nav_tm) * 100:>11.2f}%")
    print(f"{'年化夏普':<14}{annualize_sharpe(rets_bh):>11.2f}{annualize_sharpe(rets_tm):>11.2f}")
    print(f"{'胜率':<14}{win_bh:>11.1f}%{win_tm:>11.1f}%")

    return {
        "label": label,
        "n": len(sel),
        "start": sel[0][0]["date"],
        "end": sel[-1][0]["date"],
        "ic": ic,
        "total_bh": total_bh,
        "total_tm": total_tm,
        "mdd_bh": max_drawdown(nav_bh),
        "mdd_tm": max_drawdown(nav_tm),
        "sharpe_bh": annualize_sharpe(rets_bh),
        "sharpe_tm": annualize_sharpe(rets_tm),
    }


def report_dimension_ic(
    states: List[Dict], closes: List[float], split: str = "2025-01-01"
) -> None:
    """
    分维度 IC 归因 —— 每次回测都要看。

    初版就是漏了这一步：trend 维度 IC = -0.1995、volume/volatility 恒定 50
    全部被 composite 平均掉，表面上只看到"composite 没信号"，看不出是维度
    方向错了还是权重错了。分维度看才能定位。
    """
    print("\n" + "-" * 72)
    print("分维度 IC 归因（拆样本内 / 样本外）")
    print("-" * 72)
    print(f"{'维度':<18}{'权重':>7}{'样本内IC':>11}{'样本外IC':>11}{'全区间IC':>11}")
    print("-" * 72)

    fwd = forward_returns(closes, LOOKAHEAD)
    dims = list(DIMENSION_WEIGHTS.keys())

    for dim in dims:
        buckets = {"in": ([], []), "out": ([], []), "all": ([], [])}
        for i in range(len(states) - LOOKAHEAD):
            if fwd[i] is None:
                continue
            v = states[i]["dimensions"][dim]["score"]
            y = fwd[i]
            buckets["all"][0].append(v)
            buckets["all"][1].append(y)
            key = "in" if states[i]["date"] < split else "out"
            buckets[key][0].append(v)
            buckets[key][1].append(y)

        def fmt(pair):
            if len(pair[0]) < 20:
                return f"{'n/a':>11}"
            ic = spearman(pair[0], pair[1])
            return f"{ic:>11.4f}" if ic is not None else f"{'恒定':>11}"

        w = DIMENSION_WEIGHTS[dim]
        print(f"{dim:<18}{w:>7.2f}{fmt(buckets['in'])}{fmt(buckets['out'])}{fmt(buckets['all'])}")

    # composite 一起看，方便对比
    buckets = {"in": ([], []), "out": ([], []), "all": ([], [])}
    for i in range(len(states) - LOOKAHEAD):
        if fwd[i] is None:
            continue
        v, y = states[i]["composite"], fwd[i]
        buckets["all"][0].append(v)
        buckets["all"][1].append(y)
        key = "in" if states[i]["date"] < split else "out"
        buckets[key][0].append(v)
        buckets[key][1].append(y)

    def fmt(pair):
        if len(pair[0]) < 20:
            return f"{'n/a':>11}"
        ic = spearman(pair[0], pair[1])
        return f"{ic:>11.4f}" if ic is not None else f"{'恒定':>11}"

    print("-" * 72)
    print(f"{'composite':<18}{1.0:>7.2f}{fmt(buckets['in'])}{fmt(buckets['out'])}{fmt(buckets['all'])}")
    print("\n恒定 = 该维度所有日期分数相同（通常是 bug，见 _scale 方向 / 数据口径）")


def report_regime_split(
    states: List[Dict], closes: List[float], split: str = "2025-01-01"
) -> None:
    """
    检验 IC 在样本内/样本外是否**换向**，并逐年拆开看根因。

    逐年拆分是必要的：只看样本内/样本外两段，会把"某几年特殊"和"随机游走"
    混为一谈。逐年 IC 若反复反号，说明没有稳定方向，也就没有可用的 regime
    过滤条件 —— 不知道该按什么过滤。
    """
    fwd = forward_returns(closes, LOOKAHEAD)
    print("\n" + "=" * 72)
    print("样本内/样本外 方向一致性检验（换向 = 不稳定）")
    print("=" * 72)

    in_pairs, out_pairs = [], []
    for i in range(len(states) - LOOKAHEAD):
        if fwd[i] is None:
            continue
        pair = (states[i]["composite"], fwd[i], states[i]["date"])
        (in_pairs if pair[2] < split else out_pairs).append(pair)

    ic_in = spearman([p[0] for p in in_pairs], [p[1] for p in in_pairs]) if len(in_pairs) > 20 else None
    ic_out = spearman([p[0] for p in out_pairs], [p[1] for p in out_pairs]) if len(out_pairs) > 20 else None

    def s(x):
        return f"{x:+.4f}" if x is not None else "n/a"

    print(f"\n样本内 IC = {s(ic_in)}    样本外 IC = {s(ic_out)}")

    # 逐年
    years = sorted({st["date"][:4] for st in states})
    year_ics = {}
    for y in years:
        y0, y1 = f"{y}-01-01", f"{int(y) + 1}-01-01"
        pair = [(st["composite"], f) for st, f in zip(states, fwd)
                if f is not None and y0 <= st["date"] < y1]
        if len(pair) >= 30:
            year_ics[y] = spearman([p[0] for p in pair], [p[1] for p in pair])

    print("\n逐年 composite IC：")
    for y, ic in year_ics.items():
        print(f"  {y}: {ic:+.4f}")

    signs = [1 if v > 0 else -1 for v in year_ics.values()]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    if flips >= 2:
        print(f"\n⚠️  逐年符号翻转 {flips} 次 —— 判定为【随机游走 / 噪声】")
        print("   没有稳定方向，也就**没有可用的 regime 过滤**（不知道按什么过滤）。")
        print("   composite 不可用于择时，模块已在返回值里标 tradable=False。")
    elif ic_in is not None and ic_out is not None and ic_in * ic_out < 0:
        print("\n⚠️  符号相反 —— 特定区间异常，可考虑 regime 过滤。")
    else:
        print("\n✓  方向一致。")


def report_optimal_direction(
    states: List[Dict], closes: List[float]
) -> None:
    """
    最优方向检验 —— **连"事后选对方向"都跑不赢**，才算真正证伪。

    前面是按"composite 高=该买"这个既定方向回测。如果连**先看全样本 IC 符号、
    再按最有利的方向**去择时都跑不赢基准，那任何方向的权重调整都没有意义。
    这条检验专门堵住"方向选错了而已"这个辩解。
    """
    print("\n" + "=" * 72)
    print("最优方向检验（事后选最有利的方向，看能否跑赢）")
    print("=" * 72)

    scores = [st["composite"] for st in states]
    n = len(closes)

    # 基准满仓
    eq_bh, nav_bh = 1.0, []
    for i in range(n - 1):
        r = closes[i + 1] / closes[i] - 1.0
        eq_bh *= 1 + r
        nav_bh.append(eq_bh)

    # 两个方向各跑一次净值
    #
    # 反向时**不能简单把分数取负**再套同一套阈值：score=45 取负得 -45，
    # 会被 `s <= 40` 判成空仓，1103 天里 1102 天空仓，净值恒为 0 —— 一个
    # 看起来像"反向无效"、实际是分支写错的假结论。正确做法是保持分档不变，
    # 只交换高低两档的含义。
    results = {}
    for direction, label in [(1.0, "高分满仓（既定方向）"), (-1.0, "低分满仓（反向）")]:
        eq, nav, exposures = 1.0, [], []
        for i in range(n - 1):
            r = closes[i + 1] / closes[i] - 1.0
            s = scores[i]
            if direction > 0:
                exposure = 1.0 if s >= 60 else (0.0 if s <= 40 else 0.5)
            else:
                # 反向：低分才满仓，高分才空仓
                exposure = 1.0 if s <= 40 else (0.0 if s >= 60 else 0.5)
            exposures.append(exposure)
            eq *= 1 + r * exposure
            nav.append(eq)
        results[label] = (nav[-1] - 1, max_drawdown(nav))

    print(f"\n{'':<22}{'总收益':>12}{'最大回撤':>12}")
    print("-" * 48)
    print(f"{'基准满仓':<22}{(nav_bh[-1] - 1) * 100:>11.2f}%{max_drawdown(nav_bh) * 100:>11.2f}%")
    for label, (total, mdd) in results.items():
        print(f"{label:<22}{total * 100:>11.2f}%{mdd * 100:>11.2f}%")

    base_total = nav_bh[-1] - 1
    both_lose = all(t < base_total for t, _ in results.values())
    if both_lose:
        print("\n⚠️  两个方向都跑输基准 —— composite 无择时价值（已证伪）")
        print("   不是方向选错，是分数本身不含可利用的信息。")
    else:
        best = max(results.items(), key=lambda kv: kv[1][0])
        print(f"\n最优方向为「{best[0]}」，但需注意这是**事后选出的方向**（用了全样本信息）")
        print("   真实使用中你无法事先知道该用哪个方向，所以实际价值远低于此处显示。")


def main() -> int:
    print("=" * 72)
    print("市场状态回测 — 等权重，未调参")
    print(f"基准指数 {BENCH_INDEX}（沪深300）  预测窗口 {LOOKAHEAD} 日")
    print("=" * 72)

    breadth = load_breadth()
    index_rows = load_index(BENCH_INDEX)
    if not index_rows:
        print(f"未取到 {BENCH_INDEX} 的指数数据")
        return 1
    print(f"\n指数数据 {len(index_rows)} 行：{index_rows[-1]['trade_date']} ~ {index_rows[0]['trade_date']}")

    closes_full_asc = [r["close"] for r in reversed(index_rows)]
    breadth_standalone(breadth, closes_full_asc)

    states, closes = build_state_series(index_rows, breadth)
    if not states:
        print("\n未能构建状态序列")
        return 1
    print(f"\n状态序列 {len(states)} 天：{states[0]['date']} ~ {states[-1]['date']}")

    report_dimension_ic(states, closes)
    report_regime_split(states, closes)
    report_optimal_direction(states, closes)
    in_sample = report_segment("段 2  样本内 2021-09-13 ~ 2024-12-31", states, closes, "2021-09-13", "2024-12-31")
    out_sample = report_segment("段 3  样本外 2025-01-01 ~ 2026-09-30", states, closes, "2025-01-01", "2026-09-30")
    full = report_segment("全区间 2021-09 ~ 2026-09", states, closes, "2021-09-13", "2026-09-30")

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"\n{'':<12}{'样本内':>12}{'样本外':>12}")
    print("-" * 40)
    for key, name, fmt in [
        ("ic", "IC", "{:.4f}"),
        ("total_bh", "基准收益", "{:.2%}"),
        ("total_tm", "择时收益", "{:.2%}"),
        ("mdd_bh", "基准回撤", "{:.2%}"),
        ("mdd_tm", "择时回撤", "{:.2%}"),
    ]:
        a = fmt.format(in_sample[key]) if in_sample else "n/a"
        b = fmt.format(out_sample[key]) if out_sample else "n/a"
        print(f"{name:<12}{a:>12}{b:>12}")
    if full:
        print(f"\n全区间：基准 {full['total_bh']:.2%} / 择时 {full['total_tm']:.2%}"
              f"   回撤 {full['mdd_bh']:.2%} / {full['mdd_tm']:.2%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
