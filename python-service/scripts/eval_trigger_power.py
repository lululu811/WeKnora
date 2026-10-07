"""
触发力评估：第一条带置信区间的结论

前置
----
`scripts/compare_zettaranc_columns.py` 已证明 `zettaranc_zg_white_10` /
`zettaranc_dg_yellow_14` 与前端公式**逐位一致**（黄线 100.00%，白线
99.9868%，残差为 float64 末位噪声），行情源同为 `v_daily_qfq` 前复权。

所以本脚本**直接读库里那两列**，不再在 Python 里重算——这正是第一刀
换来的东西：库侧列从此可以当作"工作台口径"的权威载体。

触发定义
--------
不在本文件里定义。全部从 `config/indicators.yaml` 的 `triggers:` 块来，经
`internal/indicators/genmeta.go` 生成进 `zettaranc/indicator_meta.py`：

    WHITE_CROSS_UP    白线 DEMA(10) 上穿 黄线 多空线(14/28/57/114)
    WHITE_CROSS_DOWN  白线下穿黄线

    判定：T-1 日 left <= right 且 T 日 left > right（cross_above，镜像同理）
    判定时刻：T 日收盘即可知 —— **不含任何未来信息**

定义只存在于 yaml 是刻意的：触发如果在本脚本里再写一遍，第二个消费者就得
重新推导，"图上标的那次"和"报告统计的那次"会悄悄分叉 —— 那正是 Z_RSL 与
砖型图两次同名不同义的成因。

口径（按 grill 锁定的决策实现，不偷换）
--------------------------------------
1. 复权：前复权 `v_daily_qfq`，与工作台 `datafeed.ts:76` 默认一致
2. 进场价 = **次日首个可成交价** = T+1 日开盘价。
   次日一字涨停开盘（开盘即封板）视为买不进 → 剔除并计数。
   **不接受"用 T 日收盘价算"这个选项。**
3. 停牌：后验窗口必须落在连续交易日上。用交易日历 dense index 做
   `cidx[n] - cidx[0] == n` 判定，跨停牌即剔除并单独统计剔除比例。
4. 幸存者偏差：显式统计"库内但已长期无行情"的代码数并在报告里写明
5. 双口径并出：绝对收益 + 相对沪深300 超额。
   benchmark 只有 2021-09-13 起（index.duckdb），**超额口径窗口短于绝对
   口径**，报告分开标注，不混为一谈
6. 最小样本门：N < MIN_SAMPLES 输出"不可判定"，不给漂亮胜率
7. 胜率给 Wilson 区间；均值给 bootstrap 95% CI（收益厚尾，不假设正态）

已知口径局限（报告里也会写）
--------------------------
- 涨跌停幅度按板块前缀推断，**不含 ST 的 ±5% 特例**（库里没有证券简称）
- 前复权对现金分红是近似等比缩放，除权日当天的涨跌停判定可能偏 1 个档位
- 超额收益按基准指数同期涨跌近似，未按停牌日对齐到标的的交易日

用法
----
    uv run python scripts/eval_trigger_power.py
    uv run python scripts/eval_trigger_power.py --horizons 5 20
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import duckdb

# 触发定义与列别名都从生成产物读，不在本文件里硬编码
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zettaranc.indicator_meta import (  # noqa: E402
    DUCKDB_COLUMNS,
    INDICATOR_META,
    TRIGGERS as YAML_TRIGGERS,
    trigger as trigger_def,
)

SCHEMA_VERSION = INDICATOR_META["schemaVersion"]

FIN_ROOT = "/Users/chenlei/.hithink-finance"
IND_DB = f"{FIN_ROOT}/indicators.duckdb"
MKT_DB = f"{FIN_ROOT}/market.duckdb"
IDX_DB = f"{FIN_ROOT}/index.duckdb"
BENCH = "000300.SH"

MIN_SAMPLES = 300
BOOTSTRAP = 2000

#: 双侧 95% 正态分位数。**不要**在别处硬编码 1.96 —— 那比它大 1.8e-5，
#: 测试里因为写 1.96 而对不上曾被误判成"浮点累加顺序差异"。
Z95 = 1.959963985

TRIGGERS = {t["id"]: t["label"] for t in YAML_TRIGGERS}


def _sql_cross(t: dict) -> str:
    """
    把一条 trigger 定义翻成 SQL 判定式。

    形状固定：cross_above 是 `left <= right` 的前一根 与 `left > right` 的当根，
    cross_below 是镜像。只用 lag/当前两根，不引入任何未来信息。
    """
    left = DUCKDB_COLUMNS[t["left"]]
    right = DUCKDB_COLUMNS[t["right"]]
    if t["op"] == "cross_above":
        return (f"(w > y AND p_w <= p_y)"), left, right
    if t["op"] == "cross_below":
        return (f"(w < y AND p_w >= p_y)"), left, right
    raise ValueError(f"{t['id']}: 不支持的 op {t['op']!r}")


@dataclass
class Stats:
    n: int = 0
    wins: int = 0
    rets: List[float] = field(default_factory=list)
    # 每条观测对应的触发日。与 rets 同序 —— 聚类稳健需要按它重新分组，
    # 因为调用方会传入**派生量**（胜率的 0/1 指示变量），而 clusters 里
    # 存的仍是原始收益。两套不对齐就会算出错的区间。
    dates: List[object] = field(default_factory=list)
    # 按触发日聚合的收益簇。事件研究里同一天全市场的金叉高度相关
    # （牛市一天能出几百个），把它们当独立 Bernoulli 会让区间严重偏窄。
    clusters: Dict[object, List[float]] = field(default_factory=dict)

    def add(self, r: float, date=None) -> None:
        self.n += 1
        self.rets.append(r)
        self.dates.append(date)
        if date is not None:
            self.clusters.setdefault(date, []).append(r)
        if r > 0:
            self.wins += 1

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    def mean(self) -> float:
        return sum(self.rets) / self.n if self.n else 0.0

    def median(self) -> float:
        if not self.rets:
            return 0.0
        s = sorted(self.rets)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2

    def wilson(self, z: float = Z95) -> Tuple[float, float]:
        if self.n == 0:
            return (0.0, 0.0)
        p, d_ = self.win_rate, 1 + z * z / self.n
        c = p + z * z / (2 * self.n)
        m = z * math.sqrt(p * (1 - p) / self.n + z * z / (4 * self.n * self.n))
        return ((c - m) / d_, (c + m) / d_)

    def boot_ci(self, iters: int = BOOTSTRAP, seed: int = 20261007
                ) -> Tuple[float, float]:
        if self.n < 2:
            return (self.mean(), self.mean())
        rnd = random.Random(seed)
        r, n = self.rets, self.n
        means = sorted(sum(r[rnd.randrange(n)] for _ in range(n)) / n
                       for _ in range(iters))
        return (means[int(0.025 * iters)], means[int(0.975 * iters)])

    def _grouped(self, vals: Sequence[float]) -> List[List[float]]:
        """
        按 self.dates 把 vals 重新分组。

        必须重新分组而不是直接用 self.clusters：调用方传进来的可能是
        **派生量**（胜率的 0/1 指示变量），而 clusters 里存的是原始收益。
        2026-10-07 的测试抓到过这个 bug —— 全胜样本本该得到零宽度区间
        （簇内方差为 0），却算出 [0.386, 1.0]。
        """
        if not self.dates:
            return [list(vals)]
        groups: Dict[object, List[float]] = {}
        for d, v in zip(self.dates, vals):
            groups.setdefault(d, []).append(v)
        return [g for g in groups.values() if g]

    def _cluster_robust(self, vals: Sequence[float], z: float = Z95):
        """
        聚类稳健（cluster-robust）标准误，**点估计与区间口径一致**。

        事件研究里同一天的触发高度相关（同一次市场共振出几百个信号），
        把 N 次触发当独立 Bernoulli 会严重低估不确定性。但也不能简单改成
        "日频簇均值 ± 其标准误" —— 那是另一个估计量（按日等权），点估计会
        和区间对不上。正确做法是对**同一个加权均值**求聚类稳健 SE：

            mean = Σ x_i / N
            Var(mean) = Σ_g ( Σ_{i∈g} (x_i − mean) )² / N²
            CI = mean ± z · sqrt(Var)

        `vals` 必须与 `self.dates` 同序（胜率传 0/1 指示变量，均值传原值）。
        返回 (mean, lo, hi, 簇数)。

        注意区间**不保证落在合法取值域内**（胜率可能 >1）。这是相对 Wilson
        的取舍：换来正确的相关性调整，丢掉 [0,1] 的观感。显示时不该因此
        假装区间被截断。
        """
        n = len(vals)
        if n == 0:
            return 0.0, 0.0, 0.0, 0
        mu = sum(vals) / n
        groups = self._grouped(vals)
        k = len(groups)
        if n < 2 or k == 0:
            return mu, mu, mu, k
        acc = 0.0
        for g in groups:
            s = sum(x - mu for x in g)
            acc += s * s
        se = math.sqrt(acc) / n
        return mu, mu - z * se, mu + z * se, k

    def cluster_mean_ci(self, z: float = Z95) -> Tuple[float, float, int]:
        _, lo, hi, k = self._cluster_robust(self.rets, z)
        return lo, hi, k

    def cluster_win_rate_ci(self, z: float = Z95
                            ) -> Tuple[float, float, float, int]:
        """同口径的聚类稳健胜率区间。"""
        mu, lo, hi, k = self._cluster_robust(
            [1.0 if r > 0 else 0.0 for r in self.rets], z)
        return mu, lo, hi, k


def pct(x: float) -> float:
    return 100.0 * x


def wilson_str(st: Stats) -> str:
    lo, hi = st.wilson()
    return f"{pct(st.win_rate):6.2f}% [{pct(lo):.2f}, {pct(hi):.2f}]"


ROWS_SQL = """
WITH cal AS (
  SELECT date, dense_rank() OVER (ORDER BY date) AS cidx
  FROM (SELECT DISTINCT date FROM mkt.v_daily_qfq)
),
base AS (
  SELECT i.thscode, i.date, cal.cidx,
         i.{left} AS w,
         i.{right} AS y,
         m.open, m.close
  FROM ind.v_indicators_daily i
  JOIN mkt.v_daily_qfq m USING (thscode, date)
  JOIN cal ON cal.date = i.date
  WHERE i.date <= DATE '{end}'
),
l AS (
  SELECT *, lag(close) OVER win AS prev_close,
            lag(w) OVER win AS p_w,
            lag(y) OVER win AS p_y
  FROM base WINDOW win AS (PARTITION BY thscode ORDER BY date)
),
f AS (
  SELECT thscode, date, cidx, prev_close, w, y, p_w, p_y,
         lead(open, 1)    OVER win AS open_1,
         lead(close, {n}) OVER win AS close_n,
         lead(cidx, {n})  OVER win AS cidx_n,
         lead(date, {n})  OVER win AS exit_date
  FROM l WINDOW win AS (PARTITION BY thscode ORDER BY date)
)
SELECT thscode, date, exit_date,
       CASE WHEN thscode LIKE '30%' OR thscode LIKE '68%'
              THEN open_1 >= prev_close * 1.20 - 0.005
            WHEN thscode LIKE '8%' OR thscode LIKE '4%'
              THEN open_1 >= prev_close * 1.30 - 0.005
            ELSE open_1 >= prev_close * 1.10 - 0.005 END AS seal_1,
       (cidx_n - cidx) AS gap,
       (close_n / open_1 - 1) AS r_abs
FROM f
WHERE {cond}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--end", default="2026-09-30")
    args = ap.parse_args()

    con = duckdb.connect()
    con.execute(f"ATTACH '{IND_DB}' AS ind (READ_ONLY)")
    con.execute(f"ATTACH '{MKT_DB}' AS mkt (READ_ONLY)")
    con.execute(f"ATTACH '{IDX_DB}' AS idx (READ_ONLY)")

    total = con.execute(
        "SELECT count(DISTINCT thscode) FROM ind.v_indicators_daily").fetchone()[0]
    span = con.execute(
        "SELECT min(date), max(date) FROM ind.v_indicators_daily").fetchone()
    stale = con.execute(f"""
        SELECT count(*) FROM (
          SELECT thscode, max(date) d FROM ind.v_indicators_daily
          WHERE date <= DATE '{args.end}' GROUP BY 1
        ) WHERE d < DATE '{args.end}' - INTERVAL 90 DAY
    """).fetchone()[0]
    n_first_q = con.execute(f"""
        SELECT count(DISTINCT thscode) FROM ind.v_indicators_daily
        WHERE date BETWEEN DATE '{span[0]}' AND DATE '{span[0]}' + INTERVAL 90 DAY
    """).fetchone()[0]
    b0, b1 = con.execute(
        f"SELECT min(trade_date), max(trade_date) FROM idx.v_index_daily "
        f"WHERE thscode='{BENCH}'").fetchone()

    print(f"数据集 {total} 只  {span[0]} → {span[1]}   基准日 {args.end}")
    # stale==0 不是"样本池干净"，恰恰相反：**没有一只票是因为退市而消失的**。
    if stale == 0:
        print(f"⚠ 幸存者偏差：{total} 只全部在基准日前 90 天内仍有行情，退市/长期"
              f"停牌证券 **0 只** —— 样本池是「当前在市」快照。")
        print(f"  起始 90 天在库仅 {n_first_q} 只 → {total} 只的增量全是 IPO，"
              f"没有任何一只因退市被剔除。")
        print(f"  ⇒ 胜率与均值 **系统性偏高**，结论仅供内部方向参考，"
              f"不可对外引用。")
    else:
        print(f"样本池含 {stale} 只已退市/长期停牌证券（最后行情距基准日 >90 天）")
    print(f"基准 {BENCH}：{b0} → {b1}  ← 超额口径仅此区间可用\n")

    bench = con.execute(
        f"SELECT trade_date, close FROM idx.v_index_daily WHERE thscode='{BENCH}' "
        f"ORDER BY trade_date").fetchall()
    bdates = [d for d, _ in bench]
    bclose = dict(bench)

    for n in args.horizons:
        st = {tid: {"abs": Stats(), "exc": Stats()} for tid in TRIGGERS}
        dr = {"suspend": 0, "seal": 0, "bench": 0}
        fired = {tid: 0 for tid in TRIGGERS}

        # 逐个触发跑：定义来自 yaml，SQL 由定义拼出来，不在本文件里重述一遍
        for tid in TRIGGERS:
            tdef = trigger_def(tid)
            cond, left, right = _sql_cross(tdef)
            rows = con.execute(ROWS_SQL.format(
                n=n, end=args.end, left=left, right=right, cond=cond)).fetchall()
            fired[tid] = len(rows)
            for _code, date, exit_date, seal_1, gap, r_abs in rows:
                if r_abs is None:
                    continue
                if gap != n:                   # 后验窗口跨停牌 → 剔除
                    dr["suspend"] += 1
                    continue
                if seal_1:                     # 次日一字涨停，买不进
                    dr["seal"] += 1
                    continue
                st[tid]["abs"].add(r_abs, date)
                k0 = bisect.bisect_left(bdates, date)
                k1 = bisect.bisect_left(bdates, exit_date)
                if k0 < len(bdates) and k1 < len(bdates):
                    d0, d1 = bclose[bdates[k0]], bclose[bdates[k1]]
                    if d0:
                        st[tid]["exc"].add(r_abs - (d1 / d0 - 1), date)
                else:
                    dr["bench"] += 1

        total_kept = sum(v["abs"].n for v in st.values())
        total_fired = sum(fired.values())
        print("=" * 92)
        print(f"持有 {n} 个交易日 · 进场=T+1 开盘可成交价 · 出场=T+1+{n-1} 收盘")
        print("=" * 92)
        print(f"触发 {total_fired} 次，可用 {total_kept} 次"
              f"（剔除 跨停牌 {dr['suspend']} / 一字涨停买不进 {dr['seal']}"
              f" / 超出基准区间 {dr['bench']}）")
        print(f"触发定义来源：config/indicators.yaml triggers:（schema "
              f"{SCHEMA_VERSION}），经 zettaranc.indicator_meta 生成")
        print("注：触发高度跨股票相关（同一天全市场共振），Wilson/朴素 bootstrap "
              "假设样本独立，会偏窄；\n    以「聚类稳健（按触发日聚类）」区间为准。\n")

        for t, label in TRIGGERS.items():
            a, e = st[t]["abs"], st[t]["exc"]
            print(f"  {label}  [触发 {fired[t]} 次]")
            for nm, s in (("绝对收益", a), ("超额 vs 沪深300", e)):
                if s.n < MIN_SAMPLES:
                    print(f"    {nm:18s} 不可判定（样本 {s.n} < {MIN_SAMPLES}）")
                    continue
                mu, clo, chi, k = s._cluster_robust(s.rets)
                vmu, vlo, vhi, _ = s._cluster_robust(
                    [1.0 if r > 0 else 0.0 for r in s.rets])
                wlo, whi = s.wilson()
                print(f"    {nm}")
                print(f"      N={s.n:<6d} 聚类数 {k} 个交易日"
                      f"（朴素区间假设独立，偏窄）")
                print(f"      胜率 {pct(vmu):6.2f}%  "
                      f"朴素Wilson [{pct(wlo):.2f}, {pct(whi):.2f}]  "
                      f"聚类稳健 [{pct(vlo):.2f}, {pct(vhi):.2f}]")
                print(f"      均值 {pct(mu):+6.2f}%  "
                      f"聚类稳健95%CI [{pct(clo):+.2f}, {pct(chi):+.2f}]  "
                      f"中位 {pct(s.median()):+6.2f}%")
            print()

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
