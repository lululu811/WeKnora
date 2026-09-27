package financial

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type ValuationSnapshotTool struct {
	config *hithink_finance.Config
}

func NewValuationSnapshotTool(config *hithink_finance.Config) *ValuationSnapshotTool {
	return &ValuationSnapshotTool{config: config}
}

func (t *ValuationSnapshotTool) Name() string {
	return "hithink.finance.financial.valuation.snapshot"
}

func (t *ValuationSnapshotTool) Description() string {
	return `获取单只股票的最新估值快照。返回 snapshot_date, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm。

注意：本数据源没有市值字段（market_cap / circ_market_cap 均不存在），需要市值请改用
financials.v_financial_indicators 或直接用 hithink.finance.query.sql。

使用示例：thscode="600519.SH"`
}

func (t *ValuationSnapshotTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH",
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ValuationSnapshotTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.financial.valuation.snapshot")}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}

	// 字段名必须与 financials.v_valuation_latest 的真实 schema 一致。
	// 旧写法 `date, pb, ps_ttm, market_cap, circ_market_cap` 中 date→snapshot_date、
	// pb→pb_mrq，而 market_cap / circ_market_cap 在该视图里根本不存在，整条查询必然
	// 报 Binder Error。schema 由 internal/agent/tools/hithink_finance/schema_contract_test.go
	// 对着 testdata/schema.json 校验，改字段前先看那份快照。
	query := `SELECT snapshot_date, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm FROM v_valuation_latest WHERE thscode = ?`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "financials", query, params.Thscode)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.financial.valuation.snapshot")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("股票不存在或无估值数据：%s", params.Thscode)}, nil
	}

	result := results[0]
	result["thscode"] = params.Thscode

	outputJSON, _ := json.MarshalIndent(result, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*ValuationSnapshotTool)(nil)
