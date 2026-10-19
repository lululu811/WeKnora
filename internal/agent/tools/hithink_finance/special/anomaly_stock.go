package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// AnomalyStockTool 获取个股盘中/收盘大涨、大跌、异动归因分析与关键词列表。
type AnomalyStockTool struct {
	config *hithink_finance.Config
}

func NewAnomalyStockTool(config *hithink_finance.Config) *AnomalyStockTool {
	return &AnomalyStockTool{config: config}
}

func (t *AnomalyStockTool) Name() string {
	return "hithink.finance.special.anomaly.stock_reason"
}

func (t *AnomalyStockTool) Description() string {
	return `获取指定股票近期（或全市场最新）大涨、大跌、盘中异动归因分析、驱动逻辑与关键词列表。

返回 capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list。`
}

func (t *AnomalyStockTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 600519.SH 或 000002.SZ）。若为空则返回全市场最新异动列表",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回记录条数（默认 10，最大 50）",
				"default":     10,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *AnomalyStockTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	if params.Limit <= 0 || params.Limit > 50 {
		params.Limit = 10
	}

	var query string
	var queryArgs []interface{}

	if params.Thscode != "" {
		query = `SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list FROM v_anomaly_list WHERE thscode = ? ORDER BY capture_date DESC LIMIT ?`
		queryArgs = []interface{}{params.Thscode, params.Limit}
	} else {
		query = `SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list FROM v_anomaly_list WHERE capture_date = (SELECT MAX(capture_date) FROM v_anomaly_list) ORDER BY capture_date DESC LIMIT ?`
		queryArgs = []interface{}{params.Limit}
	}

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, queryArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	resBytes, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*AnomalyStockTool)(nil)
