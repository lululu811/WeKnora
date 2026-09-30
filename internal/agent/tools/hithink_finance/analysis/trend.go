package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"math"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// TrendAnalysisTool performs classical trend analysis based on Dow Theory,
// moving averages, and trend strength indicators.
type TrendAnalysisTool struct {
	config *hithink_finance.Config
}

// NewTrendAnalysisTool creates a new trend analysis tool.
func NewTrendAnalysisTool(config *hithink_finance.Config) *TrendAnalysisTool {
	return &TrendAnalysisTool{config: config}
}

func (t *TrendAnalysisTool) Name() string {
	return "hithink.finance.analysis.trend"
}

func (t *TrendAnalysisTool) Description() string {
	return `经典趋势分析工具。基于道氏理论、均线系统和趋势强度指标，判断股票的趋势方向、强度和潜在转折。

分析内容：
- 道氏理论：识别HH/HL（上升趋势）或LH/LL（下降趋势）结构
- 均线排列：多头排列（MA5>MA10>MA20>MA60）/ 空头排列 / 均线粘合
- 均线交叉：短期均线上穿/下穿长期均线
- 葛兰碧法则：价格与MA20的关系（突破/跌破/回踩/反弹）
- 趋势强度：ADX分级、趋势持续性判断
- Supertrend：当前趋势方向
- 趋势转折信号：均线金叉死叉、价格突破关键均线

返回结构化JSON，包含趋势判断、均线状态、关键信号和操作建议。

使用示例：thscode="600519.SH", days=120`
}

func (t *TrendAnalysisTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH（茅台）、300750.SZ（宁德时代）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "分析时间窗口（交易日），默认120，最大250",
				"default":     120,
				"maximum":     250,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *TrendAnalysisTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 120
	}

	rows, err := FetchMarketData(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("数据加载失败：%v", err)}, nil
	}
	if len(rows) < 20 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("数据不足：%d 条，至少需要 20 条", len(rows))}, nil
	}

	latest := rows[0]

	// --- Dow Theory Structure ---
	dowDir, dowDesc := analyzeDowStructure(rows)

	// --- Moving Average Alignment ---
	alignment, priceVsMA20 := analyzeMAAlignment(latest)

	// --- MA Crossovers ---
	crossovers := detectMACrossovers(rows)

	// --- Granville Signals ---
	granville := detectGranvilleSignals(rows)

	// --- Trend Strength (ADX) ---
	_, strengthLabel := classifyADX(latest)

	// --- Supertrend ---
	stDir, stVal := classifySupertrend(latest)

	// --- MA values ---
	maValues := map[string]interface{}{
		"ma5":   round2(latest.MA5),
		"ma10":  round2(latest.MA10),
		"ma20":  round2(latest.MA20),
		"ma60":  round2(latest.MA60),
		"ma120": round2(latest.MA120),
	}

	// --- Summary / verdict ---
	verdict, confidence, keySignals := buildSummary(dowDir, alignment, priceVsMA20, crossovers, granville, strengthLabel, stDir, latest)

	result := map[string]interface{}{
		"thscode": params.Thscode,
		"trend": map[string]interface{}{
			"direction":     dowDir,
			"dow_structure": dowDesc,
			"strength":      strengthLabel,
			"adx":           round2(latest.ADX),
		},
		"moving_averages": map[string]interface{}{
			"alignment":     alignment,
			"ma_values":     maValues,
			"price_vs_ma20": priceVsMA20,
			"crossovers":    crossovers,
		},
		"granville_signals": granville,
		"supertrend": map[string]interface{}{
			"direction": stDir,
			"value":     round2(stVal),
		},
		"summary": map[string]interface{}{
			"verdict":     verdict,
			"confidence":  confidence,
			"key_signals": keySignals,
		},
		"data_quality": buildDataQuality(rows, latest),
		"_source": map[string]interface{}{
			"db":     []string{"market", "indicators"},
			"tables": []string{"v_daily_qfq", "v_indicators_daily"},
		},
	}

	data, _ := json.MarshalIndent(result, "", "  ")
	// Output 是唯一会进模型上下文的字段（modelcontext/registry.go 成功分支只读
	// result.Output，observe.go 的 ModelToolResultForTool 也走同一条路）。原先这里
	// 只填 Data，导致本工具返回 Success:true 但模型收到空字符串——buildDataQuality
	// 的全部如实说明都到不了模型眼前，而这恰恰是这个工具存在的意义。
	// Data 仅供 Langfuse 记录 data_keys，保留不动。
	return &types.ToolResult{
		Success: true,
		Output:  string(data),
		Data: map[string]interface{}{
			"result": string(data),
		},
	}, nil
}

// buildDataQuality 把"这次分析建立在多少真实数据上"如实摊开。
//
// 不加这个块，模型会对着 60 根 K 线和 0 根指标行给出同样笃定的趋势判断
// ——因为缺失的那些字段全是 0，而 0 在 ADX/RSI/均线里都是合法读数。
// 附上完整度与降级说明，模型才有材料说"指标只覆盖了 3/60 天，
// 这个趋势判断仅供参考"，而不是编一个确定的结论。
func buildDataQuality(rows []marketRow, latest marketRow) map[string]interface{} {
	total := len(rows)
	indicatorRows := 0
	for _, r := range rows {
		if r.IndicatorValid && r.AnyIndicatorValue() {
			indicatorRows++
		}
	}

	notes := make([]string, 0, 3)
	if indicatorRows == 0 {
		notes = append(notes,
			"本次取数没有任何一行的指标数据，ADX/RSI/均线/Supertrend 全部为占位 0，"+
				"下面这些结论不可采信")
	} else if indicatorRows < total {
		notes = append(notes, fmt.Sprintf("指标数据只覆盖 %d/%d 个交易日，其余 %d 天的指标字段为占位 0，"+
			"涉及缺失日的信号判断不可采信", indicatorRows, total, total-indicatorRows))
	}
	if !latest.OHLCValid {
		notes = append(notes, "最新一根 K 线的价格字段不完整，涨跌幅类判断不可用")
	}
	if total < 60 {
		notes = append(notes, fmt.Sprintf("K 线仅 %d 根，短于 MA60 所需的 60 根，中长期均线结论不成立", total))
	}

	return map[string]interface{}{
		"bars":                 total,
		"indicator_bars":       indicatorRows,
		"indicator_coverage":   ratio(indicatorRows, total),
		"latest_ohlc_complete": latest.OHLCValid,
		"degraded":             len(notes) > 0,
		"notes":                notes,
	}
}

// ratio 返回 0~1 的覆盖率；分母为 0 时返回 0（没有数据，覆盖率就是 0 而不是 NaN）。
func ratio(num, den int) float64 {
	if den <= 0 {
		return 0
	}
	return float64(num) / float64(den)
}

// analyzeDowStructure uses swing points to classify Dow Theory trend.
func analyzeDowStructure(rows []marketRow) (direction, description string) {
	// rows[0] = newest. findSwings returns indices into rows.
	highs, lows := findSwings(rows, 5)

	// Take the most recent 4-6 swing points. Since rows are newest-first,
	// smaller indices = more recent. Collect the most recent ones.
	maxPoints := 6
	if len(highs) > maxPoints {
		highs = highs[:maxPoints]
	}
	if len(lows) > maxPoints {
		lows = lows[:maxPoints]
	}

	// Need at least 2 of each to compare
	if len(highs) < 2 || len(lows) < 2 {
		return "consolidation", "摆动点不足，无法判断趋势结构"
	}

	// Direction check.
	//
	// findSwings walks i from window to n-window, so the returned index slice
	// is ASCENDING. The rows are DESCENDING (rows[0] is newest). The two
	// cancel out: **the head of the slice is the most recent swing**, the tail
	// is the oldest. So "recent vs previous" is highs[0] vs highs[1].
	//
	// The old code took highs[len-1]/highs[len-2] while calling them
	// recentHigh/prevHigh, i.e. it compared the two OLDEST swings — which
	// reported a textbook Higher-High + Higher-Low uptrend as "downtrend".
	recentHighIdx := highs[0]
	prevHighIdx := highs[1]
	recentLowIdx := lows[0]
	prevLowIdx := lows[1]

	hh := rows[recentHighIdx].High > rows[prevHighIdx].High
	hl := rows[recentLowIdx].Low > rows[prevLowIdx].Low
	lh := rows[recentHighIdx].High < rows[prevHighIdx].High
	ll := rows[recentLowIdx].Low < rows[prevLowIdx].Low

	if hh && hl {
		return "uptrend", fmt.Sprintf("Higher High (%.2f > %.2f) + Higher Low (%.2f > %.2f) → 上升趋势结构",
			rows[recentHighIdx].High, rows[prevHighIdx].High,
			rows[recentLowIdx].Low, rows[prevLowIdx].Low)
	}
	if lh && ll {
		return "downtrend", fmt.Sprintf("Lower High (%.2f < %.2f) + Lower Low (%.2f < %.2f) → 下降趋势结构",
			rows[recentHighIdx].High, rows[prevHighIdx].High,
			rows[recentLowIdx].Low, rows[prevLowIdx].Low)
	}
	if hh && ll {
		return "consolidation", fmt.Sprintf("Higher High + Lower Low → 收敛/扩张震荡")
	}
	if lh && hl {
		return "consolidation", fmt.Sprintf("Lower High + Higher Low → 收敛三角形整理")
	}
	return "consolidation", "摆动结构不明确，处于整理阶段"
}

// analyzeMAAlignment classifies the MA alignment state.
func analyzeMAAlignment(r marketRow) (alignment, priceVsMA20 string) {
	if r.MA5 <= 0 || r.MA10 <= 0 || r.MA20 <= 0 || r.MA60 <= 0 {
		return "mixed", "unknown"
	}

	bullish := r.MA5 > r.MA10 && r.MA10 > r.MA20 && r.MA20 > r.MA60 && r.MA60 > r.MA120 && r.MA120 > 0
	bearish := r.MA5 < r.MA10 && r.MA10 < r.MA20 && r.MA20 < r.MA60 && r.MA60 < r.MA120 && (r.MA120 < r.MA250 || r.MA250 <= 0)

	if bullish {
		alignment = "bullish"
	} else if bearish {
		alignment = "bearish"
	} else {
		alignment = "mixed"
	}

	if r.MA20 > 0 {
		if r.Close > r.MA20 {
			priceVsMA20 = "above"
		} else {
			priceVsMA20 = "below"
		}
	} else {
		priceVsMA20 = "unknown"
	}
	return
}

// detectMACrossovers checks the latest day vs prior day for MA crosses.
func detectMACrossovers(rows []marketRow) []map[string]interface{} {
	if len(rows) < 2 {
		return nil
	}
	curr := rows[0]
	prev := rows[1]

	var crosses []map[string]interface{}

	type maPair struct {
		short, long func(marketRow) float64
		name        string
	}
	pairs := []maPair{
		{func(r marketRow) float64 { return r.MA5 }, func(r marketRow) float64 { return r.MA10 }, "MA5/MA10"},
		{func(r marketRow) float64 { return r.MA5 }, func(r marketRow) float64 { return r.MA20 }, "MA5/MA20"},
		{func(r marketRow) float64 { return r.MA10 }, func(r marketRow) float64 { return r.MA20 }, "MA10/MA20"},
		{func(r marketRow) float64 { return r.MA10 }, func(r marketRow) float64 { return r.MA60 }, "MA10/MA60"},
		{func(r marketRow) float64 { return r.MA20 }, func(r marketRow) float64 { return r.MA60 }, "MA20/MA60"},
	}

	for _, p := range pairs {
		cs := p.short(curr)
		ps := p.short(prev)
		cl := p.long(curr)
		pl := p.long(prev)
		if cs <= 0 || cl <= 0 || ps <= 0 || pl <= 0 {
			continue
		}
		// Golden cross: short was below long, now above
		if ps <= pl && cs > cl {
			crosses = append(crosses, map[string]interface{}{
				"type": "golden_cross",
				"mas":  p.name,
				"date": curr.Date,
				"desc": fmt.Sprintf("%s 金叉", p.name),
			})
		}
		// Death cross: short was above long, now below
		if ps >= pl && cs < cl {
			crosses = append(crosses, map[string]interface{}{
				"type": "death_cross",
				"mas":  p.name,
				"date": curr.Date,
				"desc": fmt.Sprintf("%s 死叉", p.name),
			})
		}
	}
	return crosses
}

// detectGranvilleSignals checks price vs MA20 relationships.
func detectGranvilleSignals(rows []marketRow) []map[string]interface{} {
	if len(rows) < 2 {
		return nil
	}
	curr := rows[0]
	prev := rows[1]

	if curr.MA20 <= 0 || prev.MA20 <= 0 {
		return nil
	}

	var signals []map[string]interface{}

	// Buy 1: price crosses above MA20 from below (突破)
	if prev.Close <= prev.MA20 && curr.Close > curr.MA20 {
		signals = append(signals, map[string]interface{}{
			"signal": "buy_1",
			"desc":   "价格突破MA20（葛兰碧买入信号1）",
			"date":   curr.Date,
		})
	}

	// Buy 2: price pulls back to MA20 and bounces (回踩)
	// Price was above MA20, dipped to touch MA20 (within 1%), now above again
	if prev.Close > prev.MA20 && curr.Close > curr.MA20 {
		touch := math.Abs(curr.Low-curr.MA20)/curr.MA20 < 0.01 || math.Abs(prev.Low-prev.MA20)/prev.MA20 < 0.01
		if touch {
			signals = append(signals, map[string]interface{}{
				"signal": "buy_2",
				"desc":   "价格回踩MA20后反弹（葛兰碧买入信号2）",
				"date":   curr.Date,
			})
		}
	}

	// Buy 3: price falls below MA20 but quickly recovers (假跌破)
	if prev.Close < prev.MA20 && curr.Close > curr.MA20 {
		if prev.Close/prev.MA20 > 0.97 { // was only slightly below
			signals = append(signals, map[string]interface{}{
				"signal": "buy_3",
				"desc":   "价格假跌破MA20后快速收回（葛兰碧买入信号3）",
				"date":   curr.Date,
			})
		}
	}

	// Sell 1: price crosses below MA20 from above (跌破)
	if prev.Close >= prev.MA20 && curr.Close < curr.MA20 {
		signals = append(signals, map[string]interface{}{
			"signal": "sell_1",
			"desc":   "价格跌破MA20（葛兰碧卖出信号1）",
			"date":   curr.Date,
		})
	}

	// Sell 2: price rallies to MA20 and gets rejected (反弹受阻)
	if prev.Close < prev.MA20 && curr.Close < curr.MA20 {
		touch := math.Abs(curr.High-curr.MA20)/curr.MA20 < 0.01 || math.Abs(prev.High-prev.MA20)/prev.MA20 < 0.01
		if touch {
			signals = append(signals, map[string]interface{}{
				"signal": "sell_2",
				"desc":   "价格反弹至MA20受阻（葛兰碧卖出信号2）",
				"date":   curr.Date,
			})
		}
	}

	return signals
}

// classifyADX returns numeric ADX and a label.
func classifyADX(r marketRow) (float64, string) {
	adx := r.ADX
	switch {
	case adx < 20:
		return adx, "weak"
	case adx < 25:
		return adx, "developing"
	case adx < 50:
		return adx, "strong"
	default:
		return adx, "very_strong"
	}
}

// classifySupertrend returns direction label and value.
func classifySupertrend(r marketRow) (string, float64) {
	if r.STDir > 0 {
		return "bullish", r.STVal
	}
	if r.STDir < 0 {
		return "bearish", r.STVal
	}
	return "neutral", r.STVal
}

// buildSummary produces the final verdict.
func buildSummary(
	dowDir, alignment, priceVsMA20 string,
	crossovers []map[string]interface{},
	granville []map[string]interface{},
	strengthLabel string,
	stDir string,
	latest marketRow,
) (verdict string, confidence float64, keySignals []string) {

	bullScore := 0
	bearScore := 0
	totalSignals := 0

	if dowDir == "uptrend" {
		bullScore += 2
	} else if dowDir == "downtrend" {
		bearScore += 2
	}
	totalSignals++

	if alignment == "bullish" {
		bullScore += 2
	} else if alignment == "bearish" {
		bearScore += 2
	}
	totalSignals++

	if priceVsMA20 == "above" {
		bullScore++
	} else if priceVsMA20 == "below" {
		bearScore++
	}
	totalSignals++

	if stDir == "bullish" {
		bullScore++
	} else if stDir == "bearish" {
		bearScore++
	}
	totalSignals++

	for _, c := range crossovers {
		if c["type"] == "golden_cross" {
			bullScore++
			keySignals = append(keySignals, fmt.Sprintf("%v", c["desc"]))
		} else {
			bearScore++
			keySignals = append(keySignals, fmt.Sprintf("%v", c["desc"]))
		}
	}

	for _, g := range granville {
		sig, _ := g["signal"].(string)
		desc, _ := g["desc"].(string)
		if sig == "buy_1" || sig == "buy_2" || sig == "buy_3" {
			bullScore++
		} else {
			bearScore++
		}
		keySignals = append(keySignals, desc)
	}

	// Add ADX strength to key signals
	keySignals = append(keySignals, fmt.Sprintf("ADX=%.1f（%s）", latest.ADX, strengthLabel))
	if latest.DIPlus > 0 && latest.DIMinus > 0 {
		if latest.DIPlus > latest.DIMinus {
			keySignals = append(keySignals, fmt.Sprintf("DI+>DI-（%.1f>%.1f）多方占优", latest.DIPlus, latest.DIMinus))
		} else {
			keySignals = append(keySignals, fmt.Sprintf("DI+<DI-（%.1f<%.1f）空方占优", latest.DIPlus, latest.DIMinus))
		}
	}

	maxScore := bullScore + bearScore
	if maxScore == 0 {
		return "中性", 0.5, keySignals
	}

	diff := float64(bullScore - bearScore)
	confidence = math.Abs(diff) / float64(maxScore)
	if confidence > 1.0 {
		confidence = 1.0
	}
	confidence = math.Round(confidence*100) / 100
	// Scale confidence to a reasonable range [0.3, 0.95]
	confidence = 0.3 + confidence*0.65

	switch {
	case bullScore > bearScore+2:
		verdict = "看多"
	case bearScore > bullScore+2:
		verdict = "看空"
	default:
		verdict = "中性"
	}

	return verdict, confidence, keySignals
}

// round2 rounds to 2 decimal places.
func round2(v float64) float64 {
	return math.Round(v*100) / 100
}
