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
	return `获取资产负债表。返回 period, assets_total(总资产), total_current_assets(流动资产),
non_current_nets_total(非流动资产净额), total_debt(总债务), holder_equity_total(股东权益),
cash(货币资金), accounts_receivable(应收账款) 等。

配合 financial.indicator.detail 里的 assets_debt_ratio（资产负债率）、
current_ratio（流动比率）一起看，能判断偿债能力与财务健康度。

注意：报告期字段是 period（形如 2026Q2）。数据区间 2017Q3–2026Q2。
使用示例：thscode="600519.SH", periods=4`
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

	query := `
		SELECT period, fiscal_year, fiscal_period, currency,
		       total_current_assets, non_current_nets_total, assets_total,
		       total_debt, holder_equity_total, cash, accounts_receivable
		FROM v_balance_sheet
		WHERE thscode = ?
		ORDER BY period DESC
		LIMIT ?
	`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", query, params.Thscode, params.Periods)
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
