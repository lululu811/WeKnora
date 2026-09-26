// Package pattern provides a technical pattern scanner that reads
// pre-computed indicators from DuckDB and synthesises actionable signals.
package pattern

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type PatternScanTool struct {
	config *hithink_finance.Config
}

func NewPatternScanTool(config *hithink_finance.Config) *PatternScanTool {
	return &PatternScanTool{config: config}
}

func (t *PatternScanTool) Name() string { return "hithink.finance.pattern.scan" }

func (t *PatternScanTool) Description() string {
	return `扫描股票的技术形态信号。从 DuckDB indicators 库的 227 个预计算指标中，
综合检测 30+ 种买卖形态，返回结构化信号列表。

检测类别：
- 买入：MACD金叉、KDJ金叉(超卖区)、RSI超卖反弹、Supertrend翻转、PSAR翻转、
  CCI超卖(<-100)、Williams%R超卖(<-80)、Stochastic金叉、MFI超卖(<20)、
  ADX多头(ADX>25且DI+>DI-)、Aroon多头(up>70)、VWAP突破、Z-Score超卖(<-2)
- 卖出：MACD死叉、KDJ死叉(超买区)、RSI超买回落(>80)、Supertrend翻空、
  MFI超买(>80)、Stochastic死叉、Z-Score超买(>2)
- 趋势：ADX强趋势、DI金叉/死叉、Vortex金叉/死叉、Aroon多头/空头排列
- 波动：布林带收口(变盘)、Donchian突破、ATR扩张
- 量价：量价齐升、量价背离、CMF资金流入/流出
- 蜡烛图：MorningStar、EveningStar、Hammer、ShootingStar、Doji、
  Engulfing、Harami、Piercing、DarkCloud 等 61 种

使用示例：thscode="600519.SH", days=10`
}

func (t *PatternScanTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "扫描天数（默认 10，用于检测交叉信号）",
				"default":     10,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *PatternScanTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
		params.Days = 10
	}
	if params.Days > 60 {
		params.Days = 60
	}

	rows, err := queryIndicatorRows(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}
	if len(rows) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到指标数据：%s", params.Thscode)}, nil
	}

	signals := detectSignals(rows)

	output := map[string]interface{}{
		"thscode":  params.Thscode,
		"days":     len(rows),
		"latest":   rows[0],
		"signals":  signals,
		"summary":  summarizeSignals(signals),
	}
	out, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(out)}, nil
}

var _ types.Tool = (*PatternScanTool)(nil)
