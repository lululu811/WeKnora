package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"math"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type ChartPatternTool struct {
	config *hithink_finance.Config
}

func NewChartPatternTool(config *hithink_finance.Config) *ChartPatternTool {
	return &ChartPatternTool{config: config}
}

func (t *ChartPatternTool) Name() string { return "hithink.finance.analysis.pattern" }

func (t *ChartPatternTool) Description() string {
	return `图表形态识别工具。基于经典技术分析理论，自动识别重要图表形态。

检测形态：
- 反转形态：头肩顶/底、双顶/底
- 持续形态：三角形整理（收敛区间）、楔形、旗形
- 蜡烛图形态：Hammer、Shooting Star、Doji、Engulfing、Morning/Evening Star 等
- 布林带形态：收口（变盘前兆）、触轨（超买超卖）
- 突破信号：价格突破形态边界 + 量能确认

使用示例：thscode="600519.SH", days=120`
}

func (t *ChartPatternTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺代码，如 600519.SH",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "分析天数（默认 120，最大 250）",
				"default":     120,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ChartPatternTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
	if params.Days > 250 {
		params.Days = 250
	}

	rows, err := FetchMarketData(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}
	if len(rows) < 10 {
		return &types.ToolResult{Success: false, Error: "数据不足（需要至少 10 个交易日）"}, nil
	}

	var patterns []map[string]interface{}

	// ── Head & Shoulders ──
	if p := detectHeadAndShoulders(rows); p != nil {
		patterns = append(patterns, p)
	}

	// ── Double Top/Bottom ──
	if p := detectDoubleTopBottom(rows); p != nil {
		patterns = append(patterns, p)
	}

	// ── Triangle ──
	if p := detectTriangle(rows); p != nil {
		patterns = append(patterns, p)
	}

	// ── Wedge ──
	if p := detectWedge(rows); p != nil {
		patterns = append(patterns, p)
	}

	// ── Flag ──
	if p := detectFlag(rows); p != nil {
		patterns = append(patterns, p)
	}

	// ── Candlestick signals ──
	candleSignals := detectCandlesticks(rows)

	// ── Bollinger ─
	bbStatus := analyzeBollinger(rows)

	// ── Summary ──
	bullCount, bearCount := 0, 0
	for _, p := range patterns {
		if p["direction"] == "bullish" {
			bullCount++
		} else {
			bearCount++
		}
	}
	verdict := "中性"
	if bullCount > bearCount {
		verdict = "偏多"
	} else if bearCount > bullCount {
		verdict = "偏空"
	}

	output := map[string]interface{}{
		"thscode":             params.Thscode,
		"patterns":            patterns,
		"candlestick_signals": candleSignals,
		"bollinger_status":    bbStatus,
		"summary": map[string]interface{}{
			"total_patterns": len(patterns),
			"bullish":        bullCount,
			"bearish":        bearCount,
			"verdict":        verdict,
		},
	}
	out, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(out)}, nil
}

var _ types.Tool = (*ChartPatternTool)(nil)

// ─── Pattern Detectors ─────────────────────────────────────────────────

func detectHeadAndShoulders(rows []marketRow) map[string]interface{} {
	highs, lows := findSwings(rows, 8)
	if len(highs) < 3 || len(lows) < 2 {
		return nil
	}

	// Check the 3 most recent swing highs for H&S top
	h1, h2, h3 := rows[highs[0]].High, rows[highs[1]].High, rows[highs[2]].High
	// h1 is most recent. Head should be the middle one chronologically = h2
	// But our highs are newest-first, so h1=right_shoulder, h2=head, h3=left_shoulder
	head, ls, rs := h2, h3, h1
	if head <= ls || head <= rs {
		return nil
	}
	// Check shoulder symmetry (within 5%)
	avgShoulder := (ls + rs) / 2
	if math.Abs(ls-rs)/avgShoulder > 0.05 {
		return nil
	}

	// Neckline: the two reaction lows that bracket the head, i.e. the lows at
	// the left shoulder (highs[2]) and the right shoulder (highs[0]).
	valleyLeft := rows[highs[2]].Low
	valleyRight := rows[highs[0]].Low
	neckline := (valleyLeft + valleyRight) / 2

	// Measured-move target: the neckline minus the head-to-neckline height.
	// The old expression was `head - (head - neckline)`, which algebraically
	// collapses to `neckline` — the "target" was always the neckline.
	target := neckline - (head - neckline)
	confidence := 0.6
	if rows[0].Close < neckline {
		confidence += 0.2
	} // neckline broken

	return map[string]interface{}{
		"name":       "头肩顶",
		"type":       "reversal",
		"direction":  "bearish",
		"confidence": confidence,
		"key_levels": map[string]float64{
			"left_shoulder": ls, "head": head, "right_shoulder": rs,
			"neckline": neckline, "target": target,
		},
		"volume_confirm": rows[0].Vol < rows[highs[2]].Vol,
		"desc":           fmt.Sprintf("头肩顶：左肩=%.2f 头=%.2f 右肩=%.2f 颈线=%.2f 目标=%.2f", ls, head, rs, neckline, target),
	}
}

func detectDoubleTopBottom(rows []marketRow) map[string]interface{} {
	highs, lows := findSwings(rows, 8)

	// Double top: two recent swing highs within 3%
	if len(highs) >= 2 {
		p1, p2 := rows[highs[0]].High, rows[highs[1]].High
		avg := (p1 + p2) / 2
		if avg > 0 && math.Abs(p1-p2)/avg < 0.03 {
			valley := rows[highs[0]].Low
			neckline := valley
			target := neckline - (avg - neckline)
			return map[string]interface{}{
				"name": "双顶", "type": "reversal", "direction": "bearish", "confidence": 0.65,
				"key_levels":     map[string]float64{"peak1": p1, "peak2": p2, "neckline": neckline, "target": target},
				"volume_confirm": rows[0].Vol < rows[highs[1]].Vol,
				"desc":           fmt.Sprintf("双顶：顶1=%.2f 顶2=%.2f 颈线=%.2f", p1, p2, neckline),
			}
		}
	}
	// Double bottom: two recent swing lows within 3%
	if len(lows) >= 2 {
		p1, p2 := rows[lows[0]].Low, rows[lows[1]].Low
		avg := (p1 + p2) / 2
		if avg > 0 && math.Abs(p1-p2)/avg < 0.03 {
			peak := rows[lows[0]].High
			neckline := peak
			target := neckline + (neckline - avg)
			return map[string]interface{}{
				"name": "双底", "type": "reversal", "direction": "bullish", "confidence": 0.65,
				"key_levels":     map[string]float64{"bottom1": p1, "bottom2": p2, "neckline": neckline, "target": target},
				"volume_confirm": rows[0].Vol > rows[lows[1]].Vol,
				"desc":           fmt.Sprintf("双底：底1=%.2f 底2=%.2f 颈线=%.2f", p1, p2, neckline),
			}
		}
	}
	return nil
}

func detectTriangle(rows []marketRow) map[string]interface{} {
	n := minInt(30, len(rows))
	if n < 15 {
		return nil
	}
	segment := rows[:n]

	var upperSlope, lowerSlope float64
	var highs, lows []float64
	for i := 0; i < n; i += 5 {
		highs = append(highs, segment[i].High)
		lows = append(lows, segment[i].Low)
	}
	if len(highs) < 3 {
		return nil
	}
	for i := 1; i < len(highs); i++ {
		upperSlope += highs[i] - highs[i-1]
		lowerSlope += lows[i] - lows[i-1]
	}

	upperSlope /= float64(len(highs) - 1)
	lowerSlope /= float64(len(lows) - 1)

	// Symmetrical triangle: both converging
	if upperSlope < -0.1 && lowerSlope > 0.1 {
		return map[string]interface{}{
			"name": "对称三角形", "type": "continuation", "direction": "neutral", "confidence": 0.6,
			"desc": fmt.Sprintf("高点递减(斜率%.3f) + 低点递增(斜率%.3f)，收敛整理中", upperSlope, lowerSlope),
		}
	}
	// Ascending triangle: flat highs + rising lows
	if math.Abs(upperSlope) < 0.3 && lowerSlope > 0.1 {
		return map[string]interface{}{
			"name": "上升三角形", "type": "continuation", "direction": "bullish", "confidence": 0.65,
			"desc": fmt.Sprintf("高点持平 + 低点递增(斜率%.3f)，看涨突破形态", lowerSlope),
		}
	}
	// Descending triangle: falling highs + flat lows
	if upperSlope < -0.1 && math.Abs(lowerSlope) < 0.3 {
		return map[string]interface{}{
			"name": "下降三角形", "type": "continuation", "direction": "bearish", "confidence": 0.65,
			"desc": fmt.Sprintf("高点递减(斜率%.3f) + 低点持平，看跌突破形态", upperSlope),
		}
	}
	return nil
}

func detectWedge(rows []marketRow) map[string]interface{} {
	n := minInt(25, len(rows))
	if n < 12 {
		return nil
	}

	var peakTrend, troughTrend float64
	for i := 0; i < n-1; i += 3 {
		peakTrend += rows[i].High - rows[i+1].High
		troughTrend += rows[i].Low - rows[i+1].Low
	}
	peaks := n / 3

	// Sign convention: rows are newest-first, so `rows[i] - rows[i+1]` is
	// (newer - older). A POSITIVE sum therefore means highs and lows are RISING.
	// The old code mapped positive -> "下降楔形/bullish", labelling every clean
	// uptrend as a falling wedge and saying so in the desc.
	nearSpan := 0.0
	for i := 0; i < 5 && i < n; i++ {
		if i == 0 {
			nearSpan = rows[i].High - rows[i].Low
		}
	}
	farSpan := 0.0
	for i := 5; i < 12 && i < n; i++ {
		if i == 5 {
			farSpan = rows[i].High - rows[i].Low
		}
	}
	converging := farSpan > 0 && nearSpan < farSpan
	conf := 0.55
	convergeNote := "但未见收敛"
	if converging {
		conf = 0.65
		convergeNote = "且振幅收敛"
	}

	// Rising wedge: highs and lows both rising -> bearish reversal.
	if peakTrend > 0 && troughTrend > 0 {
		return map[string]interface{}{
			"name": "上升楔形", "type": "reversal", "direction": "bearish", "confidence": conf,
			"desc": fmt.Sprintf("高点和低点同时上升（高点斜率%.3f，低点斜率%.3f）%s → 上涨动能衰减，看跌反转形态", peakTrend, troughTrend, convergeNote),
		}
	}
	// Falling wedge: highs and lows both falling -> bullish reversal.
	if peakTrend < 0 && troughTrend < 0 {
		return map[string]interface{}{
			"name": "下降楔形", "type": "reversal", "direction": "bullish", "confidence": conf,
			"desc": fmt.Sprintf("高点和低点同时下降（高点斜率%.3f，低点斜率%.3f）%s → 下跌动能衰减，看涨反转形态", peakTrend, troughTrend, convergeNote),
		}
	}
	_ = peaks
	return nil
}

func detectFlag(rows []marketRow) map[string]interface{} {
	// A flag is a sharp pole followed by a drift.
	//
	// Row order matters: rows are NEWEST-first, so the pole is the OLDER
	// segment (rows[consolidationLen:]) and the drift is the RECENT one
	// (rows[:consolidationLen]). The old code had the two swapped, so it
	// searched for "recent impulse + older drift" — the exact inverse of a
	// flag — and matched none of the canonical shapes.
	if len(rows) < 15 {
		return nil
	}

	for consolidationLen := 3; consolidationLen <= 10; consolidationLen++ {
		consolidation := rows[:consolidationLen]
		pole := rows[consolidationLen:]
		if len(pole) < 2 {
			continue
		}

		poleStart, poleEnd := pole[len(pole)-1], pole[0]
		if poleStart.Close <= 0 {
			continue
		}
		changePct := (poleEnd.Close - poleStart.Close) / poleStart.Close * 100
		if math.Abs(changePct) < 5 {
			continue
		}

		// Net drift across the consolidation, as a fraction of its oldest close.
		first := consolidation[len(consolidation)-1].Close
		if first <= 0 {
			continue
		}
		consChange := (consolidation[0].Close - first) / first

		if changePct > 0 && consChange > -0.03 && consChange < 0.03 {
			return map[string]interface{}{
				"name": "牛市旗形", "type": "continuation", "direction": "bullish", "confidence": 0.55,
				"desc": fmt.Sprintf("%.1f%% 急涨后横向整理%d日（净漂移%+.2f%%），看涨中继形态", changePct, consolidationLen, consChange*100),
			}
		}
		if changePct < 0 && consChange > -0.03 && consChange < 0.03 {
			return map[string]interface{}{
				"name": "熊市旗形", "type": "continuation", "direction": "bearish", "confidence": 0.55,
				"desc": fmt.Sprintf("%.1f%% 急跌后横向整理%d日（净漂移%+.2f%%），看跌中继形态", changePct, consolidationLen, consChange*100),
			}
		}
	}
	return nil
}

func detectCandlesticks(rows []marketRow) []map[string]interface{} {
	if len(rows) == 0 {
		return nil
	}
	r := rows[0]
	var signals []map[string]interface{}

	candleMap := map[float64]struct {
		name   string
		signal string
		desc   string
	}{
		r.CdlHammer:       {"锤子线", "bullish", "下影线长，潜在底部反转"},
		r.CdlShootingStar: {"流星线", "bearish", "上影线长，潜在顶部反转"},
		r.CdlDoji:         {"十字星", "neutral", "多空平衡，变盘信号"},
		r.CdlEngulfing:    {"看涨吞没", "bullish", "阳线吞没前日阴线"},
		r.CdlHarami:       {"孕线", "neutral", "趋势放缓"},
		r.CdlMorningStar:  {"晨星", "bullish", "底部反转形态"},
		r.CdlEveningStar:  {"暮星", "bearish", "顶部反转形态"},
		r.CdlPiercing:     {"刺透线", "bullish", "看涨刺透形态"},
		r.CdlDarkCloud:    {"乌云盖顶", "bearish", "看跌乌云形态"},
		r.Cdl3WhiteSold:   {"三白兵", "bullish", "连续三阳，强势上涨"},
		r.Cdl3BlackCrows:  {"三乌鸦", "bearish", "连续三阴，强势下跌"},
	}

	for val, info := range candleMap {
		if val != 0 {
			signals = append(signals, map[string]interface{}{
				"name": info.name, "signal": info.signal, "date": r.Date, "desc": info.desc,
			})
		}
	}
	return signals
}

func analyzeBollinger(rows []marketRow) map[string]interface{} {
	if len(rows) < 5 {
		return nil
	}
	r := rows[0]

	// Width trend
	var widths []float64
	for i := 0; i < 5 && i < len(rows); i++ {
		w := rows[i].BBUpper - rows[i].BBLower
		if w > 0 {
			widths = append(widths, w)
		}
	}
	widthTrend := "stable"
	if len(widths) >= 3 {
		if widths[0] < widths[len(widths)-1]*0.7 {
			widthTrend = "narrowing" // squeeze
		} else if widths[0] > widths[len(widths)-1]*1.3 {
			widthTrend = "expanding"
		}
	}

	// Position
	position := "middle"
	if r.Close >= r.BBUpper {
		position = "upper"
	}
	if r.Close <= r.BBLower {
		position = "lower"
	}

	// Band walk: consecutive days at/above upper or at/below lower
	upperWalk, lowerWalk := 0, 0
	for _, row := range rows {
		if row.Close >= row.BBUpper {
			upperWalk++
			lowerWalk = 0
		}
		if row.Close <= row.BBLower {
			lowerWalk++
			upperWalk = 0
		}
	}

	squeeze := widthTrend == "narrowing"
	return map[string]interface{}{
		"upper": r.BBUpper, "middle": r.BBMID, "lower": r.BBLower,
		"position":        position,
		"width_trend":     widthTrend,
		"squeeze":         squeeze,
		"upper_band_walk": upperWalk,
		"lower_band_walk": lowerWalk,
		"desc": func() string {
			if squeeze {
				return "布林带收口，变盘前兆"
			}
			if upperWalk >= 3 {
				return fmt.Sprintf("连续%d日触及上轨，强势运行", upperWalk)
			}
			if lowerWalk >= 3 {
				return fmt.Sprintf("连续%d日触及下轨，弱势运行", lowerWalk)
			}
			return fmt.Sprintf("价格位于布林带%s区域", position)
		}(),
	}
}
