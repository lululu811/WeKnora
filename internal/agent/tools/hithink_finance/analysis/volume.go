package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"math"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// VolumeAnalysisTool performs Wyckoff-based volume-price analysis.
type VolumeAnalysisTool struct {
	config *hithink_finance.Config
}

// NewVolumeAnalysisTool creates a new VolumeAnalysisTool.
func NewVolumeAnalysisTool(config *hithink_finance.Config) *VolumeAnalysisTool {
	return &VolumeAnalysisTool{config: config}
}

func (t *VolumeAnalysisTool) Name() string {
	return "hithink.finance.analysis.volume"
}

func (t *VolumeAnalysisTool) Description() string {
	return `量价分析工具。基于威科夫方法和量价关系理论，分析资金流向和主力行为。

分析内容：
- 威科夫阶段判断：吸筹/派发/上涨(Markup)/下跌(Markdown)
- 量价关系：量价齐升、量价背离、缩量回调、放量突破
- 假突破检测：Spring(向下假突破)/Upthrust(向上假突破)
- OBV 能量潮：趋势方向、与价格背离
- 资金流向：CMF/MFI 多空力量分析
- VWAP 偏离：价格相对 VWAP 的位置

使用示例：thscode="600519.SH", days=60`
}

func (t *VolumeAnalysisTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "查询天数（默认 60，最大 250）",
				"default":     60,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VolumeAnalysisTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
		params.Days = 60
	}
	if params.Days > 250 {
		params.Days = 250
	}

	rows, err := FetchMarketData(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if len(rows) < 10 {
		return &types.ToolResult{Success: false, Error: "数据不足：至少需要 10 天数据"}, nil
	}

	result := map[string]interface{}{
		"thscode":         params.Thscode,
		"wyckoff_phase":   t.analyzeWyckoffPhase(rows),
		"volume_price":    t.analyzeVolumePrice(rows),
		"spring_upthrust": t.analyzeSpringUpthrust(rows),
		"obv":             t.analyzeOBV(rows),
		"money_flow":      t.analyzeMoneyFlow(rows),
		"vwap":            t.analyzeVWAP(rows),
	}

	result["summary"] = t.generateSummary(result)

	outputJSON, _ := json.MarshalIndent(result, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

// analyzeWyckoffPhase detects Wyckoff market phase from recent 20-40 days.
func (t *VolumeAnalysisTool) analyzeWyckoffPhase(rows []marketRow) map[string]interface{} {
	n := minInt(40, len(rows))
	recent := rows[:n]

	var evidence []string
	phase := "neutral"
	confidence := 0.0

	priceRange := t.calcPriceRange(recent)
	volTrend := t.calcVolTrend(recent)
	cmfAvg := t.calcAvgCMF(recent)
	mfiAvg := t.calcAvgMFI(recent)
	obvTrend := t.calcOBVTrend(recent)

	isInRange := priceRange < 0.08
	isVolDeclining := volTrend < -0.1
	priceMakingHigherHighs := t.isMakingHigherHighs(recent)
	priceMakingLowerLows := t.isMakingLowerLows(recent)

	if isInRange && isVolDeclining && mfiAvg < 40 && cmfAvg > -0.05 && cmfAvg < 0.1 {
		phase = "accumulation"
		confidence = 0.7
		evidence = append(evidence, fmt.Sprintf("价格在区间内波动（幅度 %.1f%%），成交量萎缩", priceRange*100))
		evidence = append(evidence, fmt.Sprintf("MFI 均值 %.1f < 40，资金流出压力减轻", mfiAvg))
		if cmfAvg > 0 {
			evidence = append(evidence, fmt.Sprintf("CMF 均值 %.3f > 0，轻微资金流入", cmfAvg))
		}
	} else if isInRange && isVolDeclining && mfiAvg > 60 && cmfAvg < 0.05 && cmfAvg > -0.1 {
		phase = "distribution"
		confidence = 0.7
		evidence = append(evidence, fmt.Sprintf("价格在区间内波动（幅度 %.1f%%），成交量萎缩", priceRange*100))
		evidence = append(evidence, fmt.Sprintf("MFI 均值 %.1f > 60，资金流入动力衰减", mfiAvg))
		if cmfAvg < 0 {
			evidence = append(evidence, fmt.Sprintf("CMF 均值 %.3f < 0，轻微资金流出", cmfAvg))
		}
	} else if priceMakingHigherHighs && obvTrend > 0 && cmfAvg > 0 && mfiAvg > 50 {
		phase = "markup"
		confidence = 0.75
		evidence = append(evidence, "价格创出近期新高，上涨趋势确立")
		evidence = append(evidence, fmt.Sprintf("OBV 上升趋势，量价配合良好"))
		evidence = append(evidence, fmt.Sprintf("CMF 均值 %.3f > 0，资金持续流入", cmfAvg))
		evidence = append(evidence, fmt.Sprintf("MFI 均值 %.1f > 50，多方占优", mfiAvg))
	} else if priceMakingLowerLows && obvTrend < 0 && cmfAvg < 0 && mfiAvg < 50 {
		phase = "markdown"
		confidence = 0.75
		evidence = append(evidence, "价格创出近期新低，下跌趋势确立")
		evidence = append(evidence, fmt.Sprintf("OBV 下降趋势，量价配合向下"))
		evidence = append(evidence, fmt.Sprintf("CMF 均值 %.3f < 0，资金持续流出", cmfAvg))
		evidence = append(evidence, fmt.Sprintf("MFI 均值 %.1f < 50，空方占优", mfiAvg))
	} else {
		phase = "neutral"
		confidence = 0.5
		evidence = append(evidence, "未检测到明确的威科夫阶段，市场处于过渡期")
	}

	return map[string]interface{}{
		"phase":      phase,
		"confidence": confidence,
		"evidence":   evidence,
	}
}

// analyzeVolumePrice analyzes volume-price relationships for last 5-10 days.
func (t *VolumeAnalysisTool) analyzeVolumePrice(rows []marketRow) []map[string]interface{} {
	var results []map[string]interface{}
	n := minInt(10, len(rows)-1)

	for i := 0; i < n; i++ {
		curr := rows[i]
		prev := rows[i+1]

		priceChange := (curr.Close - prev.Close) / prev.Close
		volChange := 0.0
		if prev.Vol > 0 {
			volChange = (curr.Vol - prev.Vol) / prev.Vol
		}

		vol10Avg := t.calcVolAvg(rows, minInt(10, len(rows)), i+1)
		volRatio := 1.0
		if vol10Avg > 0 {
			volRatio = curr.Vol / vol10Avg
		}

		var vpType, desc string

		if priceChange > 0.01 && volChange > 0.1 {
			vpType = "量价齐升"
			desc = fmt.Sprintf("价格上涨 %.2f%%，成交量放大 %.1f%%，多方力量强劲",
				priceChange*100, volChange*100)
		} else if priceChange > 0.01 && volChange < -0.1 {
			vpType = "量价背离（顶背离）"
			desc = fmt.Sprintf("价格上涨 %.2f%%，但成交量萎缩 %.1f%%，上涨动能不足",
				priceChange*100, -volChange*100)
		} else if priceChange < -0.01 && volChange > 0.1 {
			vpType = "量价背离（底背离）"
			desc = fmt.Sprintf("价格下跌 %.2f%%，但成交量放大 %.1f%%，可能有资金承接",
				-priceChange*100, volChange*100)
		} else if priceChange < -0.005 && priceChange > -0.03 && volChange < -0.3 {
			vpType = "缩量回调"
			desc = fmt.Sprintf("价格小幅下跌 %.2f%%，成交量显著萎缩 %.1f%%，健康调整",
				priceChange*100, -volChange*100)
		} else if priceChange > 0.03 && volRatio > 1.5 {
			vpType = "放量突破"
			desc = fmt.Sprintf("价格大涨 %.2f%%，成交量是 10 日均量的 %.2f 倍，突破信号",
				priceChange*100, volRatio)
		}

		if vpType != "" {
			results = append(results, map[string]interface{}{
				"type": vpType,
				"date": curr.Date,
				"desc": desc,
			})
		}
	}

	return results
}

// analyzeSpringUpthrust detects Spring and Upthrust patterns.
func (t *VolumeAnalysisTool) analyzeSpringUpthrust(rows []marketRow) []map[string]interface{} {
	var results []map[string]interface{}
	n := minInt(20, len(rows)-1)

	for i := 0; i < n; i++ {
		curr := rows[i]
		window := minInt(10, len(rows)-i-1)

		lowestLow := math.MaxFloat64
		highestHigh := 0.0
		for j := i + 1; j <= i+window && j < len(rows); j++ {
			if rows[j].Low < lowestLow {
				lowestLow = rows[j].Low
			}
			if rows[j].High > highestHigh {
				highestHigh = rows[j].High
			}
		}

		vol10Avg := t.calcVolAvg(rows, minInt(10, len(rows)), i+1)
		volRatio := 1.0
		if vol10Avg > 0 {
			volRatio = curr.Vol / vol10Avg
		}

		if curr.Low < lowestLow && curr.Close > lowestLow && volRatio > 1.3 {
			results = append(results, map[string]interface{}{
				"type":         "spring",
				"date":         curr.Date,
				"price":        curr.Close,
				"support":      lowestLow,
				"volume_ratio": math.Round(volRatio*100) / 100,
				"desc":         fmt.Sprintf("向下假突破支撑位 %.2f，收盘价 %.2f 收回，成交量放大 %.2f 倍，Spring 信号", lowestLow, curr.Close, volRatio),
			})
		}

		if curr.High > highestHigh && curr.Close < highestHigh && volRatio > 1.3 {
			results = append(results, map[string]interface{}{
				"type":         "upthrust",
				"date":         curr.Date,
				"price":        curr.Close,
				"resistance":   highestHigh,
				"volume_ratio": math.Round(volRatio*100) / 100,
				"desc":         fmt.Sprintf("向上假突破阻力位 %.2f，收盘价 %.2f 回落，成交量放大 %.2f 倍，Upthrust 信号", highestHigh, curr.Close, volRatio),
			})
		}
	}

	return results
}

// analyzeOBV analyzes OBV trend and divergence.
func (t *VolumeAnalysisTool) analyzeOBV(rows []marketRow) map[string]interface{} {
	if len(rows) < 11 {
		return map[string]interface{}{
			"trend":      "unknown",
			"divergence": "none",
		}
	}

	currOBV := rows[0].OBV
	obv10DaysAgo := rows[10].OBV
	obvChange := currOBV - obv10DaysAgo

	trend := "rising"
	if obvChange < 0 {
		trend = "falling"
	}

	divergence := "none"
	if len(rows) >= 20 {
		priceNow := rows[0].Close
		price10Ago := rows[10].Close
		price20Ago := rows[minInt(19, len(rows)-1)].Close

		if priceNow > price10Ago && price10Ago > price20Ago && obvChange < 0 {
			divergence = "bearish"
		} else if priceNow < price10Ago && price10Ago < price20Ago && obvChange > 0 {
			divergence = "bullish"
		}
	}

	return map[string]interface{}{
		"trend":      trend,
		"divergence": divergence,
	}
}

// analyzeMoneyFlow analyzes CMF and MFI indicators.
func (t *VolumeAnalysisTool) analyzeMoneyFlow(rows []marketRow) map[string]interface{} {
	cmf := rows[0].CMF
	mfi := rows[0].MFI

	verdict := "neutral"
	if cmf > 0.1 {
		verdict = "inflow"
	} else if cmf < -0.1 {
		verdict = "outflow"
	}

	var status string
	if mfi < 20 {
		status = "超卖"
	} else if mfi > 80 {
		status = "超买"
	} else if mfi > 60 {
		status = "多方占优"
	} else if mfi < 40 {
		status = "空方占优"
	} else {
		status = "均衡"
	}

	return map[string]interface{}{
		"cmf":     math.Round(cmf*1000) / 1000,
		"mfi":     math.Round(mfi*10) / 10,
		"verdict": verdict,
		"status":  status,
	}
}

// analyzeVWAP analyzes price position relative to VWAP.
func (t *VolumeAnalysisTool) analyzeVWAP(rows []marketRow) map[string]interface{} {
	price := rows[0].Close
	vwap := rows[0].VWAP

	if vwap == 0 {
		return map[string]interface{}{
			"price":        price,
			"vwap":         0,
			"distance_pct": 0,
			"position":     "unknown",
		}
	}

	distancePct := (price - vwap) / vwap
	position := "above"
	if price < vwap {
		position = "below"
	}

	return map[string]interface{}{
		"price":        price,
		"vwap":         math.Round(vwap*100) / 100,
		"distance_pct": math.Round(distancePct*10000) / 100,
		"position":     position,
	}
}

// generateSummary creates a summary based on all analysis results.
func (t *VolumeAnalysisTool) generateSummary(result map[string]interface{}) map[string]interface{} {
	var keyFindings []string
	verdict := "观望"

	wyckoff := result["wyckoff_phase"].(map[string]interface{})
	phase := wyckoff["phase"].(string)

	if phase == "accumulation" {
		verdict = "关注吸筹信号，等待放量突破"
		keyFindings = append(keyFindings, "威科夫吸筹阶段，主力可能在底部建仓")
	} else if phase == "markup" {
		verdict = "上涨趋势，持有或逢低买入"
		keyFindings = append(keyFindings, "威科夫上涨阶段，趋势向上")
	} else if phase == "distribution" {
		verdict = "警惕派发信号，考虑减仓"
		keyFindings = append(keyFindings, "威科夫派发阶段，主力可能在顶部出货")
	} else if phase == "markdown" {
		verdict = "下跌趋势，回避或做空"
		keyFindings = append(keyFindings, "威科夫下跌阶段，趋势向下")
	}

	moneyFlow := result["money_flow"].(map[string]interface{})
	mfVerdict := moneyFlow["verdict"].(string)
	if mfVerdict == "inflow" {
		keyFindings = append(keyFindings, "资金持续流入，多方力量强劲")
	} else if mfVerdict == "outflow" {
		keyFindings = append(keyFindings, "资金持续流出，空方力量强劲")
	}

	obv := result["obv"].(map[string]interface{})
	if obv["divergence"].(string) == "bearish" {
		keyFindings = append(keyFindings, "OBV 与价格顶背离，警惕回调风险")
	} else if obv["divergence"].(string) == "bullish" {
		keyFindings = append(keyFindings, "OBV 与价格底背离，可能存在反弹机会")
	}

	vwap := result["vwap"].(map[string]interface{})
	if vwap["position"].(string) == "above" {
		keyFindings = append(keyFindings, "价格在 VWAP 上方，短期偏多")
	} else if vwap["position"].(string) == "below" {
		keyFindings = append(keyFindings, "价格在 VWAP 下方，短期偏空")
	}

	if springUpthrust, ok := result["spring_upthrust"].([]map[string]interface{}); ok && len(springUpthrust) > 0 {
		for _, su := range springUpthrust[:minInt(2, len(springUpthrust))] {
			if su["type"].(string) == "spring" {
				keyFindings = append(keyFindings, "检测到 Spring 信号，可能是底部反转")
			} else if su["type"].(string) == "upthrust" {
				keyFindings = append(keyFindings, "检测到 Upthrust 信号，可能是顶部反转")
			}
		}
	}

	return map[string]interface{}{
		"verdict":      verdict,
		"key_findings": keyFindings,
	}
}

// Helper functions

func (t *VolumeAnalysisTool) calcPriceRange(rows []marketRow) float64 {
	if len(rows) == 0 {
		return 0
	}
	high := rows[0].High
	low := rows[0].Low
	for _, r := range rows {
		if r.High > high {
			high = r.High
		}
		if r.Low < low {
			low = r.Low
		}
	}
	if low == 0 {
		return 0
	}
	return (high - low) / low
}

func (t *VolumeAnalysisTool) calcVolTrend(rows []marketRow) float64 {
	if len(rows) < 10 {
		return 0
	}
	recent5Avg := 0.0
	for i := 0; i < 5; i++ {
		recent5Avg += rows[i].Vol
	}
	recent5Avg /= 5

	old5Avg := 0.0
	for i := 5; i < 10; i++ {
		old5Avg += rows[i].Vol
	}
	old5Avg /= 5

	if old5Avg == 0 {
		return 0
	}
	return (recent5Avg - old5Avg) / old5Avg
}

func (t *VolumeAnalysisTool) calcAvgCMF(rows []marketRow) float64 {
	n := minInt(20, len(rows))
	sum := 0.0
	for i := 0; i < n; i++ {
		sum += rows[i].CMF
	}
	return sum / float64(n)
}

func (t *VolumeAnalysisTool) calcAvgMFI(rows []marketRow) float64 {
	n := minInt(20, len(rows))
	sum := 0.0
	for i := 0; i < n; i++ {
		sum += rows[i].MFI
	}
	return sum / float64(n)
}

func (t *VolumeAnalysisTool) calcOBVTrend(rows []marketRow) float64 {
	if len(rows) < 10 {
		return 0
	}
	return rows[0].OBV - rows[10].OBV
}

func (t *VolumeAnalysisTool) isMakingHigherHighs(rows []marketRow) bool {
	if len(rows) < 10 {
		return false
	}
	recent5High := rows[0].High
	for i := 1; i < 5; i++ {
		if rows[i].High > recent5High {
			recent5High = rows[i].High
		}
	}
	old5High := rows[5].High
	for i := 6; i < 10 && i < len(rows); i++ {
		if rows[i].High > old5High {
			old5High = rows[i].High
		}
	}
	return recent5High > old5High
}

func (t *VolumeAnalysisTool) isMakingLowerLows(rows []marketRow) bool {
	if len(rows) < 10 {
		return false
	}
	recent5Low := rows[0].Low
	for i := 1; i < 5; i++ {
		if rows[i].Low < recent5Low {
			recent5Low = rows[i].Low
		}
	}
	old5Low := rows[5].Low
	for i := 6; i < 10 && i < len(rows); i++ {
		if rows[i].Low < old5Low {
			old5Low = rows[i].Low
		}
	}
	return recent5Low < old5Low
}

func (t *VolumeAnalysisTool) calcVolAvg(rows []marketRow, window, startIdx int) float64 {
	if startIdx >= len(rows) {
		return 0
	}
	sum := 0.0
	count := 0
	for i := startIdx; i < startIdx+window && i < len(rows); i++ {
		sum += rows[i].Vol
		count++
	}
	if count == 0 {
		return 0
	}
	return sum / float64(count)
}

var _ types.Tool = (*VolumeAnalysisTool)(nil)
