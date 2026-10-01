"""classify_signals.py — 用真实 DuckDB 复算信号触发频率（2026-10-01）

为什么不用 run_signal_audit.sh:
  那条路径打 python-service（:50052），需要容器在跑。这里直接读
  indicators.duckdb —— 审计要的就是"某条件在多少比例的 bar 上成立"，
  而那一批条件全部能用 SQL 表达，不需要绕 HTTP。

判据沿用 signal_frequency_audit.md 的三条:
  dead        0 次
  noisy       > 20% 的 bar
  informative 1% .. 20%

⚠️ **每条 SQL 必须与 signals.go 的实现逐条一致。**
   第一版这个脚本里的条件是凭理解重写的，结果 11 条里有 6 条量错了对象 ——
   最典型的是把「MACD动能衰减」（柱体连续 4 根同向收窄）写成 `hist > 0`
   （柱体为正），把「Aroon多头排列」（up>70 且 down<30）写成 `up > down`。
   量出来的频率驱动着 signals.go 的 noisySignalNames —— 也就是**哪些信号被
   默认折叠给模型**，判据错了就折叠错了对象。

   所以下方每条 SQL 后面的注释都标了 signals.go 的行号与原始条件。改
   signals.go 的判据时这里必须同步；改这里之前先读 signals.go。
"""
from __future__ import annotations

import sys

import duckdb

DB = "/Users/chenlei/.hithink-finance/indicators.duckdb"

# (信号名, SQL 布尔表达式, 方向, signals.go 出处)
SIGNALS: list[tuple[str, str, str, str]] = [
    # ---- 波动 ----
    # signals.go:360  avgWidth > 0 && latest.BBWidth < avgWidth*0.5
    # avgWidth 是**前 5 根**（rows[i] 往回，不含 latest 之外的部分由该实现决定），
    # 这里用 ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING 近似"不含当根的 5 根均值"。
    ("布林带收口",
     "(volatility_bbands_20_2_0_upper - volatility_bbands_20_2_0_lower) "
     "< 0.5 * bb_width_avg5_prev5", "neutral", "signals.go:361"),
    # signals.go:434  kcSqueezed(latest) && !kcSqueezed(prev) —— **首次进入**
    ("Keltner挤压",
     "kc_squeezed AND NOT prev_kc_squeezed", "neutral", "signals.go:434"),
    # signals.go:488  prev.NATR <= 5 && latest.NATR > 5 —— 升破
    ("NATR进入高波动档位",
     "prev_natr <= 5 AND volatility_natr_14 > 5", "neutral", "signals.go:488"),
    # signals.go:513  ATR 为 5 日均值的 1.5 倍以上，且是**首次**升破
    ("ATR扩张",
     "volatility_atr_14 > 1.5 * atr_avg5 AND prev_atr <= 1.5 * atr_avg5",
     "neutral", "signals.go:513"),

    # ---- 动量 ----
    # signals.go:70  h0*h1>0 && h1*h2>0 && h2*h3>0 && abs 逐根收窄
    #   h0=latest, h1=前一根, h2=前两根, h3=前三根
    ("MACD动能衰减",
     "h0*h1 > 0 AND h1*h2 > 0 AND h2*h3 > 0 "
     "AND abs(h0) < abs(h1) AND abs(h1) < abs(h2) AND abs(h2) < abs(h3)",
     "neutral", "signals.go:70"),
    # signals.go:144  RSI6 < 20 && (len<2 || rows[1].RSI6 <= latest.RSI6)
    ("RSI6超卖",
     "momentum_rsi_6 < 20 AND momentum_rsi_6 <= prev_rsi6", "bullish", "signals.go:144"),
    # signals.go:148  RSI6 > 80 && (len<2 || rows[1].RSI6 >= latest.RSI6)
    ("RSI6超买",
     "momentum_rsi_6 > 80 AND momentum_rsi_6 >= prev_rsi6", "bearish", "signals.go:148"),
    # signals.go:194  CCI < -100（<-200 只是 strength 升到 0.8，条件不变）
    ("CCI超卖", "momentum_cci_20 < -100", "bullish", "signals.go:194"),
    ("CCI超买", "momentum_cci_20 > 100", "bearish", "signals.go:201"),
    # signals.go:221  WillR < 0 && WillR < -80
    ("Williams%R超卖", "momentum_willr_14 < -80", "bullish", "signals.go:223"),
    ("Williams%R超买", "momentum_willr_14 > -20", "bearish", "signals.go:224"),
    # signals.go:233  MFI < 20 / > 80
    ("MFI超卖", "volume_mfi_14 < 20", "bullish", "signals.go:234"),
    ("MFI超买", "volume_mfi_14 > 80", "bearish", "signals.go:236"),
    # signals.go:83   KDJ 超卖金叉：prev.K<=prev.D && latest.K>latest.D && latest.K<20
    ("KDJ超卖金叉",
     "prev_k <= prev_d AND momentum_kdj_9_3_k > momentum_kdj_9_3_d "
     "AND momentum_kdj_9_3_k < 20", "bullish", "signals.go:83"),
    # signals.go:88   KDJ 超买死叉：prev.K>=prev.D && latest.K<latest.D && latest.K>80
    ("KDJ超买死叉",
     "prev_k >= prev_d AND momentum_kdj_9_3_k < momentum_kdj_9_3_d "
     "AND momentum_kdj_9_3_k > 80", "bearish", "signals.go:88"),

    # ---- 趋势 ----
    # signals.go:280  ADX > 25 时，DI+ > DI- 记多头，**否则**记空头
    #   注意空头分支是 else，不是 `DI+ < DI-` —— ADX>25 而两者相等时也记空头。
    ("ADX多头趋势",
     "trend_adx_14 > 25 AND momentum_dm_14_plus > momentum_dm_14_minus",
     "bullish", "signals.go:281"),
    ("ADX空头趋势",
     "trend_adx_14 > 25 AND NOT (momentum_dm_14_plus > momentum_dm_14_minus)",
     "bearish", "signals.go:286"),
    # signals.go:303  AroonUp > 70 && AroonDown < 30
    ("Aroon多头排列",
     "momentum_aroon_25_aroonup > 70 AND momentum_aroon_25_aroondown < 30",
     "bullish", "signals.go:303"),
    # signals.go:308  AroonDown > 70 && AroonUp < 30
    ("Aroon空头排列",
     "momentum_aroon_25_aroondown > 70 AND momentum_aroon_25_aroonup < 30",
     "bearish", "signals.go:309"),

    # ---- 量价 ----
    # signals.go:546  CMF > 0.1 / <-0.1
    ("CMF资金流入", "volume_cmf_20 > 0.1", "bullish", "signals.go:546"),
    ("CMF资金流出", "volume_cmf_20 < -0.1", "bearish", "signals.go:552"),

    # ---- 统计 ----
    # signals.go:618  ZScore < -2 / > 2
    ("Z-Score超卖", "statistics_zscore_20 < -2", "bullish", "signals.go:620"),
    ("Z-Score超买", "statistics_zscore_20 > 2", "bearish", "signals.go:625"),
    # signals.go:630  LinSlope > 0 / < 0
    ("线性回归上升", "statistics_linearreg_slope_14 > 0", "bullish", "signals.go:630"),
    ("线性回归下降", "statistics_linearreg_slope_14 < 0", "bearish", "signals.go:635"),
]

# Donchian 突破类信号量不了：它们的判据要 close，而 indicators 表里**没有** close 列
# （close 在 market.duckdb，两个库是独立只读连接不能 join）。要量得走
# run_signal_audit.sh 那条 fetch_market_data 路径。不猜阈值。
UNMEASURABLE = {
    "布林中轨收复": "判据是 prev.Close <= prev.BBMid && latest.Close > BBMid，需要 close 列",
    "Donchian上轨突破": "判据是 latest.Close > DCUpper，需要 close 列",
    "Donchian下轨跌破": "判据是 latest.Close < DCLower，需要 close 列",
    "Keltner挤压向上突破": "判据需要 close 与前一根 KC 上轨的比较（signals.go:441）",
    "Keltner挤压向下突破": "判据需要 close 与前一根 KC 下轨的比较（signals.go:447）",
}


def build_view(con: duckdb, clause: str) -> None:
    """建采样视图，把 signals.go 需要的 lag/均值/布尔列一次算好。"""
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW s AS
        SELECT
          thscode, date,          -- 分区键：s2 的 lag 要用
          -- signals.go 里的 latest / prev / prev2 / prev3
          momentum_macd_12_26_9_hist                                   AS h0,
          lag(momentum_macd_12_26_9_hist, 1) OVER w                    AS h1,
          lag(momentum_macd_12_26_9_hist, 2) OVER w                    AS h2,
          lag(momentum_macd_12_26_9_hist, 3) OVER w                    AS h3,
          lag(momentum_rsi_6, 1) OVER w                                 AS prev_rsi6,
          lag(momentum_kdj_9_3_k, 1) OVER w                             AS prev_k,
          lag(momentum_kdj_9_3_d, 1) OVER w                             AS prev_d,
          lag(volatility_natr_14, 1) OVER w                             AS prev_natr,
          lag(volatility_atr_14, 1) OVER w                              AS prev_atr,
          lag(volatility_kc_20_2_upper, 1) OVER w                       AS prev_kc_upper,
          lag(volatility_kc_20_2_lower, 1) OVER w                       AS prev_kc_lower,
          -- 布林带宽的前 5 根均值（不含当根），对应 signals.go:357-360
          avg(volatility_bbands_20_2_0_upper - volatility_bbands_20_2_0_lower)
            OVER (PARTITION BY thscode ORDER BY date
                  ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)            AS bb_width_avg5_prev5,
          avg(volatility_atr_14)
            OVER (PARTITION BY thscode ORDER BY date
                  ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)            AS atr_avg5,
          -- kcSqueezed 的布尔本体（signals.go kcSqueezed 函数）
          (volatility_bbands_20_2_0_upper < volatility_kc_20_2_upper
           AND volatility_bbands_20_2_0_lower > volatility_kc_20_2_lower) AS kc_squeezed,
          momentum_macd_12_26_9_hist, momentum_rsi_6, momentum_cci_20,
          momentum_willr_14, volume_mfi_14,
          momentum_kdj_9_3_k, momentum_kdj_9_3_d,
          trend_adx_14, momentum_dm_14_plus, momentum_dm_14_minus,
          momentum_aroon_25_aroonup, momentum_aroon_25_aroondown,
          volume_cmf_20, statistics_zscore_20, statistics_linearreg_slope_14,
          volatility_natr_14, volatility_atr_14,
          volatility_bbands_20_2_0_upper, volatility_bbands_20_2_0_middle,
          volatility_bbands_20_2_0_lower,
          volatility_kc_20_2_upper, volatility_kc_20_2_lower
        FROM v_indicators_daily
        WHERE thscode IN ({clause}) AND date >= DATE '2022-08-01'
        WINDOW w AS (PARTITION BY thscode ORDER BY date)
    """)
    # 上一根的 kc_squeezed 与前两根的 KC 轨，单独补一列
    con.execute("""
        CREATE OR REPLACE TEMP VIEW s2 AS
        SELECT s.*, lag(kc_squeezed) OVER w2 AS prev_kc_squeezed
        FROM s
        WINDOW w2 AS (PARTITION BY thscode ORDER BY date)
    """)


def main() -> int:
    con = duckdb.connect(DB, read_only=True)
    total = con.execute(
        "SELECT count(DISTINCT thscode) FROM v_indicators_daily").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT thscode FROM v_indicators_daily ORDER BY thscode").fetchall()]
    sampled = codes[::max(1, len(codes) // 200)][:200]
    clause = ",".join(f"'{c}'" for c in sampled)
    build_view(con, clause)

    evaluated = con.execute("SELECT count(*) FROM s2").fetchone()[0]
    print(f"抽样 {len(sampled)} / {total} 只 | 评估 bar {evaluated:,} | 2022-08-01 → 今\n")

    print("| 信号 | 命中 | 频率 | 方向 | verdict | 出处 |")
    print("|---|---:|---:|---|---|---|")
    rows, dead, noisy, informative = [], [], [], []
    for name, expr, direction, src in SIGNALS:
        try:
            hits = con.execute(f"SELECT count(*) FROM s2 WHERE {expr}").fetchone()[0]
        except duckdb.Error as e:
            print(f"  [ERR] {name}: {str(e).splitlines()[0]}")
            continue
        freq = hits / evaluated * 100 if evaluated else 0.0
        verdict = "dead" if hits == 0 else ("noisy" if freq > 20.0 else "informative")
        rows.append((name, hits, freq, direction, verdict, src))
        {"dead": dead, "noisy": noisy, "informative": informative}[verdict].append(
            (name, freq))
    for name, hits, freq, d, v, src in sorted(rows, key=lambda r: -r[2]):
        print(f"| {name} | {hits:,} | {freq:.4f}% | {d} | `{v}` | {src} |")

    print(f"\ndead {len(dead)} / noisy {len(noisy)} / informative {len(informative)}")
    print(f"\n量不了的（{len(UNMEASURABLE)}）：")
    for n, why in UNMEASURABLE.items():
        print(f"  - {n}: {why}")
    print(f"\n### noisy 名单（同步到 signals.go 的 noisySignalNames）\n")
    for n, f in sorted(noisy, key=lambda x: -x[1]):
        print(f'  "{n}",'.ljust(34) + f"  // {f:.2f}%")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
