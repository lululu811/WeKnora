package fund

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// HoldingsStockTool 穿透查询持有指定股票的公募基金明细。
type HoldingsStockTool struct {
	config *hithink_finance.Config
}

// NewHoldingsStockTool 创建 HoldingsStockTool。
func NewHoldingsStockTool(config *hithink_finance.Config) *HoldingsStockTool {
	return &HoldingsStockTool{config: config}
}

func (t *HoldingsStockTool) Name() string {
	return "hithink.finance.fund.holdings.stock"
}

func (t *HoldingsStockTool) Description() string {
	return `穿透查询持有指定 A 股股票的公募基金与 ETF 重仓明细。

用于分析某只股票的机构资金底仓、重仓基金列表及持股比例（机构筹码结构）。`
}

func (t *HoldingsStockTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"stock_thscode": map[string]interface{}{
				"type":        "string",
				"description": "股票同花顺代码，如 600519.SH (贵州茅台) 或 300750.SZ (宁德时代)",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 20，最大 100）",
				"default":     20,
			},
		},
		"required": []string{"stock_thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *HoldingsStockTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		StockThscode string `json:"stock_thscode"`
		Limit        int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	code := strings.TrimSpace(params.StockThscode)
	if code == "" {
		return &types.ToolResult{Success: false, Error: "stock_thscode 不能为空"}, nil
	}

	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 20
	}

	query := fmt.Sprintf(`SELECT fund_thscode, stock_thscode, stock_name, hold_ratio, position_count, security_market_value_rate_pct, period_increase_rate_pct, investment_rank FROM v_fund_holdings WHERE stock_thscode = ? ORDER BY hold_ratio DESC LIMIT %d`, params.Limit)

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

var _ types.Tool = (*HoldingsStockTool)(nil)
