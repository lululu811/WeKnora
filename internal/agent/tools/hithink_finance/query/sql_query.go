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

// sqlQueryDescriptionHead / sqlQueryDescriptionTail 是人工维护的描述首尾。
//
// 中间的表/列目录 schemaCatalog 由 go:generate 从 testdata/schema.json 快照生成
// （见 gencatalog/main.go 与 sql_query_catalog.go），因此列名不会与真实库漂移；
// 生成物在运行时是静态字符串，不读文件。首尾是快照里没有的运营知识，留在此处。
// 三者拼成模型可见的唯一 schema 目录——保持「一个字符串 + 中文小节标题」，勿改 JSON/英文。
const sqlQueryDescriptionHead = `执行只读 SQL 查询。支持查询所有 DuckDB 数据库的 v_* 视图。
下表目录由 testdata/schema.json 快照生成，只覆盖 v_* 视图；未列入快照的表（如各库 raw_* 原始表）同样可查，只是未在此列出列名。

安全限制：只允许 SELECT 查询，默认 LIMIT 1000 行

`

const sqlQueryDescriptionTail = `
使用示例：
- 查行情：sql="SELECT date, close, turnover FROM v_daily_qfq WHERE thscode='600519.SH' LIMIT 5", db="market"
- 查 KDJ：sql="SELECT date, momentum_kdj_9_3_k AS k FROM v_indicators_daily WHERE thscode='600519.SH' LIMIT 10", db="indicators"
- 这只票属于哪些行业/概念：sql="SELECT name, tag FROM v_index_universe WHERE thscode='600519.SH'", db="index"
- 同行业还有哪些票：sql="SELECT c.thscode, c.name FROM v_index_constituents c JOIN v_index_universe u ON c.index_thscode=u.thscode WHERE u.tag='industry' AND u.name='半导体'", db="index"
- 行业板块近20日涨幅：sql="SELECT trade_date, close FROM v_index_daily WHERE thscode=(SELECT thscode FROM v_index_universe WHERE tag='industry' AND name='半导体' LIMIT 1) ORDER BY trade_date DESC LIMIT 20", db="index"
- 利润率趋势：sql="SELECT period, operating_income, parent_holder_net_profit FROM v_income_statement WHERE thscode='600519.SH' ORDER BY period DESC LIMIT 8", db="financials"
- 现金流质量：sql="SELECT period, act_cash_flow_net, financing_cash_flow_net FROM v_cash_flow_statement WHERE thscode='600519.SH' ORDER BY period DESC LIMIT 8", db="financials"
- 最近炸板：sql="SELECT trade_date, thscode, name, open_times FROM v_limit_break_pool ORDER BY trade_date DESC LIMIT 20", db="special"
`

//go:generate go run ./gencatalog

// Description 返回模型可见的唯一 schema 目录。
func (t *SQLQueryTool) Description() string {
	return sqlQueryDescriptionHead + schemaCatalog + sqlQueryDescriptionTail
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
