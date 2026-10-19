package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// LimitBreakPoolTool 获取短线炸板池与回封失败个股。
type LimitBreakPoolTool struct {
	config *hithink_finance.Config
}

func NewLimitBreakPoolTool(config *hithink_finance.Config) *LimitBreakPoolTool {
	return &LimitBreakPoolTool{config: config}
}

func (t *LimitBreakPoolTool) Name() string {
	return "hithink.finance.special.limit.break_pool"
}

func (t *LimitBreakPoolTool) Description() string {
	return `获取最新交易日炸板池（盘中曾触及涨停但被砸开、开板次数、收盘涨跌幅及亏钱效应）。

返回 trade_date, thscode, name, last_price, open_times, price_change_ratio。`
}

func (t *LimitBreakPoolTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 30，最大 100）",
				"default":     30,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *LimitBreakPoolTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Limit int `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 30
	}

	query := `SELECT trade_date, thscode, name, last_price, open_times, price_change_ratio FROM v_limit_break_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_break_pool) ORDER BY open_times DESC LIMIT ?`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, params.Limit)
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

var _ types.Tool = (*LimitBreakPoolTool)(nil)
