package indicator

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type TrendMATool struct {
	config *hithink_finance.Config
}

func NewTrendMATool(config *hithink_finance.Config) *TrendMATool {
	return &TrendMATool{config: config}
}

func (t *TrendMATool) Name() string {
	return "hithink.finance.indicator.trend.ma"
}

func (t *TrendMATool) Description() string {
	return `获取股票的均线指标（MA5/MA10/MA20/MA60/MA120/MA250）。

使用示例：thscode="600519.SH", days=30`
}

func (t *TrendMATool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "查询天数（默认 30）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *TrendMATool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 30
	}
	if params.Days > 500 {
		params.Days = 500
	}

	query := `SELECT date, overlap_sma_5 AS ma5, overlap_sma_10 AS ma10, overlap_sma_20 AS ma20, overlap_sma_60 AS ma60, overlap_sma_120 AS ma120, overlap_sma_250 AS ma250 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到 MA 数据：%s", params.Thscode)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(results),
		"data":    results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*TrendMATool)(nil)
