package financial

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// BalanceSheetTool —— 资产负债表。
//
// 此前全平台只有利润表一个工具，资产负债表 / 现金流量表 / 财务指标三张表
// （22 万 + 23 万 + 5 万行）没有任何工具能读到，是基本面分析最大的洞。
type BalanceSheetTool struct {
	config *hithink_finance.Config
}

func NewBalanceSheetTool(config *hithink_finance.Config) *BalanceSheetTool {
	return &BalanceSheetTool{config: config}
}

func (t *BalanceSheetTool) Name() string {
	return "hithink.finance.financial.statement.balance"
}

func (t *BalanceSheetTool) Description() string {
	return balanceSheetDesc
}

func (t *BalanceSheetTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"periods": map[string]interface{}{
				"type":        "integer",
				"description": "报告期数（默认 4，最大 20）",
				"default":     4,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *BalanceSheetTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.financial.statement.balance"
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Periods int    `json:"periods"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Periods <= 0 {
		params.Periods = 4
	}
	if params.Periods > 20 {
		params.Periods = 20
	}

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", balanceSheetQuery, params.Thscode, params.Periods)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}
	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到资产负债表数据：%s", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"periods": len(results),
		"data":    results,
	}
	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*BalanceSheetTool)(nil)
