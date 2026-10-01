package pattern

// ⚠️ 与 python-service/zettaranc 的已知偏差（2026-10-01）
//
// 这个包是 zettaranc 信号引擎的 Go 孪生实现。同一天，Python 侧的
// data_loader.py 打通了 15 个新指标列的取数管道：
//
//	正交且已验证（9）: stc / stc_macd / stc_stoch / vosc / ui / coppock
//	                  dd_abs / dd_frac / dd_log
//	2026-10-01 已修复（6）: ztr_white / ztr_yellow / ztr_bbi / ztr_brick
//	                      ztr_rsl_rank_15 / ztr_rsl_rank_105
//
// 本文件的 `row` 结构体与 SELECT 列表**尚未包含**上述任何一列，因此
// detectSignals 的行为与 Python 侧**不一致**：Python 能读到的新数据，
// Go 读不到。这是刻意的，不是遗漏 —— 见下。
//
// 为什么先不对齐:
//  1. 信号门禁只存在于本侧。signal_frequency_audit_test.go 跑的是这里
//     的 detectSignals，Python 侧写的新信号**无法被审计**。先在 Go 实现
//     才能拿到 dead/noisy/informative 的频率分布。
//  2. ztr_* 六列此前数据不可用（16% 覆盖、99.6% 常数 0、值域是排名而非
//     价格量纲），已由 a-stock 侧修复合入管道；Go 侧仍按同样的顺序
//     等信号实现后再接 —— 接入**数据**不等于接入**信号规则**。
//
// 对齐顺序：新信号先在 signals.go 实现 → 登记进 declaredSignalNames →
// 跑 signal_audit 确认 verdict 为 informative → 再回填 Python 侧。
// 反过来做等于在两个语言里各维护一份没人验证过有效性的规则。

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// row represents one day of indicator data from DuckDB.
type row struct {
	Date  string  `json:"date"`
	Close float64 `json:"close"`
	// Momentum
	DIF      float64 `json:"dif"`
	DEA      float64 `json:"dea"`
	MACDHist float64 `json:"macd_hist"`
	K        float64 `json:"k"`
	D        float64 `json:"d"`
	J        float64 `json:"j"`
	// ER = Kaufman Efficiency Ratio (momentum_er_10)：|10 日净变动| / 10 日逐日
	// 绝对变动之和，取值 0~1。ADX 回答"趋势有多强"，ER 回答"这段趋势走得多干净"
	//——同样 +10%，一根直线走到和来回震荡走到，是两个完全不同的持仓前提。
	ER     float64 `json:"er"`
	RSI6   float64 `json:"rsi6"`
	RSI14  float64 `json:"rsi14"`
	StochK float64 `json:"stoch_k"`
	StochD float64 `json:"stoch_d"`
	CCI    float64 `json:"cci"`
	WillR  float64 `json:"willr"`
	MFI    float64 `json:"mfi"`
	// Trend
	ADX       float64 `json:"adx"`
	DIPlus    float64 `json:"di_plus"`
	DIMinus   float64 `json:"di_minus"`
	STDir     float64 `json:"st_dir"`
	STVal     float64 `json:"st_val"`
	PSAR      float64 `json:"psar"`
	AroonUp   float64 `json:"aroon_up"`
	AroonDown float64 `json:"aroon_down"`
	VIPlus    float64 `json:"vi_plus"`
	VIMinus   float64 `json:"vi_minus"`
	// Choppiness Index (trend_chop_14)，取值 0~100，固定区间，不随价格尺度变化。
	// ADX 说"有没有趋势"，CHOP 说"这个趋势能不能一直跟下去"——震荡市里趋势型
	// 持仓该减，这就是它要回答的"该不该继续拿着"。
	Chop float64 `json:"chop"`
	// Volatility
	BBUpper float64 `json:"bb_upper"`
	BBMid   float64 `json:"bb_mid"`
	BBLower float64 `json:"bb_lower"`
	BBWidth float64 `json:"bb_width"`
	ATR     float64 `json:"atr"`
	// NATR = 100 * ATR / Close (volatility_natr_14)：把 ATR 折算成百分比。
	// ATR 是绝对价差，¥20 的票和 ¥200 的票没法共用一个止损距离；NATR 可以。
	NATR    float64 `json:"natr"`
	DCUpper float64 `json:"dc_upper"`
	DCLower float64 `json:"dc_lower"`
	KCUpper float64 `json:"kc_upper"`
	KCMid   float64 `json:"kc_mid"`
	KCLower float64 `json:"kc_lower"`
	// Volume
	CMF  float64 `json:"cmf"`
	OBV  float64 `json:"obv"`
	VWAP float64 `json:"vwap"`
	// PVT/PVI/NVI 都是**累计量**（逐日求和），绝对水平由起点决定、没有跨标的
	// 可比性；只有近 N 根 K 线之间的差值（斜率）有意义。signals.go 一律读差值，
	// 不读绝对值，并且两侧都必须非 0 才算数——见 hasData 的注释。
	PVT float64 `json:"pvt"`
	PVI float64 `json:"pvi"`
	NVI float64 `json:"nvi"`
	// Statistics
	ZScore   float64 `json:"zscore"`
	LinSlope float64 `json:"lin_slope"`
	// Candlestick patterns (non-zero = pattern present)
	CdlMorningStar  float64 `json:"cdl_morning_star"`
	CdlEveningStar  float64 `json:"cdl_evening_star"`
	CdlHammer       float64 `json:"cdl_hammer"`
	CdlShootingStar float64 `json:"cdl_shooting_star"`
	CdlDoji         float64 `json:"cdl_doji"`
	CdlEngulfing    float64 `json:"cdl_engulfing"`
	CdlHarami       float64 `json:"cdl_harami"`
	CdlPiercing     float64 `json:"cdl_piercing"`
	CdlDarkCloud    float64 `json:"cdl_dark_cloud"`
	Cdl3WhiteSold   float64 `json:"cdl_3white"`
	Cdl3BlackCrows  float64 `json:"cdl_3black"`
}

// queryIndicatorRows fetches recent indicator rows from DuckDB.
// rows are ordered newest-first (index 0 = latest day).
func queryIndicatorRows(ctx context.Context, config *hithink_finance.Config, thscode string, days int) ([]row, error) {
	// thscode and days are bound parameters, never formatted into the SQL.
	//
	// 这里刻意不再取 `close`：该列属于 market.v_daily_qfq，而
	// indicators.v_indicators_daily 根本没有价格列；两个 DuckDB 是独立只读连接、
	// 无法 ATTACH，混着写必然 Binder Error。收盘价改由下面的 closeSQL 从 market
	// 侧单独取，再按 date 合并回 indicator 行。
	sql := `
		SELECT
			CAST(date AS VARCHAR) AS date,
			COALESCE(momentum_macd_12_26_9_macd, 0) AS dif,
			COALESCE(momentum_macd_12_26_9_signal, 0) AS dea,
			COALESCE(momentum_macd_12_26_9_hist, 0) AS macd_hist,
			COALESCE(momentum_kdj_9_3_k, 0) AS k,
			COALESCE(momentum_kdj_9_3_d, 0) AS d,
			COALESCE(momentum_kdj_9_3_j, 0) AS j,
			COALESCE(momentum_er_10, 0) AS er,
			COALESCE(momentum_rsi_6, 0) AS rsi6,
			COALESCE(momentum_rsi_14, 0) AS rsi14,
			COALESCE(momentum_stoch_14_3_3_slowk, 0) AS stoch_k,
			COALESCE(momentum_stoch_14_3_3_slowd, 0) AS stoch_d,
			COALESCE(momentum_cci_20, 0) AS cci,
			COALESCE(momentum_willr_14, 0) AS willr,
			COALESCE(volume_mfi_14, 0) AS mfi,
			COALESCE(trend_adx_14, 0) AS adx,
			COALESCE(momentum_dm_14_plus, 0) AS di_plus,
			COALESCE(momentum_dm_14_minus, 0) AS di_minus,
			COALESCE(trend_supertrend_10_3_0_direction, 0) AS st_dir,
			COALESCE(trend_supertrend_10_3_0_trend, 0) AS st_val,
			COALESCE(trend_psar, 0) AS psar,
			COALESCE(momentum_aroon_25_aroonup, 0) AS aroon_up,
			COALESCE(momentum_aroon_25_aroondown, 0) AS aroon_down,
			COALESCE(trend_vortex_14_plus, 0) AS vi_plus,
			COALESCE(trend_vortex_14_minus, 0) AS vi_minus,
			COALESCE(trend_chop_14, 0) AS chop,
			COALESCE(volatility_bbands_20_2_0_upper, 0) AS bb_upper,
			COALESCE(volatility_bbands_20_2_0_middle, 0) AS bb_mid,
			COALESCE(volatility_bbands_20_2_0_lower, 0) AS bb_lower,
			COALESCE(volatility_bbands_20_2_0_upper - volatility_bbands_20_2_0_lower, 0) AS bb_width,
			COALESCE(volatility_atr_14, 0) AS atr,
			COALESCE(volatility_natr_14, 0) AS natr,
			COALESCE(volatility_donchian_20_upper, 0) AS dc_upper,
			COALESCE(volatility_donchian_20_lower, 0) AS dc_lower,
			COALESCE(volatility_kc_20_2_upper, 0) AS kc_upper,
			COALESCE(volatility_kc_20_2_middle, 0) AS kc_mid,
			COALESCE(volatility_kc_20_2_lower, 0) AS kc_lower,
			COALESCE(volume_cmf_20, 0) AS cmf,
			COALESCE(volume_obv, 0) AS obv,
			COALESCE(volume_vwap, 0) AS vwap,
			COALESCE(volume_pvt, 0) AS pvt,
			COALESCE(volume_pvi, 0) AS pvi,
			COALESCE(volume_nvi, 0) AS nvi,
			COALESCE(statistics_zscore_20, 0) AS zscore,
			COALESCE(statistics_linearreg_slope_14, 0) AS lin_slope,
			COALESCE(candles_cdl_morningstar_0, 0) AS cdl_morning_star,
			COALESCE(candles_cdl_eveningstar_0, 0) AS cdl_evening_star,
			COALESCE(candles_cdl_hammer_0, 0) AS cdl_hammer,
			COALESCE(candles_cdl_shootingstar_0, 0) AS cdl_shooting_star,
			COALESCE(candles_cdl_doji_0, 0) AS cdl_doji,
			COALESCE(candles_cdl_engulfing_0, 0) AS cdl_engulfing,
			COALESCE(candles_cdl_harami_0, 0) AS cdl_harami,
			COALESCE(candles_cdl_piercing_0, 0) AS cdl_piercing,
			COALESCE(candles_cdl_darkcloudcover_0, 0) AS cdl_dark_cloud,
			COALESCE(candles_cdl_3whitesoldiers_0, 0) AS cdl_3white,
			COALESCE(candles_cdl_3blackcrows_0, 0) AS cdl_3black
		FROM v_indicators_daily
		WHERE thscode = ?
		ORDER BY date DESC
		LIMIT ?
	`

	results, err := hithink_finance.QueryDuckDBParams(
		ctx, config, "indicators", sql, thscode, days)
	if err != nil {
		return nil, err
	}

	// 收盘价单独从 market 库取（见上面关于 close 的注释），按 date 合并。
	// Donchian 突破信号依赖 Close（signals.go），所以不能直接丢掉该字段。
	closeSQL := `SELECT CAST(date AS VARCHAR) AS date, close FROM v_daily_qfq WHERE thscode = ? ORDER BY date DESC LIMIT ?`
	closeRows, err := hithink_finance.QueryDuckDBParams(
		ctx, config, "market", closeSQL, thscode, days)
	if err != nil {
		return nil, err
	}
	closeByDate := make(map[string]float64, len(closeRows))
	for _, r := range closeRows {
		closeByDate[fmt.Sprint(r["date"])] = f64(r, "close")
	}

	var rows []row
	for _, r := range results {
		mapped := mapToRow(r)
		mapped.Close = closeByDate[mapped.Date]
		rows = append(rows, mapped)
	}
	return rows, nil
}

func mapToRow(m map[string]interface{}) row {
	return row{
		Date:            str(m, "date"),
		Close:           f64(m, "close"),
		DIF:             f64(m, "dif"),
		DEA:             f64(m, "dea"),
		MACDHist:        f64(m, "macd_hist"),
		K:               f64(m, "k"),
		D:               f64(m, "d"),
		J:               f64(m, "j"),
		ER:              f64(m, "er"),
		RSI6:            f64(m, "rsi6"),
		RSI14:           f64(m, "rsi14"),
		StochK:          f64(m, "stoch_k"),
		StochD:          f64(m, "stoch_d"),
		CCI:             f64(m, "cci"),
		WillR:           f64(m, "willr"),
		MFI:             f64(m, "mfi"),
		ADX:             f64(m, "adx"),
		DIPlus:          f64(m, "di_plus"),
		DIMinus:         f64(m, "di_minus"),
		STDir:           f64(m, "st_dir"),
		STVal:           f64(m, "st_val"),
		PSAR:            f64(m, "psar"),
		AroonUp:         f64(m, "aroon_up"),
		AroonDown:       f64(m, "aroon_down"),
		VIPlus:          f64(m, "vi_plus"),
		VIMinus:         f64(m, "vi_minus"),
		Chop:            f64(m, "chop"),
		BBUpper:         f64(m, "bb_upper"),
		BBMid:           f64(m, "bb_mid"),
		BBLower:         f64(m, "bb_lower"),
		BBWidth:         f64(m, "bb_width"),
		ATR:             f64(m, "atr"),
		NATR:            f64(m, "natr"),
		DCUpper:         f64(m, "dc_upper"),
		DCLower:         f64(m, "dc_lower"),
		KCUpper:         f64(m, "kc_upper"),
		KCMid:           f64(m, "kc_mid"),
		KCLower:         f64(m, "kc_lower"),
		CMF:             f64(m, "cmf"),
		OBV:             f64(m, "obv"),
		VWAP:            f64(m, "vwap"),
		PVT:             f64(m, "pvt"),
		PVI:             f64(m, "pvi"),
		NVI:             f64(m, "nvi"),
		ZScore:          f64(m, "zscore"),
		LinSlope:        f64(m, "lin_slope"),
		CdlMorningStar:  f64(m, "cdl_morning_star"),
		CdlEveningStar:  f64(m, "cdl_evening_star"),
		CdlHammer:       f64(m, "cdl_hammer"),
		CdlShootingStar: f64(m, "cdl_shooting_star"),
		CdlDoji:         f64(m, "cdl_doji"),
		CdlEngulfing:    f64(m, "cdl_engulfing"),
		CdlHarami:       f64(m, "cdl_harami"),
		CdlPiercing:     f64(m, "cdl_piercing"),
		CdlDarkCloud:    f64(m, "cdl_dark_cloud"),
		Cdl3WhiteSold:   f64(m, "cdl_3white"),
		Cdl3BlackCrows:  f64(m, "cdl_3black"),
	}
}

func f64(m map[string]interface{}, key string) float64 {
	v, ok := m[key]
	if !ok || v == nil {
		return 0
	}
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case json.Number:
		f, _ := x.Float64()
		return f
	case string:
		// DuckDB sometimes returns numbers as strings
		var f float64
		fmt.Sscanf(x, "%f", &f)
		return f
	default:
		return 0
	}
}

func str(m map[string]interface{}, key string) string {
	v, ok := m[key]
	if !ok || v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
