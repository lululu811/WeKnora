"""
数据加载器 — 从 DuckDB 加载 OHLCV 和技术指标数据

对应 Go: internal/agent/tools/hithink_finance/analysis/data.go

两条硬约束（勿破坏）
--------------------
1. `thscode` 永远走 DuckDB 绑定参数（`?`），绝不拼进 SQL 字符串。
   曾经用 f-string 拼 `WHERE thscode = '{thscode}'`，导致
   `/zettaranc/analyze` 变成注入点（BUG-8）。
2. SELECT 里不写 `COALESCE(col, 0)`。缺失就是缺失，由 Python 侧保留成
   `None`；把 NULL 变成 0 会让"没有指标"被读成"RSI6=0 → 超卖"（BUG-4）。

行序：`ORDER BY date DESC` ⇒ 返回的 rows[0] 是最新一根 K 线。
"""

import logging
from typing import Any, Dict, List

from .utils import num, to_str

logger = logging.getLogger(__name__)

MIN_DAYS = 10
MAX_DAYS = 250
MAX_SCAN_DAYS = 60

# ---- 别名 -> DuckDB 列表达式 ----------------------------------------------
# 分析层用的是短别名（dif/k/rsi6…），底层列名是 momentum_macd_12_26_9_macd
# 这种长名。映射集中在这里，SQL 由它生成，不要在别处硬编码列名。
MARKET_COLUMNS: Dict[str, str] = {
    "date": "CAST(date AS VARCHAR)",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
}

INDICATOR_COLUMNS: Dict[str, str] = {
    "date": "CAST(date AS VARCHAR)",
    # 均线
    "ma5": "overlap_sma_5",
    "ma10": "overlap_sma_10",
    "ma20": "overlap_sma_20",
    "ma60": "overlap_sma_60",
    "ma120": "overlap_sma_120",
    "ma250": "overlap_sma_250",
    # MACD
    "dif": "momentum_macd_12_26_9_macd",
    "dea": "momentum_macd_12_26_9_signal",
    "macd_hist": "momentum_macd_12_26_9_hist",
    # 摆动类
    "k": "momentum_kdj_9_3_k",
    "d": "momentum_kdj_9_3_d",
    "j": "momentum_kdj_9_3_j",
    "rsi6": "momentum_rsi_6",
    "rsi14": "momentum_rsi_14",
    "stoch_k": "momentum_stoch_14_3_3_slowk",
    "stoch_d": "momentum_stoch_14_3_3_slowd",
    "cci": "momentum_cci_20",
    "willr": "momentum_willr_14",
    "mfi": "volume_mfi_14",
    # 趋势
    "adx": "trend_adx_14",
    "di_plus": "momentum_dm_14_plus",
    "di_minus": "momentum_dm_14_minus",
    "st_dir": "trend_supertrend_10_3_0_direction",
    "st_val": "trend_supertrend_10_3_0_trend",
    "psar": "trend_psar",
    "aroon_up": "momentum_aroon_25_aroonup",
    "aroon_down": "momentum_aroon_25_aroondown",
    "vi_plus": "trend_vortex_14_plus",
    "vi_minus": "trend_vortex_14_minus",
    "lin_slope": "statistics_linearreg_slope_14",
    # 波动
    "bb_upper": "volatility_bbands_20_2_0_upper",
    "bb_mid": "volatility_bbands_20_2_0_middle",
    "bb_lower": "volatility_bbands_20_2_0_lower",
    "bb_width": "(volatility_bbands_20_2_0_upper - volatility_bbands_20_2_0_lower)",
    "atr": "volatility_atr_14",
    "dc_upper": "volatility_donchian_20_upper",
    "dc_lower": "volatility_donchian_20_lower",
    "kc_upper": "volatility_kc_20_2_upper",
    "kc_mid": "volatility_kc_20_2_middle",
    "kc_lower": "volatility_kc_20_2_lower",
    "zscore": "statistics_zscore_20",
    # 量能
    "cmf": "volume_cmf_20",
    "obv": "volume_obv",
    "vwap": "volume_vwap",
    # 蜡烛
    "cdl_hammer": "candles_cdl_hammer_0",
    "cdl_shooting_star": "candles_cdl_shootingstar_0",
    "cdl_doji": "candles_cdl_doji_0",
    "cdl_engulfing": "candles_cdl_engulfing_0",
    "cdl_harami": "candles_cdl_harami_0",
    "cdl_morning_star": "candles_cdl_morningstar_0",
    "cdl_evening_star": "candles_cdl_eveningstar_0",
    "cdl_piercing": "candles_cdl_piercing_0",
    "cdl_dark_cloud": "candles_cdl_darkcloudcover_0",
    "cdl_3white": "candles_cdl_3whitesoldiers_0",
    "cdl_3black": "candles_cdl_3blackcrows_0",

    # ------------------------------------------------------------------
    # zettaranc 自研列(2026-10-01 修复后接入)
    #
    # 曾被隔离:2026-10-01 首次接入时实测发现数据不可用(99.6% 常数 0、
    # 值域 [-100,100] 与 yaml 声称的价格量纲矛盾)。根因在
    # a-stock/scripts/add_zettaranc_columns.py,与本文件无关:
    #   1. `import pandas_ta_classic` 写在 sys.path.insert 之前,而该库是
    #      仓库内本地目录(目录名带连字符),脚本**从未成功运行过**;
    #   2. 砖型图算的是 (C-O)/(H-L),与工作台的通达信 ZX 砖型不是同一指标;
    #   3. RSL 旧列名 *_short_3 / *_long_21 里的 3/21 是 pct_change 回看天数,
    #      不是窗口长度,读起来像涨跌幅。
    # 三者已全部修复并全量回填(1035 万行,6 列 100% 非空 / 0% 零值),
    # 详见 ~/.hithink-finance/INDICATORS_DB.md。回填脚本内置校验闸门,
    # 复验用 `python3 scripts/add_zettaranc_columns.py --verify-only`。
    #
    # RSL 列名已改为 *_rank_15 / *_rank_105:它是**滚动窗口百分位排名**
    # (值域 [0,100]),不是涨跌幅。注意与工作台前端那条同名不同义 ——
    # 前端 indicators.ts 的 Z_RSL 画的是 % 涨跌幅,见 config/indicators.yaml。
    # ------------------------------------------------------------------
    "ztr_white": "zettaranc_zg_white_10",      # 白线 DEMA = EMA(EMA(C,10),10)，价格量纲
    "ztr_yellow": "zettaranc_dg_yellow_14",    # 黄线 (MA14+MA28+MA57+MA114)/4，价格量纲
    "ztr_bbi": "zettaranc_bbi",                # 牵牛绳 (MA3+MA6+MA12+MA24)/4，价格量纲
    "ztr_brick": "zettaranc_brick_value",      # 知行 ZX 砖型（通达信口径），值域 >= 0
    "ztr_rsl_rank_15": "zettaranc_rsl_rank_15",    # 3日涨幅在15窗口的百分位 [0,100]
    "ztr_rsl_rank_105": "zettaranc_rsl_rank_105",  # 21日涨幅在105窗口的百分位 [0,100]

    # ------------------------------------------------------------------
    # 2026-09-30 那批 33 列里**实测正交**的部分(2026-10-01 接入)
    #
    # 准入依据是全市场截面相关性(2025-10 至今, 6 开头, 与**已在消费**的 42 列比):
    # 只接 corr < 0.45 的。被拒的见文件末尾 REJECTED 一节,别把它们捡回来。
    # ------------------------------------------------------------------
    "stc": "momentum_stc_line",                # vs macd 0.222
    "stc_macd": "momentum_stc_macd",
    "stc_stoch": "momentum_stc_stoch",         # vs slowk 0.432
    "vosc": "volume_vosc_5_10",                # vs adosc 0.130 / vs obv 0.000
    "ui": "statistics_ui_14",                  # vs atr 0.195 / vs stddev 0.228
    "coppock": "momentum_coppock_10_10_14",    # vs macd_hist 0.335
    # 风险维度:已有列里没有等价物
    "dd_abs": "performance_drawdown_20_dd",
    "dd_frac": "performance_drawdown_20_frac",  # 0~1 比例,不是百分数
    "dd_log": "performance_drawdown_20_log",
}

# 分析层（趋势/形态/支撑阻力）用到的字段
MARKET_FIELDS: List[str] = list(MARKET_COLUMNS)
INDICATORS_FIELDS: List[str] = [
    "ma5", "ma10", "ma20", "ma60", "ma120", "ma250",
    "dif", "dea", "macd_hist", "rsi6", "rsi14",
    "adx", "di_plus", "di_minus", "st_dir", "st_val",
    "bb_upper", "bb_mid", "bb_lower", "atr",
    "cmf", "mfi", "obv", "vwap",
    "aroon_up", "aroon_down", "lin_slope", "zscore",
    "cdl_hammer", "cdl_shooting_star", "cdl_doji", "cdl_engulfing",
    "cdl_harami", "cdl_morning_star", "cdl_evening_star",
    "cdl_piercing", "cdl_dark_cloud", "cdl_3white", "cdl_3black",
    # zettaranc 自研框架列 —— 2026-10-01 已修复回填,数据可用
    "ztr_white", "ztr_yellow", "ztr_bbi", "ztr_brick",
    "ztr_rsl_rank_15", "ztr_rsl_rank_105",
    # 实测正交且值域正常的新指标(见 REJECTED 一节)
    "stc", "vosc", "ui", "coppock",
    "dd_abs", "dd_frac", "dd_log",
]
# 扫描/信号用到的字段（比上面多一批振荡指标）
INDICATORS_ONLY_FIELDS: List[str] = [
    "date", "dif", "dea", "macd_hist",
    "k", "d", "j", "rsi6", "rsi14",
    "stoch_k", "stoch_d", "cci", "willr", "mfi",
    "adx", "di_plus", "di_minus", "st_dir", "st_val",
    "psar", "aroon_up", "aroon_down", "vi_plus", "vi_minus",
    "bb_upper", "bb_mid", "bb_lower", "bb_width", "atr",
    "dc_upper", "dc_lower", "kc_upper", "kc_mid", "kc_lower",
    "cmf", "obv", "vwap", "zscore", "lin_slope",
    "cdl_morning_star", "cdl_evening_star", "cdl_hammer",
    "cdl_shooting_star", "cdl_doji", "cdl_engulfing", "cdl_harami",
    "cdl_piercing", "cdl_dark_cloud", "cdl_3white", "cdl_3black",
    # 与 INDICATORS_FIELDS 同源:框架列(2026-10-01 已修复) + 正交新指标。
    # 信号规则本身还没写(见 signal_frequency_audit 门禁),这里先打通取数管道。
    "ztr_white", "ztr_yellow", "ztr_bbi", "ztr_brick",
    "ztr_rsl_rank_15", "ztr_rsl_rank_105",
    "stc", "stc_macd", "stc_stoch", "vosc", "ui", "coppock",
    "dd_abs", "dd_frac", "dd_log",
]


def _select(fields: List[str]) -> str:
    """按字段列表渲染 SELECT 列表。字段名全部来自上面的模块级常量，
    不含任何用户输入。"""
    parts = []
    for field in fields:
        expr = INDICATOR_COLUMNS.get(field) or MARKET_COLUMNS.get(field)
        if expr is None:
            raise KeyError(f"未定义的字段映射：{field}")
        parts.append(f"{expr} AS {field}")
    return ",\n            ".join(parts)


def build_market_sql() -> str:
    """行情 OHLCV。thscode / days 全部走绑定参数。"""
    return f"""
        SELECT
            {_select(MARKET_FIELDS)}
        FROM v_daily_qfq
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT ?
    """


# 指标查询一次性取全：合并去重后的字段并集。
#
# 曾经这里用的是 INDICATORS_ONLY_FIELDS，它**不含均线**。而 fetch_market_data
# 合并时又用指标行覆盖掉行情行自带的 ma5..ma250，于是所有股票的均线永远是
# None —— analyze_ma_alignment 因此恒返回 ("unknown","unknown")。
ANALYSIS_FIELDS: List[str] = list(
    dict.fromkeys(INDICATORS_FIELDS + INDICATORS_ONLY_FIELDS)
)


def build_indicators_sql() -> str:
    """技术指标全量查询（趋势/形态/支撑阻力/信号 共用）。"""
    return f"""
        SELECT
            {_select(ANALYSIS_FIELDS)}
        FROM v_indicators_daily
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT ?
    """


def build_indicators_only_sql() -> str:
    """仅技术指标（scan / signals 用）。"""
    return build_indicators_sql()


def normalize_row(raw: Dict, fields: List[str]) -> Dict:
    """按字段列表规范化：date 走字符串，其余数值缺失即 None。"""
    out: Dict[str, Any] = {}
    for field in fields:
        out[field] = to_str(raw.get(field)) if field == "date" else num(raw.get(field))
    return out


def missing_fields(row: Dict, fields: List[str]) -> List[str]:
    """该行在给定字段里缺了哪些。用于 insufficient_data 判定。"""
    return [f for f in fields if f != "date" and row.get(f) is None]


async def fetch_market_data(
    market_source,
    thscode: str,
    days: int,
    indicators_source=None,
) -> List[Dict]:
    """
    加载 OHLCV + 指标数据。

    Args:
        market_source: market 数据源（OHLCV）
        thscode: 同花顺股票代码（作为绑定参数传入，不参与 SQL 拼接）
        days: 天数（10-250）
        indicators_source: indicators 数据源（技术指标），可选

    Returns:
        按日期**倒序**排列的行列表（index 0 = 最新一天）。
        指标缺失的字段值为 None，绝不用 0 冒充。

    Raises:
        ValueError: 找不到股票数据
    """
    days = max(MIN_DAYS, min(MAX_DAYS, days))

    market_rows = await market_source.execute(build_market_sql(), [thscode, days])
    if not market_rows:
        raise ValueError(f"未找到股票数据：{thscode}")

    indicator_rows: List[Dict] = []
    if indicators_source is not None:
        try:
            indicator_rows = await indicators_source.execute(
                build_indicators_sql(), [thscode, days]
            )
        except Exception as exc:
            # 指标缺失不该让整个分析失败，但**必须留痕**。这里原本是裸
            # `except Exception: pass`，指标 SQL 一旦写错字段，整个分析层
            # 就会静默地拿着全 None 的指标继续算。
            logger.warning("指标数据加载失败 thscode=%s: %s", thscode, exc)

    ind_map = {row["date"]: row for row in indicator_rows if row.get("date")}

    rows: List[Dict] = []
    for market_row in market_rows:
        row = normalize_row(market_row, MARKET_FIELDS)
        ind = ind_map.get(row["date"])
        for field in INDICATORS_FIELDS:
            # 指标优先，指标没给就保留行情行自带的值（均线就是这么来的）
            value = None if ind is None else num(ind.get(field))
            row[field] = row.get(field) if value is None else value
        rows.append(row)

    return rows


async def fetch_indicators_only(indicators_source, thscode: str, days: int) -> List[Dict]:
    """仅加载技术指标（用于 scan/signals）。"""
    days = max(MIN_DAYS, min(MAX_SCAN_DAYS, days))
    raw_rows = await indicators_source.execute(
        build_indicators_only_sql(), [thscode, days]
    )
    return [normalize_row(r, INDICATORS_ONLY_FIELDS) for r in raw_rows]


# ---------------------------------------------------------------------------
# REJECTED — 2026-09-30 那批 33 列里**故意不接**的(2026-10-01)
#
# 为什么专门记一份否决名单:这批列已经躺在 indicators.duckdb 里,任何人看到
# `SHOW COLUMNS` 都会觉得"这 33 列还没用,加进去吧"。但把它们按 corr 排一遍
# 之后,只有 13 列带新信息(见 INDICATOR_COLUMNS 里带注释的那批)。剩下的要么
# 是已有列的数值替身,要么是**指标定义本身就是均线**。
#
# 相关系数 = 全市场截面,2025-10 至今,thscode LIKE '6%',与已在消费的 42 列比。
# 复算:
#   duckdb -readonly ~/.hithink-finance/indicators.duckdb -c "
#   WITH s AS (SELECT * FROM v_indicators_daily
#              WHERE date>='2025-10-01' AND thscode LIKE '6%')
#   SELECT corr(<新列>, <已有列>) FROM s;"
#
# --- 数值替身(corr > 0.9),接进来等于把同一个信号数两遍 ---
#   overlap_vwma_20        ~ overlap_sma_20            0.9998
#   trend_accbands_20_*    ~ volatility_bbands_20_2_0_* 0.9983
#   volume_ha_*            ~ close                     0.9858
#   momentum_ao_5_34       ~ momentum_macd_12_26_9_macd 0.9693
#   ↑ VWMA20 和 SMA20 相关 0.9998。任何线性加权里它们会成比例地**重复计权**
#     同一个信号,不是"多一个弱信号",是趋势类判断的置信度被凭空放大。
#
# --- 中度冗余(0.7 ~ 0.8),边际信息不足以支付维护成本 ---
#   trend_vwmacd_12_26_9_*  ~ momentum_macd_*_hist      0.785
#   momentum_ultosc_7_14_28 ~ momentum_stoch_*_slowk    0.775
#   momentum_bias_14        ~ momentum_rsi_14           0.800
#   momentum_lrsi_14        ~ momentum_rsi_14           0.764
#   momentum_rvi_10_252     ~ momentum_rsi_14           0.733
#
# --- 定义本身就是均线,不是新维度(2026-10-01 实测,推翻了"显式豁免"的判断) ---
#   trend_ichimoku_tenkan    ~ overlap_sma_5   0.971
#   trend_ichimoku_senkou_b ~ overlap_sma_60  0.992
#   ↑ Ichimoku 的线本身就是均线:Tenkan=(9H+9L)/2,SenkouB=(52H+52L)/2。
#     真正的新信息在**云的关系**(SenkouA vs SenkouB 的交叉 = 未来交叉)和
#     **价格相对云的位置**,那是派生关系,不是这 4 个原始列。
#     所以只接 chikou(26 日滞后收盘,结构上必为近期 26 根 NULL,属正常)。
#     想要云的交叉信号,应该在策略层用 senkou_a/senkou_b 现算,而不是把
#     两条均线塞进 INDICATOR_COLUMNS。
#
# --- 最新日恒为 NULL,接了也读不到 ---
#   trend_ichimoku_chikou: 2026-09-30 全市场非空行数 = 0。
#     Chikou 是"26 根之前的收盘价",结构性滞后,所以**最新一根必然 NULL**
#     (framework.toml 已按 180 天窗口把它标成结构性 78.7%)。它不是坏数据,
#     但接进"分析今天这只票"的管道里读不到任何值;要看它得在历史 bar 上取。
#     → 暂不接,连上面那句"只接 chikou"也一并作废。
#
# --- 值域异常,先不接 ---
#   momentum_brar_ar: 全库 max=370.7 / p99=218.3,远超 BRAR 经典的 0~100 带。
#   momentum_brar_br: 最新日 max=564.1(2026-09-30),同样越界。
#     → **两列都拒**(2026-10-01 修正:先前只拒 AR、接了 BR 是错的)。
#     corr 上 BR 确实正交(vs willr 0.431),但**正交性救不了错公式**。
#     a-stock/INDICATORS_DB.md 写过的"能算、不报错、值域离谱"说的就是这个:
#     必须人工复算公式,不能只看相关性。
#     复算命令:
#       duckdb -readonly ~/.hithink-finance/indicators.duckdb -c \
#       "SELECT max(momentum_brar_br) FROM v_indicators_daily WHERE date='2026-09-30';"
#
# 什么时候可以重新评估:若某条信号的实测触发频率落在 dead/noisy 档
# (internal/agent/tools/hithink_finance/pattern/signal_frequency_audit.md),
# 说明现有列不够用,再回来看这份名单,并**带上频率数据**而不是凭直觉。
