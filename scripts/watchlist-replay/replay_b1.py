#!/usr/bin/env python3
"""
B1「建仓波」买点回放 —— 判定这个形态标注到底有没有 edge。

背景
----
`python-service/zettaranc/annotator.py` 里的 `detect_build_wave_b1` 已经算出
B1 标注并画在 K 线工作台上。问题是：它是一个**买点**，还是一个只是看起来
像买点的图形？这份脚本不改任何判定逻辑，只回答「在 B1 日期之后，按可执行的
口径买入，收益是否显著高于同一批股票的无条件收益」。

口径（全部固定，不做参数搜索）
----------------------------
- 信号日 D = 探测器输出的 date（收盘后可知，用户次日盘前看到）
- 买入 D+1 开盘（用户能看到信号的最早可执行时点）
- 卖出 D+5 / D+10 / D+20 收盘（按该股票的交易日计数，不是自然日）
- 收益 = close[D+h] / open[D+1] - 1
- 基准 A（无条件）：同一批股票、同一段历史里所有可算的 (股票, 入场日) 组合
  的平均收益，入场日同样取 open[e]、出场取 close[e+h]，逐日、逐年对齐
- 基准 B（同日截面）：每个信号日，其入场日在全市场的等权平均收益。用来回答
  「是不是只是那天全市场都在涨」

数据
----
只读打开 `~/.hithink-finance/market.duckdb` 的 `v_daily_qfq`（前复权 OHLCV）。
B1 判定只吃 OHLCV，不碰 indicators 库。

用法
----
    python3 scripts/watchlist-replay/replay_b1.py            # 全市场
    python3 scripts/watchlist-replay/replay_b1.py --max-symbols 300   # 抽样自检
    python3 scripts/watchlist-replay/replay_b1.py --json out.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from array import array
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
ANNOTATOR_PATH = os.path.join(REPO_ROOT, "python-service", "zettaranc", "annotator.py")
DEFAULT_DB = os.path.expanduser("~/.hithink-finance/market.duckdb")

HORIZONS: Tuple[int, ...] = (5, 10, 20)

# 「3 根延迟」稳健性：annotator 的 detect 循环是 range(25, len(bars)-3)，
# 而 /api/annotate 传给它的 bars 一直排到最新一根 —— 所以**最新 3 根 K 线上的 B1
# 永远不会被发出来**。若照现状上线，信号实际可见日是 D+3，最早可执行买入是
# D+4 开盘。这时「D+5 收盘卖出」只剩 1 根 K 线，「D+10」= 6 根，「D+20」= 16 根。
LAG_HOLD: Dict[int, int] = {5: 1, 10: 6, 20: 16}
EXTRA_HORIZONS: Tuple[int, ...] = tuple(sorted(set(LAG_HOLD.values())))


# --------------------------------------------------------------------------
# 载入生产探测器（不复制、不重写逻辑，import 的就是线上那一份）
# --------------------------------------------------------------------------
def load_detector():
    spec = importlib.util.spec_from_file_location("zettaranc_annotator", ANNOTATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入标注器: {ANNOTATOR_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ZettarancAnnotator.detect_build_wave_b1


# --------------------------------------------------------------------------
# 统计小工具（纯内置函数，无第三方依赖）
# --------------------------------------------------------------------------
def pct(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    k = int(round(q * (len(sorted_vals) - 1)))
    k = max(0, min(len(sorted_vals) - 1, k))
    return sorted_vals[k]


def describe(vals: Sequence[float]) -> Dict[str, Optional[float]]:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    s = sorted(vals)
    return {
        "n": n,
        "mean": sum(s) / n,
        "median": pct(s, 0.5),
        "win_rate": sum(1 for v in s if v > 0) / n,
        "p10": pct(s, 0.10),
        "p90": pct(s, 0.90),
        "min": s[0],
        "max": s[-1],
    }


def mean_std(vals: Sequence[float]) -> Tuple[float, float]:
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    m = sum(vals) / n
    if n == 1:
        return m, 0.0
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, var ** 0.5


def pct_str(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def num(v: Optional[float], nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(db_path: str, max_symbols: int, min_bars: int) -> Dict[str, Any]:
    import duckdb  # 局部导入：只有真正跑数据时才需要

    detect_b1 = load_detector()

    con = duckdb.connect(db_path, read_only=True)
    symbols = [
        r[0]
        for r in con.execute(
            """
            SELECT thscode FROM v_daily_qfq
            GROUP BY thscode
            HAVING count(*) >= ?
            ORDER BY thscode
            """,
            [min_bars],
        ).fetchall()
    ]
    total_symbols = con.execute(
        "SELECT count(DISTINCT thscode) FROM v_daily_qfq"
    ).fetchone()[0]
    if max_symbols and max_symbols < len(symbols):
        symbols = symbols[:max_symbols]

    # 事件：(symbol, entry_date_iso, horizon -> return)
    events: List[Dict[str, Any]] = []
    # 无条件基准：逐 horizon 的全局收益数组 + 逐年 (count, sum, win) + 逐入场日均值
    bench_all: Dict[int, array] = {h: array("d") for h in HORIZONS}
    bench_year: Dict[int, Dict[str, List[float]]] = {
        h: {} for h in HORIZONS
    }  # year -> [count, sum, win]
    daily: Dict[int, Dict[str, List[float]]] = {
        h: {} for h in HORIZONS
    }  # entry_date -> [count, sum]
    # 额外的持有长度（只用于「3 根延迟」稳健性对照，只累计均值需要的量，不存分布）
    extra_sum: Dict[int, List[float]] = {h: [0.0, 0.0, 0.0] for h in EXTRA_HORIZONS}
    daily_extra: Dict[int, Dict[str, List[float]]] = {h: {} for h in EXTRA_HORIZONS}
    date_min: Optional[str] = None
    date_max: Optional[str] = None
    causality_samples: List[Dict[str, Any]] = []

    cur = con.cursor()
    for si, sym in enumerate(symbols):
        rows = cur.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM v_daily_qfq WHERE thscode = ? ORDER BY date
            """,
            [sym],
        ).fetchall()
        if not rows:
            continue
        dates = [r[0].isoformat() for r in rows]
        opens = [float(r[1]) for r in rows]
        closes = [float(r[4]) for r in rows]
        bars = [
            {
                "date": dates[k],
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for k, r in enumerate(rows)
        ]
        n = len(rows)
        if date_min is None or dates[0] < date_min:
            date_min = dates[0]
        if date_max is None or dates[-1] > date_max:
            date_max = dates[-1]

        # ---- 无条件基准：所有可执行的 (入场日, 出场日) 组合 ----
        for e in range(1, n):
            o = opens[e]
            if o <= 0:
                continue
            dd = dates[e]
            yr = dd[:4]
            for h in HORIZONS:
                x = e + h
                if x >= n:
                    continue
                r = closes[x] / o - 1.0
                bench_all[h].append(r)
                slot = bench_year[h].setdefault(yr, [0.0, 0.0, 0.0])
                slot[0] += 1
                slot[1] += r
                slot[2] += 1.0 if r > 0 else 0.0
                d = daily[h].setdefault(dd, [0.0, 0.0])
                d[0] += 1
                d[1] += r
            for L in EXTRA_HORIZONS:
                x = e + L
                if x >= n:
                    continue
                r = closes[x] / o - 1.0
                s = extra_sum[L]
                s[0] += 1
                s[1] += r
                s[2] += 1.0 if r > 0 else 0.0
                d = daily_extra[L].setdefault(dd, [0.0, 0.0])
                d[0] += 1
                d[1] += r

        # ---- B1 事件 + 前向收益 ----
        idx_of = {d: k for k, d in enumerate(dates)}
        full_events = detect_b1(bars)
        full_idx: Dict[int, Any] = {}
        for p in full_events:
            i = idx_of.get(str(p["date"]))
            if i is not None:
                full_idx.setdefault(i, p)

        # ---- 因果性抽检：把序列截到事件日之前，事件必须一模一样地出现 ----
        # 探测器只用到 bars[0..i]，所以 detect(bars[:i+4]) 与全序列在 i 及之前
        # 必须逐条一致。不一致 ⇒ 存在未来数据泄漏。
        if len(causality_samples) < 6 and full_idx:
            i_last = max(full_idx)
            prefix = detect_b1(bars[: i_last + 4])
            pre_idx = {idx_of[str(q["date"])] for q in prefix if str(q["date"]) in idx_of}
            expect = {j for j in full_idx if j <= i_last}
            causality_samples.append(
                {
                    "symbol": sym,
                    "cut_index": i_last,
                    "cut_date": dates[i_last],
                    "events_full_upto_cut": len(expect),
                    "events_on_prefix": len(pre_idx),
                    "identical": pre_idx == expect,
                }
            )

        for i, p in full_idx.items():
            e = i + 1  # D+1 开盘入场
            if e >= n or opens[e] <= 0:
                continue
            rets: Dict[int, float] = {}
            for h in HORIZONS:
                x = i + h  # 卖出 = D+h 收盘
                if x >= n:
                    continue
                rets[h] = closes[x] / opens[e] - 1.0
            if not rets:
                continue
            # D+4 开盘入场（照现状上线时信号实际可见日为 D+3），出场日不变
            rets_lag: Dict[int, float] = {}
            lag_e = i + 4
            if lag_e < n and opens[lag_e] > 0:
                for h in HORIZONS:
                    x = i + h
                    if x >= n or x <= lag_e:
                        continue
                    rets_lag[h] = closes[x] / opens[lag_e] - 1.0
            events.append(
                {
                    "symbol": sym,
                    "signal_date": str(p["date"]),
                    "entry_date": dates[e],
                    "entry_date_lag": dates[lag_e] if lag_e < n else None,
                    "j_value": p.get("metadata", {}).get("j_value"),
                    "wave_gain": p.get("metadata", {}).get("wave_gain"),
                    "rets": rets,
                    "rets_lag": rets_lag,
                }
            )
        if (si + 1) % 500 == 0:
            print(f"  ...{si + 1}/{len(symbols)} symbols", file=sys.stderr)

    # ---- 汇总 ----
    daily_mean: Dict[int, Dict[str, float]] = {
        h: {d: (c[1] / c[0] if c[0] else 0.0) for d, c in daily[h].items()}
        for h in HORIZONS
    }

    horizons_out: Dict[str, Any] = {}
    for h in HORIZONS:
        sig = [ev["rets"][h] for ev in events if h in ev["rets"]]
        sig_desc = describe(sig)
        bench_vals = bench_all[h]
        bench_desc = describe(bench_vals)

        matched: List[float] = []
        excess: List[float] = []
        for ev in events:
            if h not in ev["rets"]:
                continue
            mb = daily_mean[h].get(ev["entry_date"])
            if mb is None:
                continue
            matched.append(mb)
            excess.append(ev["rets"][h] - mb)
        matched_desc = describe(matched)
        excess_desc = describe(excess)

        # 逐日等权基准：每个交易日算一次截面均值，再对交易日取平均。
        # 事件按天严重聚集（单日最多数百个），用「逐笔」加权会把日历上
        # 某几天的行情放大成整个样本的结论。这个口径每个交易日只算一票。
        day_vals = [s / c for (c, s) in daily[h].values() if c]
        bench_day_desc = describe(day_vals)

        # 按入场月/入场日聚类的 t 值：重叠窗口会让逐笔 t 值虚高，
        # 按日聚类是这批数据下最保守的可解释口径（单日事件数可达数百）。
        by_month: Dict[str, List[float]] = {}
        by_dayx: Dict[str, List[float]] = {}
        for ev, ex in zip([e for e in events if h in e["rets"]], excess):
            by_month.setdefault(ev["entry_date"][:7], []).append(ex)
            by_dayx.setdefault(ev["entry_date"], []).append(ex)

        def _cluster_t(groups: Dict[str, List[float]]) -> Optional[float]:
            means = [sum(v) / len(v) for v in groups.values()]
            m, sd = mean_std(means)
            if len(means) < 2 or sd <= 0:
                return None
            return m / (sd / len(means) ** 0.5)

        clustered_t = _cluster_t(by_month)
        clustered_t_day = _cluster_t(by_dayx)

        horizons_out[str(h)] = {
            "signal": sig_desc,
            "benchmark_pooled": bench_desc,
            "benchmark_day_equal": bench_day_desc,
            "benchmark_same_day": matched_desc,
            "excess_vs_pooled_mean": (sig_desc["mean"] - bench_desc["mean"]) if sig else None,
            "excess_vs_day_equal_mean": (sig_desc["mean"] - bench_day_desc["mean"]) if sig else None,
            "excess_vs_same_day": excess_desc,
            "clustered_t_by_month": clustered_t,
            "clustered_months": len(by_month),
            "clustered_t_by_day": clustered_t_day,
            "clustered_days": len(by_dayx),
        }

    # ---- 逐年 ----
    years = sorted(
        {ev["entry_date"][:4] for ev in events}
        | {y for h in HORIZONS for y in bench_year[h]}
    )
    year_out: Dict[str, Any] = {}
    for y in years:
        row: Dict[str, Any] = {"n_events": sum(1 for ev in events if ev["entry_date"][:4] == y)}
        for h in HORIZONS:
            sig = [
                ev["rets"][h]
                for ev in events
                if ev["entry_date"][:4] == y and h in ev["rets"]
            ]
            b = bench_year[h].get(y)
            bench_mean = (b[1] / b[0]) if b and b[0] else None
            bench_win = (b[2] / b[0]) if b and b[0] else None
            dy = [v for d, v in (
                (d, (s / c) if c else None) for d, (c, s) in daily[h].items() if d[:4] == y
            ) if v is not None]
            bench_day_equal_mean = (sum(dy) / len(dy)) if dy else None
            excess = [
                ev["rets"][h] - daily_mean[h].get(ev["entry_date"], 0.0)
                for ev in events
                if ev["entry_date"][:4] == y and h in ev["rets"]
            ]
            row[f"h{h}"] = {
                "n": len(sig),
                "signal_mean": (sum(sig) / len(sig)) if sig else None,
                "signal_win": (sum(1 for v in sig if v > 0) / len(sig)) if sig else None,
                "bench_mean": bench_mean,
                "bench_day_equal_mean": bench_day_equal_mean,
                "bench_win": bench_win,
                "excess_mean": (sum(excess) / len(excess)) if excess else None,
                "excess_win": (sum(1 for v in excess if v > 0) / len(excess)) if excess else None,
            }
        year_out[y] = row

    # ---- 事件聚类：同一只票连续/近邻触发会把「看起来很多次」变成「其实没几次」 ----
    near_dup = 0
    prev_by_sym: Dict[str, Dict[str, Any]] = {}
    for ev in sorted(events, key=lambda e: (e["symbol"], e["signal_date"])):
        prev = prev_by_sym.get(ev["symbol"])
        if prev is not None:
            d0 = prev["entry_date"]
            d1 = ev["entry_date"]
            # 自然日差 ≤14 ⇒ 大概率中间不到 10 个交易日，属于同一波回调的重复触发
            if _days_between(d0, d1) <= 14:
                near_dup += 1
        prev_by_sym[ev["symbol"]] = ev
    by_day: Dict[str, int] = {}
    for ev in events:
        by_day[ev["signal_date"]] = by_day.get(ev["signal_date"], 0) + 1
    by_year_events: Dict[str, int] = {}
    for ev in events:
        y = ev["entry_date"][:4]
        by_year_events[y] = by_year_events.get(y, 0) + 1
    n_syms_with_events = len({ev["symbol"] for ev in events})
    max_day, max_day_n = ("", 0)
    for d, c in by_day.items():
        if c > max_day_n:
            max_day, max_day_n = d, c

    coverage = {
        "db": db_path,
        "symbols_total_in_db": total_symbols,
        "symbols_scanned": len(symbols),
        "min_bars": min_bars,
        "date_min": date_min,
        "date_max": date_max,
        "total_events": len(events),
        "symbols_with_events": n_syms_with_events,
        "distinct_signal_days": len(by_day),
        "max_events_in_one_day": max_day_n,
        "max_events_day": max_day,
        "events_within_14d_of_previous_same_symbol": near_dup,
        "events_per_year": by_year_events,
        "causality_check": causality_samples,
    }
    # ---- 「3 根延迟」稳健性：D+4 开盘买入，出场日不变 ----
    daily_mean_extra: Dict[int, Dict[str, float]] = {
        h: {d: (c[1] / c[0] if c[0] else 0.0) for d, c in daily_extra[h].items()}
        for h in EXTRA_HORIZONS
    }
    lag_out: Dict[str, Any] = {}
    for h in HORIZONS:
        L = LAG_HOLD[h]
        sig = [ev["rets_lag"][h] for ev in events if h in ev["rets_lag"]]
        exc = [
            ev["rets_lag"][h] - daily_mean_extra[L].get(ev["entry_date_lag"], 0.0)
            for ev in events
            if h in ev["rets_lag"]
        ]
        bm = (extra_sum[L][1] / extra_sum[L][0]) if extra_sum[L][0] else None
        lag_out[str(h)] = {
            "entry_lag_bars": 3,
            "holding_bars": L,
            "n": len(sig),
            "signal_mean": (sum(sig) / len(sig)) if sig else None,
            "signal_win": (sum(1 for v in sig if v > 0) / len(sig)) if sig else None,
            "bench_pooled_mean": bm,
            "excess_vs_same_day": (sum(exc) / len(exc)) if exc else None,
        }

    return {
        "coverage": coverage,
        "horizons": HORIZONS,
        "per_horizon": horizons_out,
        "per_year": year_out,
        "lag3_robustness": lag_out,
        "events": events,
    }


def _days_between(a: str, b: str) -> int:
    """两个 ISO 日期的自然日差（避免 import datetime 之外的依赖）。"""
    from datetime import date as _d

    ya, ma, da = (int(x) for x in a.split("-"))
    yb, mb, db = (int(x) for x in b.split("-"))
    return abs((_d(yb, mb, db) - _d(ya, ma, da)).days)


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def print_report(res: Dict[str, Any], show_events: int) -> None:
    cov = res["coverage"]
    print("=" * 78)
    print("B1 建仓波 —— 买点 edge 回放")
    print("=" * 78)
    print(f"数据源            : {cov['db']}")
    print(
        f"覆盖              : {cov['symbols_scanned']}/{cov['symbols_total_in_db']} 个股票"
        f"（每只 ≥{cov['min_bars']} 根K线），{cov['date_min']} .. {cov['date_max']}"
    )
    print(f"B1 事件总数       : {cov['total_events']}")
    print(
        f"触发分布          : 涉及 {cov['symbols_with_events']} 只票 / {cov['distinct_signal_days']} 个交易日；"
        f"单日最多 {cov['max_events_in_one_day']} 个（{cov['max_events_day']}）；"
        f"其中 {cov['events_within_14d_of_previous_same_symbol']} 个与同票上一个事件相隔 ≤14 自然日"
    )
    print("口径              : 信号日 D 盘后可知 → D+1 开盘买入 → D+5/10/20 收盘卖出")
    cc = cov.get("causality_check") or []
    if cc:
        ok = all(c["identical"] for c in cc)
        print(
            f"因果性抽检        : {'通过' if ok else '失败'}（{len(cc)} 只票做前缀截断复算，"
            "事件集合逐条一致 ⇒ 无未来数据泄漏）"
        )
        for c in cc:
            print(
                f"    {c['symbol']:<11} 截到 {c['cut_date']}（idx {c['cut_index']}）: "
                f"全序列≤截点 {c['events_full_upto_cut']} 条 / 前缀复算 {c['events_on_prefix']} 条 / "
                f"{'一致' if c['identical'] else '不一致'}"
            )
    print()

    print("--- 分持有期：信号 vs 无条件基准 ---")
    hdr = (
        f"{'H':>3} {'n':>6} {'sig_mean':>9} {'sig_med':>9} {'sig_win':>8} "
        f"{'bench_eq':>9} {'exc_eq':>8} {'exc_pool':>9} {'exc_matched':>12} {'t_day':>7} {'t_month':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for h in res["horizons"]:
        o = res["per_horizon"][str(h)]
        s = o["signal"]
        print(
            f"{h:>3} {s['n']:>6} {pct_str(s['mean']):>9} {pct_str(s['median']):>9} "
            f"{s['win_rate'] * 100:>7.1f}% {pct_str(o['benchmark_day_equal']['mean']):>9} "
            f"{pct_str(o['excess_vs_day_equal_mean']):>8} {pct_str(o['excess_vs_pooled_mean']):>9} "
            f"{pct_str(o['excess_vs_same_day']['mean']):>12} "
            f"{num(o['clustered_t_by_day']):>7} {num(o['clustered_t_by_month']):>8}"
        )
    print("  bench_eq = 逐日等权基准（每个交易日一次截面均值，再对交易日取平均，每票权重相等）")
    print("  exc_eq = 信号均值 − bench_eq；exc_pool = 信号均值 − 逐笔加权的全样本均值；")
    print("  exc_matched = 每个信号 − 其入场日全市场均值（配对超额，最强控制）；t_* = 该超额按日/按月聚类的 t")
    print()
    print("分位数（信号 / 基准·逐笔加权，即单笔可比的分布）:")
    for h in res["horizons"]:
        o = res["per_horizon"][str(h)]
        s, b = o["signal"], o["benchmark_pooled"]
        print(
            f"  H={h:<2} 信号 p10/p50/p90 = {pct_str(s['p10'])} / {pct_str(s['median'])} / {pct_str(s['p90'])}"
            f"   基准 p10/p50/p90 = {pct_str(b['p10'])} / {pct_str(b['median'])} / {pct_str(b['p90'])}"
        )
    print()
    print("信号收益 − 同日全市场均值（配对超额）:")
    for h in res["horizons"]:
        e = res["per_horizon"][str(h)]["excess_vs_same_day"]
        print(
            f"  H={h:<2} mean={pct_str(e['mean'])} median={pct_str(e['median'])} "
            f"胜率={e['win_rate'] * 100:.1f}% p10={pct_str(e['p10'])} p90={pct_str(e['p90'])}"
        )
    print()

    print("--- 逐年：信号 vs 无条件基准（同一批股票、同一年入场） ---")
    print("  列内为 sig=信号均值  beq=逐日等权基准  bpool=逐笔加权基准  exc=配对超额(信号−同日全市场)  win=信号胜率")
    hdr2 = f"{'year':>4} {'events':>7} | " + " | ".join(
        f"{'H' + str(h):>10} {'sig':>8} {'beq':>8} {'bpool':>8} {'exc':>8} {'win%':>6}"
        for h in res["horizons"]
    )
    print(hdr2)
    print("-" * len(hdr2))
    for y, row in sorted(res["per_year"].items()):
        cells = []
        for h in res["horizons"]:
            c = row[f"h{h}"]
            cells.append(
                f"{'H' + str(h):>10} {pct_str(c['signal_mean']):>8} {pct_str(c['bench_day_equal_mean']):>8} "
                f"{pct_str(c['bench_mean']):>8} {pct_str(c['excess_mean']):>8} "
                f"{(c['signal_win'] * 100 if c['signal_win'] is not None else float('nan')):>5.1f}%"
            )
        print(f"{y:>4} {row['n_events']:>7} | " + " | ".join(cells))
    print()
    print("逐年一致性（excess 相对同日基准为正的年份数 / 总年份数）:")
    for h in res["horizons"]:
        pos = sum(
            1
            for row in res["per_year"].values()
            if row[f"h{h}"]["excess_mean"] is not None and row[f"h{h}"]["excess_mean"] > 0
        )
        tot = sum(1 for row in res["per_year"].values() if row[f"h{h}"]["excess_mean"] is not None)
        print(f"  H={h:<2} {pos}/{tot} 年为正")
    print()

    lag = res.get("lag3_robustness") or {}
    if lag:
        print("--- 稳健性：按现状上线时的 3 根延迟（信号 D+3 才可见 → D+4 开盘买入） ---")
        print(f"{'H':>3} {'hold':>5} {'n':>6} {'sig_mean':>9} {'sig_win':>8} {'bench':>9} {'exc':>8}")
        for h in res["horizons"]:
            c = lag[str(h)]
            print(
                f"{h:>3} {c['holding_bars']:>5} {c['n']:>6} {pct_str(c['signal_mean']):>9} "
                f"{(c['signal_win'] * 100 if c['signal_win'] is not None else float('nan')):>7.1f}% "
                f"{pct_str(c['bench_pooled_mean']):>9} {pct_str(c['excess_vs_same_day']):>8}"
            )
        print("  说明：出场日不变，故持有期缩短为 1/6/16 根；bench 为同一持有长度的逐笔加权基准，exc 为同日配对超额")
        print()

    if show_events:
        print(f"--- 前 {show_events} 个事件样例 ---")
        for ev in res["events"][:show_events]:
            print(
                f"  {ev['entry_date']} {ev['symbol']:<11} J={ev['j_value']} "
                f"wave={ev['wave_gain']}% rets="
                + ", ".join(f"H{h}:{pct_str(ev['rets'][h])}" for h in sorted(ev["rets"]))
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="B1 建仓波买点 edge 回放")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--max-symbols", type=int, default=0, help="0 = 全部")
    ap.add_argument("--min-bars", type=int, default=30, help="探测器自身要求 ≥30")
    ap.add_argument("--json", default="", help="把原始结果写到该路径")
    ap.add_argument("--show-events", type=int, default=0)
    args = ap.parse_args()

    res = run(args.db, args.max_symbols, args.min_bars)
    print_report(res, args.show_events)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2, default=float)
        print(f"\n原始结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
