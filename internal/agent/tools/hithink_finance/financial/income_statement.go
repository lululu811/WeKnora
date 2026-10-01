package financial

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type IncomeStatementTool struct {
	config *hithink_finance.Config
}

func NewIncomeStatementTool(config *hithink_finance.Config) *IncomeStatementTool {
	return &IncomeStatementTool{config: config}
}

func (t *IncomeStatementTool) Name() string {
	return "hithink.finance.financial.statement.income"
}

func (t *IncomeStatementTool) Description() string {
	return incomeStatementDesc
}

func (t *IncomeStatementTool) Parameters() json.RawMessage {
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

func (t *IncomeStatementTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.financial.statement.income")}, nil
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

	// 字段名对齐 financials.v_income_statement 的真实 schema（见 schema_contract_test.go
	// 与 testdata/schema.json）。旧写法 report_date / revenue / operating_cost / eps
	// 在该视图里一个都不存在，查询必然 Binder Error。排序见 period.go。
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", incomeStatementQuery, params.Thscode, params.Periods)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.financial.statement.income")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到财务数据：%s", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"periods": len(results),
		"data":    results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*IncomeStatementTool)(nil)
