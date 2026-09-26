package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// BacktestTool runs strategy backtests using the Zettaranc system via python-service HTTP API.
type BacktestTool struct {
	client *HTTPClient
}

// NewBacktestTool creates a new backtest tool backed by python-service.
func NewBacktestTool(client *HTTPClient) *BacktestTool {
	return &BacktestTool{client: client}
}

func (t *BacktestTool) Name() string {
	return "zettaranc.backtest"
}

func (t *BacktestTool) Description() string {
	return `使用 Z哥交易体系进行策略回测。

支持策略：
- shaofu: 少妇战法
- multi: 多策略融合
- b1, b2, sb1: 单战法策略

返回内容：
- 收益率、夏普比率、最大回撤
- 胜率、盈亏比
- 交易记录
- 资金曲线

使用示例：
- 少妇战法回测茅台：strategy="shaofu", thscode="600519.SH", days=250
- 多策略回测宁德时代：strategy="multi", thscode="300750.SZ", days=250`
}

func (t *BacktestTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"strategy": map[string]interface{}{
				"type":        "string",
				"description": "策略名称：shaofu（少妇战法）、multi（多策略）、b1、b2、sb1",
				"enum":        []string{"shaofu", "multi", "b1", "b2", "sb1"},
			},
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "回测天数（默认 250，即一年）",
				"default":     250,
			},
		},
		"required": []string{"strategy", "thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *BacktestTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Strategy string `json:"strategy"`
		Thscode  string `json:"thscode"`
		Days     int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	if params.Strategy == "" {
		return &types.ToolResult{
			Success: false,
			Error:   "参数错误：strategy 不能为空",
		}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{
			Success: false,
			Error:   "参数错误：thscode 不能为空",
		}, nil
	}

	if params.Days <= 0 {
		params.Days = 250
	}

	// Call python-service HTTP API
	resp, err := t.client.Screen(ctx, params.Strategy, 50)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("回测失败：%v。该工具正在迁移到 python-service，请先用 zettaranc.screener 选择候选股票。", err),
		}, nil
	}

	// Format output
	output := map[string]interface{}{
		"strategy": params.Strategy,
		"thscode":  params.Thscode,
		"days":     params.Days,
		"note":     "完整回测功能迁移中；当前返回策略候选股票列表，请使用 zettaranc.screener 替代",
		"data":     resp,
	}

	outputJSON, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(outputJSON),
	}, nil
}

// Ensure BacktestTool implements the Tool interface.
var _ types.Tool = (*BacktestTool)(nil)
