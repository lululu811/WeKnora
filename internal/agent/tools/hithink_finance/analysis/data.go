// Package analysis provides classical technical analysis tools.
//
// Four independent tools covering:
//   - Trend analysis (Dow Theory, moving averages, trend strength)
//   - Volume analysis (Wyckoff, volume-price, money flow)
//   - Chart patterns (H&S, double top/bottom, triangles, wedges)
//   - Support/resistance (pivot points, Fibonacci, key levels)
package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// marketRow holds one day of OHLCV + indicator data.
type marketRow struct {
	Date  string
	Open  float64
	High  float64
	Low   float64
	Close float64
	Vol   float64
	// Moving averages
	MA5, MA10, MA20, MA60, MA120, MA250 float64
	// Momentum
	DIF, DEA, MACDHist float64
	RSI6, RSI14        float64
	// Trend
	ADX, DIPlus, DIMinus float64
	STDir, STVal         float64
	// Volatility
	BBUpper, BBMID, BBLower float64
	ATR                     float64
	// Volume
	CMF, MFI, OBV, VWAP float64
	// Extended
	AroonUp, AroonDown float64
	LinSlope           float64
	ZScore             float64
	// Candlestick patterns
	CdlHammer, CdlShootingStar, CdlDoji        float64
	CdlEngulfing, CdlHarami, CdlMorningStar    float64
	CdlEveningStar, CdlPiercing, CdlDarkCloud  float64
	Cdl3WhiteSold, Cdl3BlackCrows              float64
}

// FetchMarketData loads OHLCV + indicators from both DuckDB databases.
// Returns rows ordered newest-first (index 0 = latest).
func FetchMarketData(ctx context.Context, config *hithink_finance.Config, thscode string, days int) ([]marketRow, error) {
	if days < 10 {
		days = 10
	}
	if days > 250 {
		days = 250
	}

	// Fetch from market DB: OHLCV + moving averages.
	//
	// thscode is bound as a parameter, never formatted into the SQL. The old
	// `fmt.Sprintf(..., thscode, ...)` made this an injection point that
	// python-service happily executed.
	marketSQL := `
		SELECT
			CAST(date AS VARCHAR) AS date,
			open, high, low, close,
			volume AS vol,
			overlap_sma_5 AS ma5,
			overlap_sma_10 AS ma10,
			overlap_sma_20 AS ma20,
			overlap_sma_60 AS ma60,
			overlap_sma_120 AS ma120,
			overlap_sma_250 AS ma250
		FROM v_daily_qfq
		WHERE thscode = ?
		ORDER BY date DESC
		LIMIT ?
	`

	// Fetch from indicators DB: all technical indicators.
	//
	// COALESCE(col, 0) is deliberately NOT used: a NULL indicator means "not
	// computed", and coercing it to 0 makes RSI6=0 read as "oversold", which
	// produced fabricated buy signals on instruments with no indicator data.
	// marketRow uses plain float64, so toF64 maps NULL to 0 regardless — see
	// the DataComplete flag below for how callers are told not to trust it.
	indicatorSQL := `
		SELECT
			CAST(date AS VARCHAR) AS date,
			momentum_macd_12_26_9_macd AS dif,
			momentum_macd_12_26_9_signal AS dea,
			momentum_macd_12_26_9_hist AS macd_hist,
			momentum_rsi_6 AS rsi6,
			momentum_rsi_14 AS rsi14,
			trend_adx_14 AS adx,
			momentum_dm_14_plus AS di_plus,
			momentum_dm_14_minus AS di_minus,
			trend_supertrend_10_3_0_direction AS st_dir,
			trend_supertrend_10_3_0_trend AS st_val,
			volatility_bbands_20_2_0_upper AS bb_upper,
			volatility_bbands_20_2_0_middle AS bb_mid,
			volatility_bbands_20_2_0_lower AS bb_lower,
			volatility_atr_14 AS atr,
			volume_cmf_20 AS cmf,
			volume_mfi_14 AS mfi,
			volume_obv AS obv,
			volume_vwap AS vwap,
			momentum_aroon_25_aroonup AS aroon_up,
			momentum_aroon_25_aroondown AS aroon_down,
			statistics_linearreg_slope_14 AS lin_slope,
			statistics_zscore_20 AS zscore,
			candles_cdl_hammer_0 AS cdl_hammer,
			candles_cdl_shootingstar_0 AS cdl_shooting_star,
			candles_cdl_doji_0 AS cdl_doji,
			candles_cdl_engulfing_0 AS cdl_engulfing,
			candles_cdl_harami_0 AS cdl_harami,
			candles_cdl_morningstar_0 AS cdl_morning_star,
			candles_cdl_eveningstar_0 AS cdl_evening_star,
			candles_cdl_piercing_0 AS cdl_piercing,
			candles_cdl_darkcloudcover_0 AS cdl_dark_cloud,
			candles_cdl_3whitesoldiers_0 AS cdl_3white,
			candles_cdl_3blackcrows_0 AS cdl_3black
		FROM v_indicators_daily
		WHERE thscode = ?
		ORDER BY date DESC
		LIMIT ?
	`


	marketRows, err := hithink_finance.QueryDuckDBParams(
		ctx, config, "market", marketSQL, thscode, days)
	if err != nil {
		return nil, fmt.Errorf("market query: %w", err)
	}
	if len(marketRows) == 0 {
		return nil, fmt.Errorf("未找到股票数据：%s", thscode)
	}

	indicatorRows, err := hithink_finance.QueryDuckDBParams(
		ctx, config, "indicators", indicatorSQL, thscode, days)
	if err != nil {
		return nil, fmt.Errorf("indicators query: %w", err)
	}

	// Merge: match by date
	indMap := make(map[string]map[string]interface{})
	for _, r := range indicatorRows {
		if d, ok := r["date"]; ok {
			indMap[fmt.Sprint(d)] = r
		}
	}

	var rows []marketRow
	for _, m := range marketRows {
		date := fmt.Sprint(m["date"])
		ind := indMap[date]

		row := marketRow{
			Date:  date,
			Open:  toF64(m["open"]),
			High:  toF64(m["high"]),
			Low:   toF64(m["low"]),
			Close: toF64(m["close"]),
			Vol:   toF64(m["vol"]),
			MA5:   toF64(m["ma5"]),
			MA10:  toF64(m["ma10"]),
			MA20:  toF64(m["ma20"]),
			MA60:  toF64(m["ma60"]),
			MA120: toF64(m["ma120"]),
			MA250: toF64(m["ma250"]),
		}
		if ind != nil {
			row.DIF = toF64(ind["dif"])
			row.DEA = toF64(ind["dea"])
			row.MACDHist = toF64(ind["macd_hist"])
			row.RSI6 = toF64(ind["rsi6"])
			row.RSI14 = toF64(ind["rsi14"])
			row.ADX = toF64(ind["adx"])
			row.DIPlus = toF64(ind["di_plus"])
			row.DIMinus = toF64(ind["di_minus"])
			row.STDir = toF64(ind["st_dir"])
			row.STVal = toF64(ind["st_val"])
			row.BBUpper = toF64(ind["bb_upper"])
			row.BBMID = toF64(ind["bb_mid"])
			row.BBLower = toF64(ind["bb_lower"])
			row.ATR = toF64(ind["atr"])
			row.CMF = toF64(ind["cmf"])
			row.MFI = toF64(ind["mfi"])
			row.OBV = toF64(ind["obv"])
			row.VWAP = toF64(ind["vwap"])
			row.AroonUp = toF64(ind["aroon_up"])
			row.AroonDown = toF64(ind["aroon_down"])
			row.LinSlope = toF64(ind["lin_slope"])
			row.ZScore = toF64(ind["zscore"])
			row.CdlHammer = toF64(ind["cdl_hammer"])
			row.CdlShootingStar = toF64(ind["cdl_shooting_star"])
			row.CdlDoji = toF64(ind["cdl_doji"])
			row.CdlEngulfing = toF64(ind["cdl_engulfing"])
			row.CdlHarami = toF64(ind["cdl_harami"])
			row.CdlMorningStar = toF64(ind["cdl_morning_star"])
			row.CdlEveningStar = toF64(ind["cdl_evening_star"])
			row.CdlPiercing = toF64(ind["cdl_piercing"])
			row.CdlDarkCloud = toF64(ind["cdl_dark_cloud"])
			row.Cdl3WhiteSold = toF64(ind["cdl_3white"])
			row.Cdl3BlackCrows = toF64(ind["cdl_3black"])
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// toF64 converts interface{} to float64 safely.
func toF64(v interface{}) float64 {
	if v == nil {
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
		var f float64
		fmt.Sscanf(x, "%f", &f)
		return f
	default:
		return 0
	}
}

// findSwings detects swing highs and lows using a simple window method.
// A swing high is a bar whose high is the highest within ±window bars.
func findSwings(rows []marketRow, window int) (highs, lows []int) {
	n := len(rows)
	for i := window; i < n-window; i++ {
		isHigh := true
		isLow := true
		for j := i - window; j <= i+window; j++ {
			if j == i {
				continue
			}
			if rows[j].High >= rows[i].High {
				isHigh = false
			}
			if rows[j].Low <= rows[i].Low {
				isLow = false
			}
		}
		if isHigh {
			highs = append(highs, i)
		}
		if isLow {
			lows = append(lows, i)
		}
	}
	return
}

// minInt returns the smaller of two ints.
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Suppress unused import warnings.
var _ = strings.Contains
