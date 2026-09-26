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
	return `获取单只股票的最新估值快照。返回 pe_ttm, pb, ps_ttm, market_cap 等。

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
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
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

	query := fmt.Sprintf(`SELECT date, pe_ttm, pb, ps_ttm, market_cap, circ_market_cap FROM v_valuation_latest WHERE thscode = '%s'`, params.Thscode)

	results, err := hithink_finance.QueryDuckDB(ctx, t.config, "financials", query)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
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
