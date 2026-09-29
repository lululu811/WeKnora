package special

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// tradeDateRe 只接受 YYYY-MM-DD。真正的注入防线是 `?` 绑定，这个正则负责
// 把「模型传了个奇怪日期」变成一句能读懂的报错，而不是 DuckDB 的 Binder Error。
var tradeDateRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

type LimitUpPoolTool struct {
	config *hithink_finance.Config
}

func NewLimitUpPoolTool(config *hithink_finance.Config) *LimitUpPoolTool {
	return &LimitUpPoolTool{config: config}
}

func (t *LimitUpPoolTool) Name() string {
	return "hithink.finance.special.limit.limit_up_pool"
}

func (t *LimitUpPoolTool) Description() string {
	return `获取指定日期的涨停池数据。返回 thscode, name, trade_date, limit_up_time, stat 等。

使用示例：trade_date="latest"`
}

func (t *LimitUpPoolTool) Parameters() json.RawMessage {
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
				"description": "返回数量限制（默认 100）",
				"default":     100,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *LimitUpPoolTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
	} else {
		// 仍然走参数绑定，但先校验格式：trade_date 是 DATE 列，绑定一个
		// 非法字面量会让 DuckDB 在解析阶段就报 Binder Error，而这里提前
		// 拦下来能给调用方一句能看懂的话。真正的注入防线是 `?` 绑定，
		// 格式校验只是让错误可读。
		if !tradeDateRe.MatchString(params.TradeDate) {
			return &types.ToolResult{
				Success: false,
				Error:   fmt.Sprintf("trade_date 格式非法：%q，应为 YYYY-MM-DD", params.TradeDate),
			}, nil
		}
	}
	if params.Limit <= 0 {
		params.Limit = 100
	}

	// trade_date 与 limit 全部走 `?` 绑定。旧写法
	// fmt.Sprintf(` WHERE trade_date = '%s'`, params.TradeDate) 把模型给的
	// 字符串直接拼进 SQL —— LLM 会照抄用户在对话里写的任何内容，一个
	// 带单引号的日期就能把语句闭合掉。
	query := `SELECT thscode, name, trade_date, limit_up_time, continue_day_cnt, seal_money, last_price FROM v_limit_up_pool`
	var bind []interface{}
	if params.TradeDate == "" {
		query += ` WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool)`
	} else {
		query += ` WHERE trade_date = ?`
		bind = append(bind, params.TradeDate)
	}
	// LIMIT 不能用占位符（DuckDB 允许但类型推断不稳），这里数值已被上面的
	// `params.Limit <= 0` 夹过一遍，且 Limit 来自 JSON 的 int 反序列化，
	// 不会是任意字符串。
	query += fmt.Sprintf(` ORDER BY seal_money DESC LIMIT %d`, params.Limit)

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, bind...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未找到涨停数据。请检查日期是否正确或数据是否已同步"}, nil
	}

	output := map[string]interface{}{
		"trade_date": results[0]["trade_date"],
		"count":      len(results),
		"data":       results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*LimitUpPoolTool)(nil)
