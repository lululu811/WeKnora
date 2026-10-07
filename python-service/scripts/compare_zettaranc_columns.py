"""
跨栈逐点比对：indicators.duckdb 的 zettaranc 列 vs 工作台（前端 TS）公式

背景
----
「评估闭环」的第一刀。整套评估成立的前提是"eval 算的数 = 图上画的数"。
前端唯一的生产公式实现在 `frontend/src/finance/components/kline/`：

    白线  zg_white   = calcDEMA(close, 10)          stock-score.ts:48
    黄线  dg_yellow  = calcLongBBI(close, 14/28/57/114)  stock-score.ts:109
    BBI   bbi        = calcBBI(close, 3/6/12/24)    indicators.ts:102

库侧这 3 列由 `a-stock/scripts/add_zettaranc_columns.py` 产出（不在本 checkout），
所以本脚本不假设库侧口径，而是**用两个候选行情源各算一遍，看哪个能对上**：

    A. market.duckdb `v_daily`      —— 不复权
    B. market.duckdb `v_daily_qfq`  —— 前复权（工作台 datafeed.ts:76 的默认值）

`INDICATORS_DB.md:3` 自称"同步自 v_daily，前复权"——这句话本身自相矛盾，
本脚本就是用来把它钉死的。

不可比的两列
------------
    zettaranc_rsl_rank_15 / _rank_105 —— 滚动窗口百分位排名，**前端没有对应实现**
    （前端那个同名的 RSL 已于 2026-10-01 改名为 Z_PCT_RET，因为它算的是涨跌幅。
      见 indicators.ts:120-123）所以这两列不存在"跨栈比对"这回事。

实测结论（2026-10-07）
--------------------
60 只等距抽样 × 104,820 bar × 3 列，行情源 = `v_daily_qfq`：

    白线  DEMA(10)              逐点精确 99.9868%   最大偏差 4.66e-09
    黄线  多空线(14/28/57/114)    逐点精确 100.00%    最大偏差 0
    BBI   牵牛绳(3/6/12/24)      逐点精确 100.00%    最大偏差 0

白线残留的 0.0132% 是 float64 求和顺序造成的末位噪声（相对误差 ~4e-11），
不是口径差异。

换成 `v_daily`（不复权）则最大偏差达 500+ —— 那是另一个指标。
**所以 `INDICATORS_DB.md:3` 那句"同步自 v_daily，前复权"是自相矛盾的错误表述，
真实来源是 `v_daily_qfq`。**

前端 vs 库侧的最大偏离 ≤0.0097（前端每根 toFixed(2)，库侧全程双精度）。
经济上可忽略，但**两端永远不可能逐点相等**——conformance 断言必须用容差。

三个逐字细节（照抄 TS，漏一个就对不上）
----------------------------------------
1. `calcEMA` 每一根就 `toFixed(2)`，**内层 EMA 先截到 2 位再进第二遍**。
   向量化实现不做这步，结果会差在小数第 3 位之后，且随 bar 数放大。
2. `calcLongBBI` 在 114 根数据不足时**降级平均**（用已有均线求均），
   不是返回 null。`indicators.ts` 的 `calcBBI` 反之——任一条没成形就 null。
   两者是有意的语义分叉，不抹平。
3. `calcSMA` 是滚动和，不是每次重算窗口。

用法
----
    uv run python scripts/compare_zettaranc_columns.py                # 默认 40 只
    uv run python scripts/compare_zettaranc_columns.py --stocks 100
    uv run python scripts/compare_zettaranc_columns.py --end 2026-09-30
"""

from __future__ import annotations

import argparse
import math
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional, Sequence

import duckdb

FIN_ROOT = "/Users/chenlei/.hithink-finance"
IND_DB = f"{FIN_ROOT}/indicators.duckdb"
MKT_DB = f"{FIN_ROOT}/market.duckdb"

# 从第几根 bar 起比较。EMA 在这份实现里没有 warmup（首根即种子），SMA 需要
# period 根，所以窗口开头天然不可比。114 是黄线最长周期，取 130 留出余量。
WARMUP = 130

COLUMNS = {
    "zg_white": ("zettaranc_zg_white_10", "白线 DEMA(10)"),
    "dg_yellow": ("zettaranc_dg_yellow_14", "黄线 多空线(14/28/57/114)"),
    "bbi": ("zettaranc_bbi", "BBI 牵牛绳(3/6/12/24)"),
}


# --------------------------------------------------------------------------
# JS toFixed(2) 语义：round-half-away-from-zero（与 Python round() 的
# banker's rounding 不同，这是比对能否对上的关键）
# --------------------------------------------------------------------------
def r2(x: float) -> Optional[float]:
    if x is None or not math.isfinite(x):
        return None
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ok(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# --------------------------------------------------------------------------
# 以下四个函数是 indicators.ts / stock-score.ts 的逐字移植
# --------------------------------------------------------------------------
def calc_ema(closes: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
    """indicators.ts:53"""
    k = 2 / (period + 1)
    out: List[Optional[float]] = []
    ema: Optional[float] = None
    for c in closes:
        if not _ok(c):
            out.append(None)
            continue
        ema = c if ema is None else c * k + ema * (1 - k)
        out.append(r2(ema))          # ← 逐字细节 1：每根就截 2 位
    return out


def calc_sma(closes: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
    """indicators.ts:74 —— 滚动和"""
    out: List[Optional[float]] = []
    s = 0.0
    for i, c in enumerate(closes):
        if not _ok(c):
            out.append(None)
            continue
        s += c
        if i >= period:
            prev = closes[i - period]
            s -= prev if _ok(prev) else 0
        out.append(r2(s / period) if i >= period - 1 else None)
    return out


def calc_dema(closes: Sequence[Optional[float]], period: int = 10) -> List[Optional[float]]:
    """stock-score.ts:48 —— EMA(EMA(close,p),p)，第二遍吃的是已截断的 ema1"""
    ema1 = calc_ema(closes, period)
    k = 2 / (period + 1)
    out: List[Optional[float]] = []
    ema2: Optional[float] = None
    for v in ema1:
        if v is None:
            out.append(None)
            continue
        ema2 = v if ema2 is None else v * k + ema2 * (1 - k)
        out.append(r2(ema2))
    return out


def calc_long_bbi(closes: Sequence[Optional[float]],
                  periods: Sequence[int] = (14, 28, 57, 114)) -> List[Optional[float]]:
    """stock-score.ts:109 —— 数据不足时降级平均（逐字细节 2）"""
    mas = [calc_sma(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        if all(v is not None for v in vals):
            out.append(r2(sum(vals) / 4))
            continue
        valid = [v for v in vals if v is not None]
        out.append(r2(sum(valid) / len(valid)) if valid else None)
    return out


def calc_bbi_strict(closes: Sequence[Optional[float]],
                    periods: Sequence[int] = (3, 6, 12, 24)) -> List[Optional[float]]:
    """indicators.ts:102 —— 任一条均线没成形就 null，不降级"""
    mas = [calc_sma(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        out.append(r2(sum(vals) / 4) if all(v is not None for v in vals) else None)
    return out


FORMULAS = {
    "zg_white": lambda c: calc_dema(c, 10),
    "dg_yellow": lambda c: calc_long_bbi(c, (14, 28, 57, 114)),
    "bbi": lambda c: calc_bbi_strict(c, (3, 6, 12, 24)),
}


# --------------------------------------------------------------------------
# 库侧口径：全程双精度，**不做任何取整**
#
# 实测结论（2026-10-07，60 只抽样 / 104820 bar × 3 列）：库里 zettaranc 列与这组
# 实现**逐点完全一致（100.00%，最大偏差 0.000000）**。库侧连末次 toFixed(2)
# 都没做，存的是原始 double。
#
# 也就是说：库和图算的是**同一个指标、同一个行情源**，差别只在取整纪律——
# 前端每根 toFixed(2)，库侧全程双精度。因此两端**永远不可能逐点相等**，
# 前端相对库侧的最大偏离 ≤0.0095（价格 17~40 上相对误差 ~2e-4）。
#
# 代价：conformance 断言**必须用容差（约 0.011）不能用 ==**。
# 收益：不存在"同名不同义"，评估闭环的地基是实的。
# --------------------------------------------------------------------------
def _ema_raw(vals: Sequence[Optional[float]], k: float) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    e: Optional[float] = None
    for x in vals:
        if not _ok(x):
            out.append(None)
            continue
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def _sma_raw(closes: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, c in enumerate(closes):
        if not _ok(c):
            out.append(None)
            continue
        s += c
        if i >= period:
            prev = closes[i - period]
            s -= prev if _ok(prev) else 0
        out.append(s / period if i >= period - 1 else None)
    return out


def lib_calc_dema(closes: Sequence[Optional[float]], period: int = 10
                  ) -> List[Optional[float]]:
    k = 2 / (period + 1)
    e1 = _ema_raw(closes, k)
    return _ema_raw(e1, k)


def lib_calc_long_bbi(closes: Sequence[Optional[float]],
                      periods: Sequence[int] = (14, 28, 57, 114)
                      ) -> List[Optional[float]]:
    mas = [_sma_raw(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        if all(v is not None for v in vals):
            out.append(sum(vals) / 4)
            continue
        valid = [v for v in vals if v is not None]
        out.append(sum(valid) / len(valid) if valid else None)
    return out


def lib_calc_bbi(closes: Sequence[Optional[float]],
                 periods: Sequence[int] = (3, 6, 12, 24)) -> List[Optional[float]]:
    mas = [_sma_raw(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        out.append(sum(vals) / 4 if all(v is not None for v in vals) else None)
    return out



LIB_FORMULAS = {
    "zg_white": lambda c: lib_calc_dema(c, 10),
    "dg_yellow": lambda c: lib_calc_long_bbi(c, (14, 28, 57, 114)),
    "bbi": lambda c: lib_calc_bbi(c, (3, 6, 12, 24)),
}



# --------------------------------------------------------------------------
def pick_stocks(con: duckdb.DuckDBPyConnection, n: int, end: str) -> List[str]:
    """
    确定性抽样：取最新交易日有数据、且历史足够长的票，按 thscode 排序后等距抽取。
    等距而非随机 —— 抽样结果可复现，且天然覆盖 000/002/300/600/601/603/688 各段。
    """
    rows = con.execute(
        """
        SELECT thscode FROM v_indicators_daily
        WHERE date = (SELECT max(date) FROM v_indicators_daily)
          AND zettaranc_bbi IS NOT NULL
        ORDER BY thscode
        """
    ).fetchall()
    codes = [r[0] for r in rows]
    if len(codes) <= n:
        return codes
    step = len(codes) / n
    return [codes[int(i * step)] for i in range(n)]


def load_series(con: duckdb.DuckDBPyConnection, view: str, code: str,
                end: str) -> List[Optional[float]]:
    rows = con.execute(
        f"SELECT close FROM {view} WHERE thscode = ? AND date <= ? ORDER BY date ASC",
        [code, end],
    ).fetchall()
    return [r[0] for r in rows]


def load_db_cols(con: duckdb.DuckDBPyConnection, code: str, end: str
                 ) -> List[dict]:
    cols = [c for c, _ in COLUMNS.values()]
    sel = ",".join(cols)
    rows = con.execute(
        f"SELECT date,{sel} FROM v_indicators_daily "
        f"WHERE thscode = ? AND date <= ? ORDER BY date ASC",
        [code, end],
    ).fetchall()
    return [dict(zip(["date"] + cols, r)) for r in rows]


def compare(mine: Sequence[Optional[float]], theirs: Sequence[Optional[float]]
            ) -> dict:
    n = 0
    both = 0
    miss_mine = 0
    miss_theirs = 0
    exact = 0
    same_2dp = 0
    max_abs = 0.0
    worst: Optional[tuple] = None
    for a, b in zip(mine, theirs):
        if a is None and b is None:
            n += 1
            both += 1
            continue
        if a is None:
            n += 1
            miss_mine += 1
            continue
        if b is None:
            n += 1
            miss_theirs += 1
            continue
        n += 1
        both += 1
        d = abs(a - b)
        if d < 1e-9:
            exact += 1
        if d <= 0.005 + 1e-9:
            same_2dp += 1
        if d > max_abs:
            max_abs = d
            worst = (a, b)
    return {
        "n": n, "both": both,
        "miss_mine": miss_mine, "miss_theirs": miss_theirs,
        "exact": exact, "same_2dp": same_2dp,
        "max_abs": max_abs, "worst": worst,
    }


def pct(a: int, b: int) -> float:
    return 100.0 * a / b if b else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=40)
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--show-worst", type=int, default=3)
    args = ap.parse_args()

    ind = duckdb.connect(IND_DB, read_only=True)
    mkt = duckdb.connect(MKT_DB, read_only=True)

    codes = pick_stocks(ind, args.stocks, args.end)
    print(f"抽样 {len(codes)} 只，基准日 {args.end}，warmup 跳过前 {WARMUP} 根\n")

    for view, tag in (("v_daily", "A 不复权 v_daily"),
                      ("v_daily_qfq", "B 前复权 v_daily_qfq（工作台默认）")):
        print("=" * 86)
        print(f"行情源 {tag}")
        print("=" * 86)
        totals = {k: dict(n=0, both=0, miss_mine=0, miss_theirs=0,
                          fexact=0, fsame=0, fmax=0.0,
                          lexact=0, lmax=0.0) for k in COLUMNS}
        worst_rows = []

        for code in codes:
            closes = load_series(mkt, view, code, args.end)
            drows = load_db_cols(ind, code, args.end)
            if len(closes) != len(drows) or len(closes) <= WARMUP:
                continue
            for key, (col, label) in COLUMNS.items():
                theirs = [r[col] for r in drows][WARMUP:]
                t = totals[key]

                # 前端口径：逐字 TS，每根 toFixed(2)
                st = compare(FORMULAS[key](closes)[WARMUP:], theirs)
                for f in ("n", "both", "miss_mine", "miss_theirs"):
                    t[f] += st[f]
                t["fexact"] += st["exact"]
                t["fsame"] += st["same_2dp"]
                t["fmax"] = max(t["fmax"], st["max_abs"])
                if st["worst"] and st["max_abs"] > 0.011:
                    worst_rows.append((st["max_abs"], code, label,
                                       st["worst"][0], st["worst"][1]))

                # 库侧口径：全程双精度，仅末次 r2
                st2 = compare(LIB_FORMULAS[key](closes)[WARMUP:], theirs)
                t["lexact"] += st2["exact"]
                t["lmax"] = max(t["lmax"], st2["max_abs"])

        for key, (col, label) in COLUMNS.items():
            t = totals[key]
            if not t["n"]:
                print(f"  {label:32s} 无可比样本")
                continue
            print(f"  {label}")
            print(f"    前端口径(每步截2位)   同2位 {pct(t['fsame'], t['both']):6.2f}%   "
                  f"最大偏差 {t['fmax']:.6f}")
            print(f"    库侧口径(全程双精度)   完全一致 {pct(t['lexact'], t['both']):6.2f}%   "
                  f"最大偏差 {t['lmax']:.6f}")
            print(f"    NULL 对齐             库有前端无 {t['miss_mine']:6d} | "
                  f"前端有库无 {t['miss_theirs']:6d}   "
                  f"（样本 {t['n']}）")
        if worst_rows:
            worst_rows.sort(reverse=True)
            print(f"  -- 前端口径偏差 >0.011 的样本（最多 {args.show_worst} 个）--")
            for d, code, label, a, b in worst_rows[: args.show_worst]:
                print(f"     {code} {label:24s} 前端={a:10.4f} 库={b:10.4f} Δ={d:.4f}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
