package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type HotStockTool struct {
	config *hithink_finance.Config
}

func NewHotStockTool(config *hithink_finance.Config) *HotStockTool {
	return &HotStockTool{config: config}
}

func (t *HotStockTool) Name() string {
	return "hithink.finance.special.hot_stock.skyrocket"
}

func (t *HotStockTool) Description() string {
	return `获取飙升榜（热股榜）数据。返回 thscode, name, trade_date, hot_rank, hot_value 等。

使用示例：trade_date="latest"`
}

func (t *HotStockTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"trade_date": map[string]interface{}{
				"type":        "string",
				"description": "交易日期（YYYY-MM-DD）或 'latest' 表示最新",
				"default":     "latest",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 50）",
				"default":     50,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *HotStockTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		TradeDate string `json:"trade_date"`
		Limit     int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.TradeDate == "" || params.TradeDate == "latest" {
		params.TradeDate = ""
	}
	if params.Limit <= 0 {
		params.Limit = 50
	}

	query := `SELECT thscode, name, trade_date, hot_rank, hot_value, change_rate, price FROM v_skyrocket`
	if params.TradeDate == "" {
		query += ` WHERE trade_date = (SELECT MAX(trade_date) FROM v_skyrocket)`
	} else {
		query += fmt.Sprintf(` WHERE trade_date = '%s'`, params.TradeDate)
	}
	query += fmt.Sprintf(` ORDER BY hot_rank ASC LIMIT %d`, params.Limit)

	results, err := hithink_finance.QueryDuckDB(ctx, t.config, "special", query)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未找到飙升榜数据。请检查数据是否已同步"}, nil
	}

	output := map[string]interface{}{
		"trade_date": results[0]["trade_date"],
		"count":      len(results),
		"data":       results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*HotStockTool)(nil)
