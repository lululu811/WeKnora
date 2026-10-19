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
	return `获取飙升榜（热股榜）数据。返回 thscode, name, capture_date, rank, heat(热度), rank_change, rank_trend 等。

注意：日期列是 capture_date 不是 trade_date，热度列是 heat 不是 hot_value，本地
数据不含价格字段。

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

	// 字段名对齐 special.v_skyrocket 的真实 schema（见 schema_contract_test.go 与
	// testdata/schema.json）。注意该视图的日期列叫 capture_date 而不是 trade_date，
	// 热度列叫 heat、排名列叫 rank，且根本没有 price 列——旧写法整条都取不到。
	// 同样把 trade_date / limit 改成绑定参数，去掉 Sprintf 拼接。
	query := `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_skyrocket`
	var bindArgs []interface{}
	if params.TradeDate == "" {
		query += ` WHERE capture_date = (SELECT MAX(capture_date) FROM v_skyrocket)`
	} else {
		query += ` WHERE capture_date = ?`
		bindArgs = append(bindArgs, params.TradeDate)
	}
	query += ` ORDER BY rank ASC LIMIT ?`
	bindArgs = append(bindArgs, params.Limit)

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.special.hot_stock.skyrocket")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未找到飙升榜数据。请检查数据是否已同步"}, nil
	}

	output := map[string]interface{}{
		"trade_date": results[0]["capture_date"],
		"count":      len(results),
		"data":       results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

// HotStockListTool returns the overall market heat ranking.
type HotStockListTool struct {
	config *hithink_finance.Config
}

// NewHotStockListTool creates a new hot stock list tool.
func NewHotStockListTool(config *hithink_finance.Config) *HotStockListTool {
	return &HotStockListTool{config: config}
}

func (t *HotStockListTool) Name() string {
	return "hithink.finance.special.hot_stock.top_list"
}

func (t *HotStockListTool) Description() string {
	return `获取全市场人气总榜（按综合关注度与热度排序）。返回 thscode, name, capture_date, rank, heat(热度值), rank_change, rank_trend 等。`
}

func (t *HotStockListTool) Parameters() json.RawMessage {
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
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *HotStockListTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	query := `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_hot_stock`
	var bindArgs []interface{}
	if params.TradeDate == "" {
		query += ` WHERE capture_date = (SELECT MAX(capture_date) FROM v_hot_stock)`
	} else {
		query += ` WHERE capture_date = ?`
		bindArgs = append(bindArgs, params.TradeDate)
	}
	query += ` ORDER BY rank ASC LIMIT ?`
	bindArgs = append(bindArgs, params.Limit)

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.special.hot_stock.top_list")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未找到人气榜数据"}, nil
	}

	output := map[string]interface{}{
		"trade_date": results[0]["capture_date"],
		"count":      len(results),
		"data":       results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

// HotStockTrendTool returns the historical ranking trend for a stock.
type HotStockTrendTool struct {
	config *hithink_finance.Config
}

// NewHotStockTrendTool creates a new hot stock trend tool.
func NewHotStockTrendTool(config *hithink_finance.Config) *HotStockTrendTool {
	return &HotStockTrendTool{config: config}
}

func (t *HotStockTrendTool) Name() string {
	return "hithink.finance.special.hot_stock.rank_trend"
}

func (t *HotStockTrendTool) Description() string {
	return `获取指定个股的历史人气排名趋势。返回 thscode, trend_date, date_ms, rank。用于研判资金关注度是持续升温还是退潮。`
}

func (t *HotStockTrendTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "股票代码，如 600519.SH、600418.SH",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回历史天数（默认 30）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *HotStockTrendTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Limit <= 0 {
		params.Limit = 30
	}

	query := `SELECT thscode, trend_date, date_ms, rank FROM v_hot_stock_rank_trend WHERE thscode = ? ORDER BY trend_date DESC LIMIT ?`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, params.Thscode, params.Limit)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.special.hot_stock.rank_trend")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到 %s 的人气排名趋势数据", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"count":   len(results),
		"data":    results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*HotStockTool)(nil)
	_ types.Tool = (*HotStockListTool)(nil)
	_ types.Tool = (*HotStockTrendTool)(nil)
)
