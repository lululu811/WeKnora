"""
工作台指标公式的**逐字移植**，供评估管线现算。

为什么不直接读 indicators.duckdb 的列
------------------------------------
2026-10-07 实测（三只票 × 全历史，比对"跨柱发生的日期"）：

    触发        前端    库列   同日重合   占前端   占库列
    KDJ 金叉   10045   9888      9721    96.77%   98.31%
    MACD 金叉   4706   4226      2855    60.67%   67.56%

KDJ 差 3%，MACD **差 39%**。而且数值差的量级不随时间衰减（100 根之后中位数
仍有 0.03~1.70，最大 49.5）—— 那不是 EMA 种子没衰减干净的噪声，是两条
**不同的序列**。

所以：库里的 momentum_macd_12_26_9_* / momentum_kdj_9_3_* 不能代表工作台
在图上画的东西。用它们统计触发，会得到一个关于"另一个指标"的结论，而报告
读起来完全像是在说这个战法。这就是本项目反复在打的「声明与实际不符」。

对比之下，zettaranc 三列是**证明过**的（compare_zettaranc_columns.py：
黄线 100.00%、白线 99.9868% 逐位一致），所以那些列可以放心读。

移植的纪律
----------
每个函数旁边标了源文件的行号。移植必须逐字，包括 `toFixed(2)` 这类**中间**
取整 —— 漏掉它不会报错，只会让数值在小数第三位之后分叉，然后随 bar 数放大。

新增公式时必须同时：
  1. 在 indicators.yaml 的 triggers: 里登记（或在指标文档里说明为什么不用）
  2. 在 tests/unit/ 里加一条性质测试
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Sequence


def r2(x: Optional[float]) -> Optional[float]:
    """
    JS Number.prototype.toFixed(2) 的语义。

    **不能用 Python 内置 round** —— 那是 banker's rounding（round-half-to-even），
    JS 的是 round-half-away-from-zero。0.125 在两边一个给 0.13 一个给 0.12。
    """
    if x is None:
        return None
    if not isinstance(x, (int, float)):
        return None
    v = float(x)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return float(Decimal(repr(v)).quantize(Decimal("0.01"),
                                           rounding=ROUND_HALF_UP))


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and v == v and v not in (
        float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# 均线族 —— 与 compare_zettaranc_columns.py 里的移植一致，已逐位验证
# ---------------------------------------------------------------------------

def ema(closes: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
    """indicators.ts:53 calcEMA。内部不取整，只在输出时 toFixed(2)。"""
    k = 2 / (period + 1)
    out: List[Optional[float]] = []
    e: Optional[float] = None
    for c in closes:
        if not _finite(c):
            out.append(None)
            continue
        e = c if e is None else c * k + e * (1 - k)
        out.append(r2(e))
    return out


def sma(closes: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
    """indicators.ts:74 calcSMA —— 滚动和，不是每次重算窗口。"""
    out: List[Optional[float]] = []
    s = 0.0
    for i, c in enumerate(closes):
        if not _finite(c):
            out.append(None)
            continue
        s += c
        if i >= period:
            prev = closes[i - period]
            s -= prev if _finite(prev) else 0
        out.append(r2(s / period) if i >= period - 1 else None)
    return out


def dema(closes: Sequence[Optional[float]], period: int = 10
         ) -> List[Optional[float]]:
    """
    stock-score.ts:48 calcDEMA = EMA(EMA(·,p),p)。

    逐字细节：内层 ema() 每根已经 toFixed(2)，第二遍吃的是**截断后**的值。
    向量化实现不做这步，结果会差在小数第三位之后。
    """
    inner = ema(closes, period)
    k = 2 / (period + 1)
    out: List[Optional[float]] = []
    e: Optional[float] = None
    for v in inner:
        if v is None:
            out.append(None)
            continue
        e = v if e is None else v * k + e * (1 - k)
        out.append(r2(e))
    return out


def long_bbi(closes: Sequence[Optional[float]],
             periods: Sequence[int] = (14, 28, 57, 114)
             ) -> List[Optional[float]]:
    """
    stock-score.ts:109 calcLongBBI（黄线 / 大哥线）。

    逐字细节：数据不足 114 根时**降级平均**（用已有均线求均），不是返回 null。
    与 indicators.ts 的 calc_bbi 是一处有意的语义分叉，不抹平。
    """
    mas = [sma(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        if all(v is not None for v in vals):
            out.append(r2(sum(vals) / 4))
            continue
        valid = [v for v in vals if v is not None]
        out.append(r2(sum(valid) / len(valid)) if valid else None)
    return out


def bbi(closes: Sequence[Optional[float]],
        periods: Sequence[int] = (3, 6, 12, 24)) -> List[Optional[float]]:
    """
    indicators.ts:102 calcBBI（牵牛绳）。与 long_bbi 不同：任一条均线没成形
    就返回 null，不降级 —— 这是有意为之。
    """
    mas = [sma(closes, p) for p in periods]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas]
        out.append(r2(sum(vals) / 4) if all(v is not None for v in vals)
                   else None)
    return out


# ---------------------------------------------------------------------------
# MACD / KDJ —— 这两个库列**不可用**，必须现算（见模块头部实测）
# ---------------------------------------------------------------------------

def macd(closes: Sequence[float], short: int = 12, long: int = 26,
         signal: int = 9) -> Dict[str, List[Optional[float]]]:
    """
    indicators.ts:168 calcMACD (12,26,9)。

    逐字细节：
    - emaShort / emaLong 的种子是 closes[0]，但循环从 i=0 就开始递推，
      所以第 0 根是个恒等变换，dif[0] 恰为 0、dea[0] 也为 0；
    - 内部状态 **不取整**，只有 push 进 result 时才 toFixed(2)。
      跨柱判定用的是 dif 与 dea 的**大小关系**，所以输出取整不影响判定，
      但 dif/dea 的量级本身决定了事件有多"薄"。
    - hist = (dif - dea) * 2（两边都是这个约定）
    """
    ks, kl, km = 2 / (short + 1), 2 / (long + 1), 2 / (signal + 1)
    es = closes[0] if len(closes) else 0.0
    el = closes[0] if len(closes) else 0.0
    dea = 0.0
    difs: List[Optional[float]] = []
    deas: List[Optional[float]] = []
    hists: List[Optional[float]] = []
    for i, c in enumerate(closes):
        c = c if _finite(c) else 0.0          # `?? 0` 的对应
        es = c * ks + es * (1 - ks)
        el = c * kl + el * (1 - kl)
        dif = es - el
        dea = dif if i == 0 else dif * km + dea * (1 - km)
        difs.append(r2(dif))
        deas.append(r2(dea))
        hists.append(r2((dif - dea) * 2))
    return {"dif": difs, "dea": deas, "hist": hists}


def kdj(closes: Sequence[float], lows: Sequence[float], highs: Sequence[float],
        n: int = 9, k_smooth: int = 3, d_smooth: int = 3
        ) -> Dict[str, List[Optional[float]]]:
    """
    indicators.ts:201 calcKDJ (9,3,3)。

    逐字细节：k / d 的种子都是 **50**（不是 close），RSV 用滚动 n 根的
    最高/最低；high == low 时 RSV 取 50 而不是 0（除零保护也是口径的一部分）。
    """
    k, d = 50.0, 50.0
    ks_l: List[Optional[float]] = []
    ds_l: List[Optional[float]] = []
    js_l: List[Optional[float]] = []
    for i, c in enumerate(closes):
        lo = min(lows[max(0, i - n + 1):i + 1])
        hi = max(highs[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (c - lo) / (hi - lo) * 100
        k = ((k_smooth - 1) * k + rsv) / k_smooth
        d = ((d_smooth - 1) * d + k) / d_smooth
        ks_l.append(r2(k))
        ds_l.append(r2(d))
        js_l.append(r2(k_smooth * k - (d_smooth - 1) * d))
    return {"k": ks_l, "d": ds_l, "j": js_l}


# ---------------------------------------------------------------------------
# 注册表：config/indicators.yaml 的 triggers: 用 `compute: frontend` + `公式.字段`
# 引用这里的东西。loader 会校验公式名与字段名都在这张表里。
# ---------------------------------------------------------------------------
FORMULAS: Dict[str, Dict[str, object]] = {
    "dema": {"fn": dema, "fields": ["line"], "needs": ("close",)},
    "long_bbi": {"fn": long_bbi, "fields": ["line"], "needs": ("close",)},
    "bbi": {"fn": bbi, "fields": ["line"], "needs": ("close",)},
    "macd": {"fn": macd, "fields": ["dif", "dea", "hist"],
             "needs": ("close",)},
    "kdj": {"fn": kdj, "fields": ["k", "d", "j"],
            "needs": ("close", "low", "high")},
}
