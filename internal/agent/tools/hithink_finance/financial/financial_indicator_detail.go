package financial

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// FinancialIndicatorDetailTool —— 财务能力指标（27 列）。
//
// 这张表是三表之外最直接可用的"结论型"数据：ROE、毛利率、净利率、流动比率、
// 资产负债率、各项周转率、净利润现金含量，全部由上游算好，不需要自己拼。
// 之前没有任何工具能读到它。
type FinancialIndicatorDetailTool struct {
	config *hithink_finance.Config
}

func NewFinancialIndicatorDetailTool(config *hithink_finance.Config) *FinancialIndicatorDetailTool {
	return &FinancialIndicatorDetailTool{config: config}
}

func (t *FinancialIndicatorDetailTool) Name() string {
	return "hithink.finance.financial.indicator.detail"
}

func (t *FinancialIndicatorDetailTool) Description() string {
	return `获取个股的财务能力指标，直接给出算好的比率，不需要自己用三表拼。

盈利能力：weighted_avg_roe(加权ROE), deduct_weighted_avg_roe(扣非加权ROE),
          sale_gross_margin(销售毛利率), sale_net_interest_ratio(销售净利率),
          parent_holder_net_profit_yoy(归母净利同比), operating_income_yoy(营收同比)
偿债能力：current_ratio(流动比率), quick_ratio(速动比率), cash_ratio(现金比率),
          assets_debt_ratio(资产负债率), long_term_debt_equity_ratio
运营效率：total_assets_turnover_ratio(总资产周转率), inventory_turnover_ratio(存货周转率),
          receive_account_turnover_ratio(应收账款周转率), current_assets_turnover_ratio
利润质量：net_profit_cash_content(净利润现金含量), cash_operating_index,
          operating_cash_flow_net_divide_income(经营现金流/收入), cash_meet_invest_ratio

**分析要点**：net_profit_cash_content（净利润现金含量）低于 0.8 要警惕，
说明利润没变成真金白银；sale_gross_margin 持续下滑说明议价能力在弱化。

注意：报告期字段是 report（不是 period）。数据区间 2024-1 至 2026-2。
使用示例：thscode="600519.SH", periods=4`
}

func (t *FinancialIndicatorDetailTool) Parameters() json.RawMessage {
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

func (t *FinancialIndicatorDetailTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.financial.indicator.detail"
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

	// 显式列出 26 个指标列而不是 SELECT *：捕获列 captured_at 是入库时间戳，
	// 对分析没有价值却占着工具输出预算。显式列也让 schema 契约测试能逐列校验。
	query := `
		SELECT report,
		       operating_income_yoy, operating_profit_yoy,
		       parent_holder_net_profit_yoy, total_assets_growth_ratio,
		       fixed_asset_invest_expansion_ratio,
		       weighted_avg_roe, deduct_weighted_avg_roe,
		       sale_gross_margin, sale_net_interest_ratio,
		       current_ratio, quick_ratio, cash_ratio, earned_interest_multiple,
		       assets_debt_ratio, long_term_debt_equity_ratio,
		       total_assets_turnover_ratio, inventory_turnover_ratio,
		       current_assets_turnover_ratio, receive_account_turnover_ratio,
		       net_profit_cash_content, cash_operating_index,
		       operating_cash_flow_net_divide_income, cash_meet_invest_ratio
		FROM v_financial_indicators_detail
		WHERE thscode = ?
		ORDER BY report DESC
		LIMIT ?
	`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", query, params.Thscode, params.Periods)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}
	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf(
			"未找到财务指标数据：%s。注意该表只覆盖 2024-1 之后的报告期，更早的公司请改用三表工具。", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"periods": len(results),
		"data":    results,
	}
	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*FinancialIndicatorDetailTool)(nil)
