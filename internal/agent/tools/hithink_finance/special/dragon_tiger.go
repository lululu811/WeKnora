package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type DragonTigerTool struct {
	config *hithink_finance.Config
}

func NewDragonTigerTool(config *hithink_finance.Config) *DragonTigerTool {
	return &DragonTigerTool{config: config}
}

func (t *DragonTigerTool) Name() string {
	return "hithink.finance.special.dragon_tiger.list"
}

func (t *DragonTigerTool) Description() string {
	return `获取龙虎榜数据。返回 thscode, name, trade_date, board_type, net_value(净买额), net_rate(净买占比), buy_value, sell_value, org_net_value(机构净买), change, hot_rank, range_days 等。

注意：净买额字段是 net_value，不是 net_buy；上榜原因(reason)不在本地数据里。

使用示例：trade_date="latest"`
}

func (t *DragonTigerTool) Parameters() json.RawMessage {
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

func (t *DragonTigerTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	// 字段名对齐 special.v_dragon_tiger 的真实 schema（见 schema_contract_test.go
	// 与 testdata/schema.json）。旧写法 close_price / change_rate / turnover /
	// net_buy / reason 在该视图里一个都不存在，查询必然 Binder Error。
	//
	// trade_date 和 limit 都改用绑定参数，不再 Sprintf 拼接——旧代码把用户传入的
	// trade_date 直接塞进引号里，是条漏网的注入路径（analysis/data.go 早已改掉，
	// 这里和 hot_stock.go 当时漏了）。
	query := `SELECT trade_date, board_type, thscode, name, net_value, net_rate, buy_value, sell_value, org_net_value, change, hot_rank, range_days FROM v_dragon_tiger`
	var bindArgs []interface{}
	if params.TradeDate == "" {
		query += ` WHERE trade_date = (SELECT MAX(trade_date) FROM v_dragon_tiger)`
	} else {
		query += ` WHERE trade_date = ?`
		bindArgs = append(bindArgs, params.TradeDate)
	}
	query += ` ORDER BY net_value DESC LIMIT ?`
	bindArgs = append(bindArgs, params.Limit)

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.special.dragon_tiger.list")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未找到龙虎榜数据。请检查日期是否正确或数据是否已同步"}, nil
	}

	output := map[string]interface{}{
		"trade_date": results[0]["trade_date"],
		"count":      len(results),
		"data":       results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*DragonTigerTool)(nil)
