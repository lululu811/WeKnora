package fund

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// HoldingsPortfolioTool 查询指定基金或 ETF 的重仓股持仓清单。
type HoldingsPortfolioTool struct {
	config *hithink_finance.Config
}

// NewHoldingsPortfolioTool 创建 HoldingsPortfolioTool。
func NewHoldingsPortfolioTool(config *hithink_finance.Config) *HoldingsPortfolioTool {
	return &HoldingsPortfolioTool{config: config}
}

func (t *HoldingsPortfolioTool) Name() string {
	return "hithink.finance.fund.holdings.portfolio"
}

func (t *HoldingsPortfolioTool) Description() string {
	return `查询指定基金或 ETF 的重仓股票组合（持仓明细、持股市值占比、增减持变动等）。`
}

func (t *HoldingsPortfolioTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"fund_thscode": map[string]interface{}{
				"type":        "string",
				"description": "基金代码，如 510300.SH (华泰柏瑞沪深300ETF) 或 110011.OF (易方达中小盘)",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 20，最大 100）",
				"default":     20,
			},
		},
		"required": []string{"fund_thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *HoldingsPortfolioTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		FundThscode string `json:"fund_thscode"`
		Limit       int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	code := strings.TrimSpace(params.FundThscode)
	if code == "" {
		return &types.ToolResult{Success: false, Error: "fund_thscode 不能为空"}, nil
	}

	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 20
	}

	query := fmt.Sprintf(`SELECT fund_thscode, stock_thscode, stock_name, hold_ratio, position_count, security_market_value_rate_pct, period_increase_rate_pct, investment_rank FROM v_fund_holdings WHERE fund_thscode = ? ORDER BY hold_ratio DESC LIMIT %d`, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "fund", query, code)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	resBytes, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*HoldingsPortfolioTool)(nil)
