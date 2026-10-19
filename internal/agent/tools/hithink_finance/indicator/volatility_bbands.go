package indicator

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// VolatilityBBandsTool 获取股票布林带与真实波幅 ATR/NATR。
type VolatilityBBandsTool struct {
	config *hithink_finance.Config
}

func NewVolatilityBBandsTool(config *hithink_finance.Config) *VolatilityBBandsTool {
	return &VolatilityBBandsTool{config: config}
}

func (t *VolatilityBBandsTool) Name() string {
	return "hithink.finance.indicator.volatility.bbands"
}

func (t *VolatilityBBandsTool) Description() string {
	return `获取股票布林带（上轨、中轨、下轨）与真实波幅 ATR/NATR 指标。

返回 date, upper, middle, lower, atr, natr。`
}

func (t *VolatilityBBandsTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 600519.SH）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "查询天数（默认 30）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VolatilityBBandsTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
		params.Days = 30
	}
	if params.Days > 500 {
		params.Days = 500
	}

	query := `SELECT date, volatility_bbands_20_2_0_upper AS upper, volatility_bbands_20_2_0_middle AS middle, volatility_bbands_20_2_0_lower AS lower, volatility_atr_14 AS atr, volatility_natr_14 AS natr FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(rows),
		"data":    rows,
	}

	resBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*VolatilityBBandsTool)(nil)
