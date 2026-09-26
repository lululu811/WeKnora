package query

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type SQLQueryTool struct {
	config *hithink_finance.Config
}

func NewSQLQueryTool(config *hithink_finance.Config) *SQLQueryTool {
	return &SQLQueryTool{config: config}
}

func (t *SQLQueryTool) Name() string {
	return "hithink.finance.query.sql"
}

func (t *SQLQueryTool) Description() string {
	return `执行只读 SQL 查询。支持查询所有 DuckDB 数据库的 v_* 视图。

可用数据库及主要视图字段：
- market: v_daily_qfq(thscode, date, open, high, low, close, volume, turnover, forward_factor, currency, interval), v_symbol(thscode, name, market, sector)
- indicators: v_indicators_daily(thscode, date, 以及 200+ 技术指标列如 momentum_kdj_9_3_k/d/j, momentum_rsi_14, momentum_macd_12_26_9_macd 等。注意：indicators 表没有 price 列)
- financials: 财务数据
- special: 涨停池、龙虎榜、热股等特色数据
- fund: 基金数据
- index: 指数数据
- futures: 期货数据

安全限制：只允许 SELECT 查询，默认 LIMIT 1000 行

使用示例：
- 查行情：sql="SELECT date, close, turnover FROM v_daily_qfq WHERE thscode='600519.SH' LIMIT 5", db="market"
- 查 KDJ：sql="SELECT date, momentum_kdj_9_3_k AS k, momentum_kdj_9_3_d AS d FROM v_indicators_daily WHERE thscode='600519.SH' LIMIT 10", db="indicators"`
}

func (t *SQLQueryTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"sql": map[string]interface{}{
				"type":        "string",
				"description": "SQL 查询语句（只读，只能 SELECT）",
			},
			"db": map[string]interface{}{
				"type":        "string",
				"description": "数据库名称",
				"enum":        hithink_finance.DBNames(),
			},
		},
		"required": []string{"sql", "db"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SQLQueryTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		SQL string `json:"sql"`
		DB  string `json:"db"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.SQL == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：sql 不能为空"}, nil
	}
	if params.DB == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：db 不能为空"}, nil
	}

	if err := hithink_finance.ValidateReadOnlySQL(params.SQL); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	params.SQL = hithink_finance.EnsureLimit(params.SQL, hithink_finance.DefaultLimit)

	results, err := hithink_finance.QueryDuckDB(ctx, t.config, params.DB, params.SQL)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	output := map[string]interface{}{
		"db":    params.DB,
		"count": len(results),
		"rows":  results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SQLQueryTool)(nil)
