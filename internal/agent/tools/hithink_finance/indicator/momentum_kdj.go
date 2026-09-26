package indicator

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type MomentumKDJTool struct {
	config *hithink_finance.Config
}

func NewMomentumKDJTool(config *hithink_finance.Config) *MomentumKDJTool {
	return &MomentumKDJTool{config: config}
}

func (t *MomentumKDJTool) Name() string {
	return "hithink.finance.indicator.momentum.kdj"
}

func (t *MomentumKDJTool) Description() string {
	return `获取股票的 KDJ 指标。返回 date, k, d, j。

使用示例：thscode="600519.SH", days=30`
}

func (t *MomentumKDJTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
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

func (t *MomentumKDJTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	query := `SELECT date, momentum_kdj_9_3_k AS k, momentum_kdj_9_3_d AS d, momentum_kdj_9_3_j AS j FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到 KDJ 数据：%s", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(results),
		"data":    results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*MomentumKDJTool)(nil)
