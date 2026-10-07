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
from zettaranc.frontend_formulas import FORMULAS  # noqa: E402
from zettaranc.indicator_meta import (  # noqa: E402
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


# ---------------------------------------------------------------------------
# 多重比较校正
#
# 为什么要做：一次跑下来有「触发数 × 周期数 × 口径数」个假设
# （当前 6 触发 × 3 周期 × 2 口径 = 36 个）。在 5% 水平上纯靠运气就能出
# 1~2 个"显著"，而且这些触发高度相关（都是同一批 K 线上的均线交叉），
# 有效检验数远小于 36 —— 不校正的话，测得越多，假显著越多。
#
# 用 Benjamini-Hochberg 控制 FDR（错误发现率），不是 Bonferroni 校正 FWER：
# 我们真正在意的是"报告为有效的结论里有多少是错的"，而不是"能不能一个都
# 不漏"。BH 在相关假设下更不保守，也更符合这份报告的用途。
# ---------------------------------------------------------------------------

RESULTS: List[dict] = []


def two_sided_p(mean: float, se: float) -> float:
    """
    均值是否显著异于 0 的双侧 p 值（正态近似）。

    这里用正态近似而不是 t 分布：聚类数是 ~2000 个交易日，t 与正态在这个
    自由度下差别在第三位，而正态近似省掉一张表且不会因为自由度算错而给出
    离谱的 p 值。
    """
    if se <= 0:
        return 1.0 if abs(mean) < 1e-15 else 0.0
    z = abs(mean / se)
    return math.erfc(z / math.sqrt(2.0))


def apply_bh(results: List[dict]) -> None:
    """给每条结果算 p 值并做 BH 校正。"""
    for r in results:
        lo, hi = r["mean_ci"]
        se = (hi - lo) / (2 * Z95)
        r["se"] = se
        r["p"] = two_sided_p(r["mean"], se)
    m = len(results)
    order = sorted(range(m), key=lambda i: results[i]["p"])
    # BH：p_(i) 按升序，阈值 i/m * alpha，取最后一个不超阈值的秩
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        adj = min(prev, results[i]["p"] * m / rank)
        results[i]["p_bh"] = adj
        prev = adj


def print_bh(results: List[dict]) -> None:
    if not results:
        return
    print("=" * 96)
    print(f"多重比较校正（Benjamini-Hochberg，共 {len(results)} 个假设）")
    print("=" * 96)
    print("  假设是「触发后的超额/绝对收益均值 = 0」。BH 控的是 FDR。")
    print("  未经校正的 p 在相关假设下会成批造假显著 —— 这些触发都跑在同一批")
    print("  K 线的均线交叉上，有效独立检验数远少于假设个数。\n")
    print(f"  {'触发':<10s} {'周期':>4s} {'口径':<12s} {'均值':>8s} "
          f"{'原始 p':>10s} {'BH 校正 p':>10s}  判定")
    sig = 0
    for r in sorted(results, key=lambda x: x["p"]):
        keep = r["p_bh"] <= 0.05
        sig += keep
        verdict = "显著" if keep else "不显著"
        if r["mean"] < 0 and keep:
            verdict = "显著(反向)"
        print(f"  {r['trigger']:<10s} {r['horizon']:>4d} {r['kind']:<12s} "
              f"{pct(r['mean']):>+7.2f}% {r['p']:>10.4f} {r['p_bh']:>10.4f}  {verdict}")
    print(f"\n  BH 后仍显著：{sig} / {len(results)}")
    if sig == 0:
        print("  ⇒ **没有任何一个假设在 FDR 5% 下站得住。** 这不是脚本坏了，"
              "这正是第一刀结论的正式表述。")
    print()


def attach_all(con, timeout: int = 600) -> set:
    """
    挂上三个库，遇到锁就等。

    为什么要等：`~/.hithink-finance` 下这些库由 launchd 的定时同步持有
    （daily-sync 工作日 17:30 / indicators-sync 19:00 / night-sync 03:00）。
    DuckDB 的写锁是独占的，评估是只读方，抢不过同步 —— 直接抛 IOException
    只会让人以为脚本坏了。

    **返回实际挂上的库名集合**，而不是无条件成功：index.duckdb 常年被
    daily-sync 占着（本机实测单次持锁可超过 30 分钟），而它只用来取基准。
    为一个 1223 行的基准序列干等 30 分钟不划算，所以它降级到本地缓存。
    """
    import time
    attached = set()
    for name, path in (("ind", IND_DB), ("mkt", MKT_DB), ("idx", IDX_DB)):
        waited, delay = 0, 5.0
        while True:
            try:
                con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
                attached.add(name)
                break
            except duckdb.IOException as e:
                if "lock" not in str(e).lower() or waited >= timeout:
                    if name == "idx":
                        print(f"  {path.split('/')[-1]} 仍被持锁，改用本地基准缓存",
                              flush=True)
                        break
                    raise
                if waited == 0:
                    print(f"  {path.split('/')[-1]} 被同步任务持锁，等待中…",
                          flush=True)
                time.sleep(delay)
                waited += delay
                delay = min(delay * 1.5, 30.0)
    return attached


BENCH_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "bench_000300.csv")


def load_benchmark(con, attached: set):
    """
    取基准序列，优先读库，失败读缓存，读到就顺手刷新缓存。

    缓存存在的理由不是性能而是**可用性**：daily-sync 每天 17:30 占着
    index.duckdb 且单次可能持锁半小时以上，而基准只有 1223 个交易日
    （一年多）的收盘价 —— 它每天都变，但变得很慢，值得缓存。
    """
    import csv
    import os as _os
    _os.makedirs(_os.path.dirname(BENCH_CACHE), exist_ok=True)
    if "idx" in attached:
        rows = con.execute(
            f"SELECT trade_date, close FROM idx.v_index_daily "
            f"WHERE thscode='{BENCH}' ORDER BY trade_date").fetchall()
        if rows:
            with open(BENCH_CACHE, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["trade_date", "close"])
                w.writerows(rows)
            return [d for d, _ in rows], dict(rows), "库"
    if _os.path.exists(BENCH_CACHE):
        rows = []
        with open(BENCH_CACHE) as f:
            for rec in csv.DictReader(f):
                rows.append((rec["trade_date"], float(rec["close"])))
        return [d for d, _ in rows], dict(rows), "本地缓存"
    return [], {}, "无"


# 等权全市场基准：把样本池里每只票的日收益取均值再连乘。它**不是**沪深300，
# 报告里必须换个名字 —— 它衡量的是"这个样本池等权之后涨了多少"，与沪深300
# 的市值加权口径不可混用。之所以要有这条通路：index.duckdb 被 daily-sync
# 独占的时段可能超过半小时，而评估不该因为取不到基准就整个跑不了。
EQUITY_BENCH_SQL = """
SELECT m.date, avg(r) AS mr
FROM (
  SELECT m.date,
         m.close / lag(m.close) OVER (PARTITION BY m.thscode ORDER BY m.date) - 1 AS r
  FROM mkt.v_daily_qfq m
  WHERE m.date <= DATE '{end}' AND m.close IS NOT NULL AND m.close > 0
) m
WHERE m.r IS NOT NULL
GROUP BY m.date
ORDER BY m.date
"""


def _equal_weight_bench(con, end: str):
    import math as _math
    rows = con.execute(EQUITY_BENCH_SQL.format(end=end)).fetchall()
    dates, level = [], 1000.0
    out = []
    for d, mr in rows:
        level *= (1.0 + float(mr))
        dates.append(d)
        out.append((d, level))
    return dates, dict(out)


def pct(x: float) -> float:
    return 100.0 * x


def wilson_str(st: Stats) -> str:
    lo, hi = st.wilson()
    return f"{pct(st.win_rate):6.2f}% [{pct(lo):.2f}, {pct(hi):.2f}]"


# 前视收益框架：一次 SQL 算完所有标的的"如果在这天进场、持有 n 天会怎样"，
# 序列（left/right）不管来自库列还是前端公式，都不在这条 SQL 里。
#
# 为什么拆成两段：compute=frontend 的触发**不能**读 v_indicators_daily ——
# 实测那里的 MACD 与工作台 MACD 的跨柱位置只重合 60.7%，KDJ 差 3%（见
# zettaranc/frontend_formulas.py 模块头）。把序列放到 Python 里按来源分别取，
# 判定逻辑就只剩一份，两个来源不会各写一套交叉判定而悄悄分叉。
FRAME_SQL = """
WITH cal AS (
  SELECT date, dense_rank() OVER (ORDER BY date) AS cidx
  FROM (SELECT DISTINCT date FROM mkt.v_daily_qfq)
),
j AS (
  SELECT m.thscode, m.date AS date, cal.cidx, m.open, m.high, m.low, m.close,
         i.zettaranc_zg_white_10 AS ztr_white,
         i.zettaranc_dg_yellow_14 AS ztr_yellow,
         i.zettaranc_bbi AS ztr_bbi
  FROM mkt.v_daily_qfq m
  JOIN cal ON cal.date = m.date
  LEFT JOIN ind.v_indicators_daily i
    ON i.thscode = m.thscode AND i.date = m.date
  WHERE m.date <= DATE '{end}'
)
SELECT thscode, date, cidx, open, high, low, close,
       ztr_white, ztr_yellow, ztr_bbi,
       lag(close) OVER win AS prev_close,
       lead(open, 1)    OVER win AS open_1,
       lead(close, {n}) OVER win AS close_n,
       lead(cidx, {n})  OVER win AS cidx_n,
       lead(date, {n})  OVER win AS exit_date
FROM j WINDOW win AS (PARTITION BY thscode ORDER BY date)
ORDER BY thscode, date
"""

# 跌停/一字涨停判定用：次日开盘相对前收的幅度。ST 的 ±5% 特例缺失（库里没有
# 证券简称），这是个已知盲区，会让 ST 股的"买不进"漏判、样本略偏乐观。
def _seal_flag(thscode: str, prev_close, open_1) -> Optional[bool]:
    if prev_close is None or open_1 is None or prev_close == 0:
        return None
    pct = "30" if (thscode.startswith("30") or thscode.startswith("68")) else \
          "40" if (thscode.startswith("8") or thscode.startswith("4")) else "10"
    return open_1 >= prev_close * (1.0 + int(pct) / 100.0) - 0.005


def _cross_mask(left, right, above: bool) -> List[bool]:
    """
    跨柱判定：只看当根与前一根。

    形状固定且**唯一**——op 只有 cross_above / cross_below 两种，两者互为
    镜像。front/duckdb 两种数据来源共用这一个函数，就是为了让"库列算出来的
    跨柱"和"现算出来的跨柱"永远按同一条规则判定。
    """
    n = len(left)
    out = [False] * n
    for i in range(1, n):
        a, b, pa, pb = left[i], right[i], left[i - 1], right[i - 1]
        if None in (a, b, pa, pb):
            continue
        if above and pa <= pb and a > b:
            out[i] = True
        elif (not above) and pa >= pb and a < b:
            out[i] = True
    return out


def _compute_frontend_series(name: str, bars: dict) -> dict:
    """按 frontend_formulas.FORMULAS 的注册表现算一组序列。"""
    spec = FORMULAS[name]
    needs = spec["needs"]
    kwargs = {}
    if "low" in needs:
        kwargs["lows"] = bars["low"]
    if "high" in needs:
        kwargs["highs"] = bars["high"]
    params = bars.get("_params_" + name)
    if params:
        kwargs.update(params)
    return spec["fn"](bars["close"], **kwargs)


def _duckdb_series(tdef: dict, bars: dict) -> Tuple[list, list]:
    """
    走库列的触发。

    **只允许已经被证明逐位一致的列。** config/indicators.yaml 里
    compute: duckdb 的触发目前只有白线/黄线，它们由
    compare_zettaranc_columns.py 证明过（黄线 100.00%、白线 99.9868%）。
    库里的 MACD / KDJ 不满足这个条件 —— 所以那些触发标了 compute: frontend。
    """
    left = bars["_col_" + tdef["left"]]
    right = bars["_col_" + tdef["right"]]
    return left, right


def _series_for(tdef: dict, bars: dict) -> Tuple[list, list]:
    """
    取一条触发需要的 left / right 两条序列，按 compute 决定来源。

    duckdb    —— 取已证明逐位一致的库列（当前只有 zettaranc 三列）
    frontend  —— 用 frontend_formulas 的逐字移植现算
    """
    comp = tdef.get("compute") or "duckdb"
    if comp == "duckdb":
        return _duckdb_series(tdef, bars)
    name_l, field_l = tdef["left"].split(".", 1)
    name_r, field_r = tdef["right"].split(".", 1)
    computed = _compute_frontend_series(name_l, bars)
    left = computed[field_l]
    if name_r == name_l:
        return left, computed[field_r]
    computed_r = _compute_frontend_series(name_r, bars)
    return left, computed_r[field_r]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--end", default="2026-09-30")
    args = ap.parse_args()

    con = duckdb.connect()
    attached = attach_all(con)

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

    bdates, bclose, bsrc = load_benchmark(con, attached)
    if not bdates and "mkt" in attached:
        bdates, bclose, bsrc = _equal_weight_bench(con, args.end)
        print("⚠ 沪深300 不可得（index.duckdb 被 daily-sync 持锁），"
              "本次超额口径改用 **等权全市场** 基准（口径不同，不可与"
              "沪深300 结果混读）")
    if not bdates:
        print("⚠ 基准不可得：本次只出绝对收益，**不出超额口径**。\n")
    else:
        print(f"基准 {BENCH}：{bdates[0]} → {bdates[-1]}（来源：{bsrc}）\n")

    for n in args.horizons:
        st = {tid: {"abs": Stats(), "exc": Stats()} for t_ in [0]
              for tid in TRIGGERS}
        dr = {"suspend": 0, "seal": 0, "bench": 0, "nofwd": 0}
        fired = {tid: 0 for tid in TRIGGERS}

        # 一次 SQL 取回全部标的的前视收益框架，按 thscode 顺序流式消费。
        # 序列（left/right）在 Python 里按来源取：库列走 _duckdb_series，
        # 前端公式走 _compute_frontend_series。两种来源共用 _cross_mask。
        cur = con.execute(FRAME_SQL.format(n=n, end=args.end))
        col_names = [d[0] for d in cur.description]
        cur_code = None
        batch: list = []

        def flush(rows: list) -> None:
            """处理一只标的的全部 bar：算序列 → 判跨柱 → 记账。"""
            if not rows:
                return
            nb = len(rows)
            bars = {c: [r[i] for r in rows] for i, c in enumerate(col_names)
                    if c in ("open", "high", "low", "close",
                             "ztr_white", "ztr_yellow", "ztr_bbi")}
            bars["_col_ztr_white"] = bars.get("ztr_white")
            bars["_col_ztr_yellow"] = bars.get("ztr_yellow")
            for tdef_id, tdef in ((t, trigger_def(t)) for t in TRIGGERS):
                try:
                    p = tdef.get("params") or []
                    if tdef["left"].startswith("macd.") and len(p) >= 3:
                        bars["_params_macd"] = dict(
                            short=p[0], long=p[1], signal=p[2])
                    if tdef["left"].startswith("kdj.") and len(p) >= 3:
                        bars["_params_kdj"] = dict(
                            n=p[0], k_smooth=p[1], d_smooth=p[2])
                    left, right = _series_for(tdef, bars)
                except (KeyError, TypeError) as e:
                    raise SystemExit(
                        f"触发 {tdef_id} 取序列失败: {e}\n"
                        f"若新增了公式，记得同时更新 internal/indicators/meta.go 的 "
                        f"FrontendFormulaFields 与 zettaranc/frontend_formulas.py 的 FORMULAS")
                mask = _cross_mask(left, right, tdef["op"] == "cross_above")
                fired[tdef_id] += sum(mask)
                for i, hit in enumerate(mask):
                    if not hit:
                        continue
                    r_open = rows[i][col_names.index("open_1")]
                    r_close = rows[i][col_names.index("close_n")]
                    cidx_n = rows[i][col_names.index("cidx_n")]
                    # 末尾几根：lead(cidx, n) 越过股票最后一行 → None，
                    # 那不是"跨停牌"，是根本没有前视窗口。两种要分开计数。
                    if r_open is None or r_close is None or cidx_n is None:
                        dr["nofwd"] += 1
                        continue
                    gap = cidx_n - rows[i][2]
                    if gap != n:                # 后验窗口跨停牌 → 剔除
                        dr["suspend"] += 1
                        continue
                    if _seal_flag(rows[0][0],
                                  rows[i][col_names.index("prev_close")],
                                  r_open):
                        dr["seal"] += 1       # 次日一字涨停，买不进
                        continue
                    date = rows[i][1]
                    exit_date = rows[i][col_names.index("exit_date")]
                    r_abs = r_close / r_open - 1
                    st[tdef_id]["abs"].add(r_abs, date)
                    k0 = bisect.bisect_left(bdates, date)
                    k1 = bisect.bisect_left(bdates, exit_date)
                    if k0 < len(bdates) and k1 < len(bdates):
                        d0, d1 = bclose[bdates[k0]], bclose[bdates[k1]]
                        if d0:
                            st[tdef_id]["exc"].add(
                                r_abs - (d1 / d0 - 1), date)
                    else:
                        dr["bench"] += 1

        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            for row in chunk:
                code = row[0]
                if code != cur_code:
                    if batch:
                        flush(batch)
                    cur_code, batch = code, []
                batch.append(row)
        if batch:
            flush(batch)

        total_kept = sum(v["abs"].n for v in st.values())
        total_fired = sum(fired.values())
        print("=" * 96)
        print(f"持有 {n} 个交易日 · 进场=T+1 开盘可成交价 · 出场=T+1+{n-1} 收盘")
        print("=" * 96)
        print(f"触发 {total_fired} 次，可用 {total_kept} 次"
              f"（剔除 跨停牌 {dr['suspend']} / 一字涨停买不进 {dr['seal']}"
              f" / 超出基准区间 {dr['bench']} / 无前视窗口 {dr['nofwd']}）")
        print(f"触发定义来源：config/indicators.yaml triggers:（schema "
              f"{SCHEMA_VERSION}），经 zettaranc.indicator_meta 生成")
        print("注：触发高度跨股票相关（同一天全市场共振），区间一律按"
              "「按触发日聚类」的稳健标准误给出。\n")

        for t, label in TRIGGERS.items():
            a, e = st[t]["abs"], st[t]["exc"]
            src = trigger_def(t).get("compute") or "duckdb"
            print(f"  {label}  [触发 {fired[t]} 次 · 序列来源 {src}]")
            for nm, s_ in (("绝对收益", a), ("超额 vs 沪深300", e)):
                if s_.n < MIN_SAMPLES:
                    print(f"    {nm:18s} 不可判定（样本 {s_.n} < {MIN_SAMPLES}）")
                    continue
                mu, clo, chi, k = s_._cluster_robust(s_.rets)
                vmu, vlo, vhi, _ = s_._cluster_robust(
                    [1.0 if r > 0 else 0.0 for r in s_.rets])
                RESULTS.append({
                    "trigger": t, "label": label, "horizon": n, "kind": nm,
                    "n": s_.n, "clusters": k, "mean": mu,
                    "mean_ci": (clo, chi), "win_rate": vmu,
                    "win_ci": (vlo, vhi), "median": s_.median(), "src": src,
                })
                print(f"    {nm}")
                print(f"      N={s_.n:<6d} 聚类数 {k} 个交易日")
                print(f"      胜率 {pct(vmu):6.2f}%  聚类稳健 [{pct(vlo):.2f}, {pct(vhi):.2f}]")
                print(f"      均值 {pct(mu):+6.2f}%  聚类稳健95%CI "
                      f"[{pct(clo):+.2f}, {pct(chi):+.2f}]  中位 {pct(s_.median()):+6.2f}%")
            print()

    apply_bh(RESULTS)
    print_bh(RESULTS)

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
