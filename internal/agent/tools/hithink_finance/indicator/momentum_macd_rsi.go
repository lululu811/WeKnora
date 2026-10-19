package indicator

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// MomentumMacdRsiTool 获取股票 MACD 与多周期 RSI。
type MomentumMacdRsiTool struct {
	config *hithink_finance.Config
}

func NewMomentumMacdRsiTool(config *hithink_finance.Config) *MomentumMacdRsiTool {
	return &MomentumMacdRsiTool{config: config}
}

func (t *MomentumMacdRsiTool) Name() string {
	return "hithink.finance.indicator.momentum.macd_rsi"
}

func (t *MomentumMacdRsiTool) Description() string {
	return `获取股票 MACD（快线、信号线、柱状图）与多周期 RSI（RSI6、RSI12、RSI24）。

返回 date, macd, signal, hist, rsi_6, rsi_12, rsi_24。`
}

func (t *MomentumMacdRsiTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 600519.SH）",
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

func (t *MomentumMacdRsiTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	query := `SELECT date, momentum_macd_12_26_9_macd AS macd, momentum_macd_12_26_9_signal AS signal, momentum_macd_12_26_9_hist AS hist, momentum_rsi_6 AS rsi_6, momentum_rsi_12 AS rsi_12, momentum_rsi_24 AS rsi_24 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(rows),
		"data":    rows,
	}

	resBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*MomentumMacdRsiTool)(nil)
