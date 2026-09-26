package market

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// PriceSnapshotTool returns the latest price snapshot for a stock.
type PriceSnapshotTool struct {
	config *hithink_finance.Config
}

// NewPriceSnapshotTool creates a new price snapshot tool.
func NewPriceSnapshotTool(config *hithink_finance.Config) *PriceSnapshotTool {
	return &PriceSnapshotTool{config: config}
}

func (t *PriceSnapshotTool) Name() string {
	return "hithink.finance.market.price.snapshot"
}

func (t *PriceSnapshotTool) Description() string {
	return `获取单只股票的最新行情快照（前复权）。

返回字段：
- date: 交易日期
- open: 开盘价
- high: 最高价
- low: 最低价
- close: 收盘价
- volume: 成交量（股）
- turnover: 成交额（元）

使用示例：
- 获取茅台最新行情：thscode="600519.SH"
- 获取宁德时代最新行情：thscode="300750.SZ"`
}

func (t *PriceSnapshotTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH（茅台）、300750.SZ（宁德时代）",
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *PriceSnapshotTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	// Check sync window
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   err.Error(),
		}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{
			Success: false,
			Error:   "参数错误：thscode 不能为空",
		}, nil
	}

	// Query DuckDB via Python service
	query := `
		SELECT date, open, high, low, close, volume, turnover
		FROM v_daily_qfq
		WHERE thscode = ?
		ORDER BY date DESC
		LIMIT 1
	`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "market", query, params.Thscode)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   err.Error(),
		}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("股票不存在：%s。请检查股票代码是否正确，格式应为 '600519.SH' 或 '300750.SZ'", params.Thscode),
		}, nil
	}

	// Extract result
	result := results[0]

	// Check data freshness
	if dateStr, ok := result["date"].(string); ok {
		dataDate, err := time.Parse("2006-01-02", dateStr)
		if err == nil {
			daysOld := int(time.Since(dataDate).Hours() / 24)
			if daysOld > 3 {
				result["warning"] = fmt.Sprintf("数据已过期 %d 天，最后更新日期：%s。请检查数据同步状态", daysOld, dateStr)
			}
		}
	}

	result["thscode"] = params.Thscode

	output, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(output),
	}, nil
}

// Ensure PriceSnapshotTool implements the Tool interface.
var _ types.Tool = (*PriceSnapshotTool)(nil)
