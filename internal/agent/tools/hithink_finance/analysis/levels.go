package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"sort"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// LevelsAnalysisTool identifies support and resistance levels using multiple
// classical techniques: pivot points, Fibonacci retracement, swing highs/lows,
// moving averages, round numbers, Bollinger bands and VWAP, then highlights
// price zones where several indicators converge.
type LevelsAnalysisTool struct {
	config *hithink_finance.Config
}

// NewLevelsAnalysisTool constructs a LevelsAnalysisTool.
func NewLevelsAnalysisTool(config *hithink_finance.Config) *LevelsAnalysisTool {
	return &LevelsAnalysisTool{config: config}
}

func (t *LevelsAnalysisTool) Name() string { return "hithink.finance.analysis.levels" }

func (t *LevelsAnalysisTool) Description() string {
	return `支撑阻力位分析工具。自动识别关键价格位，辅助买卖点判断。

分析内容：
- 枢轴点（Pivot Points）：基于最新交易日的 H/L/C 计算 R1/R2/R3 和 S1/S2/S3
- 斐波那契回撤：基于近期波段高低点计算 23.6%/38.2%/50%/61.8%/78.6% 回撤位
- 前高前低：近期显著高低点作为支撑阻力
- 均线支撑阻力：MA5/10/20/60/120/250 作为动态支撑阻力
- 整数关口：心理价位（如 1300、1250 等）
- 布林带边界：BB Upper/Lower 作为动态阻力支撑
- VWAP：机构成本线

使用示例：thscode="600519.SH", days=120`
}

func (t *LevelsAnalysisTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "分析回溯天数（默认 120，最大 250）",
				"default":     120,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

// pivotPoints holds classic pivot point levels.
type pivotPoints struct {
	PP float64 `json:"pp"`
	R1 float64 `json:"r1"`
	R2 float64 `json:"r2"`
	R3 float64 `json:"r3"`
	S1 float64 `json:"s1"`
	S2 float64 `json:"s2"`
	S3 float64 `json:"s3"`
}

// fibResult holds the Fibonacci retracement output.
type fibResult struct {
	High   float64            `json:"high"`
	Low    float64            `json:"low"`
	Levels map[string]float64 `json:"levels"`
}

// swingLevel is a single swing high/low entry.
type swingLevel struct {
	Price float64 `json:"price"`
	Date  string  `json:"date"`
	Type  string  `json:"type"`
}

// confluenceZone is a price cluster where multiple indicators converge.
type confluenceZone struct {
	Price    float64  `json:"price"`
	Levels   []string `json:"levels"`
	Strength string   `json:"strength"`
}

// nearestLevel reports the closest support or resistance.
type nearestLevel struct {
	Price float64 `json:"price"`
	Type  string  `json:"type"`
}

func (t *LevelsAnalysisTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 120
	}
	if params.Days > 250 {
		params.Days = 250
	}

	rows, err := FetchMarketData(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}
	if len(rows) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到数据：%s", params.Thscode)}, nil
	}

	latest := rows[0]
	price := latest.Close
	if price <= 0 {
		return &types.ToolResult{Success: false, Error: "最新收盘价为 0，数据可能不完整"}, nil
	}

	// --- Pivot Points -------------------------------------------------------
	pivot := calcPivotPoints(latest.High, latest.Low, latest.Close)

	// --- Fibonacci Retracement ----------------------------------------------
	hhIdx, llIdx := 0, 0
	for i, r := range rows {
		if r.High > rows[hhIdx].High {
			hhIdx = i
		}
		if r.Low < rows[llIdx].Low {
			llIdx = i
		}
	}
	fibHigh := rows[hhIdx].High
	fibLow := rows[llIdx].Low
	// downtrend when current price sits below the period high
	fib := calcFibonacci(fibHigh, fibLow, price < fibHigh)

	// --- Swing highs / lows -------------------------------------------------
	swingHighIdx, swingLowIdx := findSwings(rows, 5)
	var resistSwings, supportSwings []swingLevel
	for i := 0; i < len(swingHighIdx) && i < 5; i++ {
		idx := swingHighIdx[i]
		resistSwings = append(resistSwings, swingLevel{
			Price: rows[idx].High, Date: rows[idx].Date, Type: "swing_high",
		})
	}
	for i := 0; i < len(swingLowIdx) && i < 5; i++ {
		idx := swingLowIdx[i]
		supportSwings = append(supportSwings, swingLevel{
			Price: rows[idx].Low, Date: rows[idx].Date, Type: "swing_low",
		})
	}

	// --- Moving-Average Levels ----------------------------------------------
	maLevels := map[string]float64{
		"ma5":   latest.MA5,
		"ma10":  latest.MA10,
		"ma20":  latest.MA20,
		"ma60":  latest.MA60,
		"ma120": latest.MA120,
		"ma250": latest.MA250,
	}

	// --- Round Numbers ------------------------------------------------------
	roundAbove, roundBelow := roundNumbers(price)

	// --- Bollinger & VWAP ---------------------------------------------------
	bollinger := map[string]float64{
		"upper": latest.BBUpper,
		"lower": latest.BBLower,
	}
	vwap := latest.VWAP

	// --- Collect all candidate levels for confluence & nearest-S/R ----------
	var allLevels []namedLevel

	addLevel := func(name string, v float64) {
		if v > 0 {
			allLevels = append(allLevels, namedLevel{name, v})
		}
	}
	addLevel("Pivot_PP", pivot.PP)
	addLevel("Pivot_R1", pivot.R1)
	addLevel("Pivot_R2", pivot.R2)
	addLevel("Pivot_R3", pivot.R3)
	addLevel("Pivot_S1", pivot.S1)
	addLevel("Pivot_S2", pivot.S2)
	addLevel("Pivot_S3", pivot.S3)
	for pct, val := range fib.Levels {
		addLevel(fmt.Sprintf("Fib_%s%%", pct), val)
	}
	for _, s := range resistSwings {
		addLevel("SwingHigh_"+s.Date, s.Price)
	}
	for _, s := range supportSwings {
		addLevel("SwingLow_"+s.Date, s.Price)
	}
	for k, v := range maLevels {
		addLevel("MA_"+k, v)
	}
	for _, v := range roundAbove {
		addLevel(fmt.Sprintf("Round_%g", v), v)
	}
	for _, v := range roundBelow {
		addLevel(fmt.Sprintf("Round_%g", v), v)
	}
	addLevel("BB_Upper", latest.BBUpper)
	addLevel("BB_Lower", latest.BBLower)
	addLevel("VWAP", latest.VWAP)

	// Cluster nearby levels into confluence zones.
	tol := latest.ATR
	if tol <= 0 || tol < price*0.005 {
		tol = price * 0.01
	}
	zones := clusterLevels(allLevels, tol)

	// Nearest support (highest level below price) and resistance (lowest above).
	nearestSup, nearestRes := findNearest(allLevels, price)

	// Summary verdict.
	verdict := generateVerdict(price, pivot, maLevels, nearestSup, nearestRes)

	output := map[string]interface{}{
		"thscode":       params.Thscode,
		"current_price": price,
		"pivot_points":  pivot,
		"fibonacci":     fib,
		"swing_levels": map[string]interface{}{
			"resistance": resistSwings,
			"support":    supportSwings,
		},
		"ma_levels":        maLevels,
		"round_numbers":    map[string]interface{}{"above": roundAbove, "below": roundBelow},
		"bollinger":        bollinger,
		"vwap":             vwap,
		"confluence_zones": zones,
		"nearest": map[string]interface{}{
			"support":    nearestSup,
			"resistance": nearestRes,
		},
		"summary": verdict,
	}

	outJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outJSON)}, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// namedLevel pairs a label with a price for clustering / nearest-level logic.
type namedLevel struct {
	name  string
	price float64
}

// calcPivotPoints computes classic floor-trader pivot levels.
func calcPivotPoints(h, l, c float64) pivotPoints {
	pp := (h + l + c) / 3
	r := h - l
	return pivotPoints{
		PP: pp,
		R1: 2*pp - l,
		R2: pp + r,
		R3: h + 2*(pp-l),
		S1: 2*pp - h,
		S2: pp - r,
		S3: l - 2*(h-pp),
	}
}

// calcFibonacci computes the standard retracement levels.
// When trendDown is true the reference move is high→low, otherwise low→high.
func calcFibonacci(high, low float64, trendDown bool) fibResult {
	pcts := []struct {
		label string
		f     float64
	}{
		{"23.6", 0.236},
		{"38.2", 0.382},
		{"50.0", 0.500},
		{"61.8", 0.618},
		{"78.6", 0.786},
	}
	span := high - low
	levels := make(map[string]float64, len(pcts))
	for _, p := range pcts {
		var v float64
		if trendDown {
			// Retracement rally from low towards high.
			v = high - span*p.f
		} else {
			// Pull-back from high towards low.
			v = low + span*p.f
		}
		levels[p.label] = v
	}
	return fibResult{High: high, Low: low, Levels: levels}
}

// roundNumbers returns the two nearest round-number levels above and below
// price. The granularity depends on price magnitude (psychological levels).
func roundNumbers(price float64) (above, below []float64) {
	var step float64
	switch {
	case price > 1000:
		step = 50
	case price > 100:
		step = 10
	case price > 10:
		step = 5
	default:
		step = 1
	}
	lower := math.Floor(price/step) * step
	upper := lower + step
	for v := lower; v >= lower-step && len(below) < 2; v -= step {
		if v < price {
			below = append(below, v)
		}
	}
	for v := upper; v <= upper+step && len(above) < 2; v += step {
		if v > price {
			above = append(above, v)
		}
	}
	return
}

// clusterLevels groups named levels whose prices lie within `tol` of each
// other and returns the clusters that contain at least two members.
func clusterLevels(levels []namedLevel, tol float64) []confluenceZone {
	if len(levels) < 2 {
		return nil
	}
	// Work on a copy we can sort.
	sorted := make([]namedLevel, len(levels))
	copy(sorted, levels)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].price < sorted[j].price })

	var zones []confluenceZone
	cluster := []int{0}
	for i := 1; i < len(sorted); i++ {
		if sorted[i].price-sorted[cluster[0]].price <= tol {
			cluster = append(cluster, i)
		} else {
			if len(cluster) >= 2 {
				zones = append(zones, buildZone(sorted, cluster))
			}
			cluster = []int{i}
		}
	}
	if len(cluster) >= 2 {
		zones = append(zones, buildZone(sorted, cluster))
	}

	sort.Slice(zones, func(i, j int) bool {
		if len(zones[i].Levels) != len(zones[j].Levels) {
			return len(zones[i].Levels) > len(zones[j].Levels)
		}
		return zones[i].Price < zones[j].Price
	})
	return zones
}

// buildZone creates a confluenceZone from a list of indices into `sorted`.
func buildZone(sorted []namedLevel, idx []int) confluenceZone {
	var sum float64
	names := make([]string, 0, len(idx))
	for _, i := range idx {
		sum += sorted[i].price
		names = append(names, sorted[i].name)
	}
	strength := "medium"
	if len(idx) >= 3 {
		strength = "strong"
	}
	return confluenceZone{
		Price:    sum / float64(len(idx)),
		Levels:   names,
		Strength: strength,
	}
}

// findNearest returns the closest level below price (support) and above
// price (resistance). Either may be nil when no level exists on that side.
func findNearest(levels []namedLevel, price float64) (*nearestLevel, *nearestLevel) {
	var sup, res *nearestLevel
	for _, lv := range levels {
		if lv.price < price {
			if sup == nil || lv.price > sup.Price {
				sup = &nearestLevel{Price: lv.price, Type: lv.name}
			}
		} else if lv.price > price {
			if res == nil || lv.price < res.Price {
				res = &nearestLevel{Price: lv.price, Type: lv.name}
			}
		}
	}
	return sup, res
}

// generateVerdict produces a short textual summary based on price position.
func generateVerdict(price float64, pivot pivotPoints, maLevels map[string]float64, sup, res *nearestLevel) map[string]interface{} {
	var keyLevels []string
	if sup != nil {
		keyLevels = append(keyLevels, fmt.Sprintf("近端支撑 %s @ %.2f", sup.Type, sup.Price))
	}
	if res != nil {
		keyLevels = append(keyLevels, fmt.Sprintf("近端阻力 %s @ %.2f", res.Type, res.Price))
	}

	aboveMAs, belowMAs := 0, 0
	for _, v := range maLevels {
		if v <= 0 {
			continue
		}
		if price >= v {
			aboveMAs++
		} else {
			belowMAs++
		}
	}

	verdict := ""
	switch {
	case aboveMAs >= 4:
		verdict = "多头排列，价格站稳大多数均线之上，偏强运行"
	case belowMAs >= 4:
		verdict = "空头排列，价格运行于大多数均线之下，偏弱整理"
	default:
		verdict = "均线交织，方向不明，关注关键支撑阻力位突破情况"
	}
	if price > pivot.PP {
		verdict += "；当前位于枢轴点上方，短期偏多"
	} else {
		verdict += "；当前位于枢轴点下方，短期偏空"
	}

	return map[string]interface{}{
		"verdict":    verdict,
		"key_levels": keyLevels,
	}
}

// Compile-time interface check.
var _ types.Tool = (*LevelsAnalysisTool)(nil)
