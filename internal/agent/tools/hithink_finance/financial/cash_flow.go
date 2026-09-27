package financial

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// CashFlowStatementTool —— 现金流量表。
type CashFlowStatementTool struct {
	config *hithink_finance.Config
}

func NewCashFlowStatementTool(config *hithink_finance.Config) *CashFlowStatementTool {
	return &CashFlowStatementTool{config: config}
}

func (t *CashFlowStatementTool) Name() string {
	return "hithink.finance.financial.statement.cashflow"
}

func (t *CashFlowStatementTool) Description() string {
	return `获取现金流量表。返回 period,
act_cash_flow_net(经营活动现金流净额), invest_cash_flow_net(投资活动现金流净额),
financing_cash_flow_net(筹资活动现金流净额), cash_equivalents_net_addition(现金净增加额),
pay_dividends_profits_interest_cash(分配股利利润偿付利息支付的现金),
pay_fixed_assets_etc_cash(购建固定资产等支付的现金) 等。

**分析要点**：把 act_cash_flow_net 和利润表的 net_profit 对比——
经营现金流长期远低于净利润，是利润质量存疑的经典信号（赚的是纸面钱）。
经营现金流为负而利润为正时，通常意味着应收账款在膨胀。

注意：报告期字段是 period（形如 2026Q2）。数据区间 2017Q3–2026Q2。
使用示例：thscode="600519.SH", periods=4`
}

func (t *CashFlowStatementTool) Parameters() json.RawMessage {
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

func (t *CashFlowStatementTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.financial.statement.cashflow"
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
		       act_cash_flow_net, invest_cash_flow_net, financing_cash_flow_net,
		       cash_equivalents_net_addition,
		       pay_dividends_profits_interest_cash, pay_fixed_assets_etc_cash
		FROM v_cash_flow_statement
		WHERE thscode = ?
		ORDER BY period DESC
		LIMIT ?
	`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", query, params.Thscode, params.Periods)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}
	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到现金流量表数据：%s", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"periods": len(results),
		"data":    results,
	}
	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*CashFlowStatementTool)(nil)
