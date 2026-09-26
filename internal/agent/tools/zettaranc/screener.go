package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// ScreenerTool performs stock screening using the Zettaranc system via python-service HTTP API.
type ScreenerTool struct {
	client *HTTPClient
}

// NewScreenerTool creates a new screener tool backed by python-service.
func NewScreenerTool(client *HTTPClient) *ScreenerTool {
	return &ScreenerTool{client: client}
}

func (t *ScreenerTool) Name() string {
	return "zettaranc.screener"
}

func (t *ScreenerTool) Description() string {
	return `使用 Z哥交易体系进行智能选股。

支持策略：
- B1: B1 买点（J 值超卖 + 缩量回调）
- B2: B2 买点（趋势确认）
- SB1: 加强版 B1
- shaofu: 少妇战法共振
- limit_up: 涨停板选股
- anomaly: 异动选股

返回内容：
- 按评分排序的候选股票列表
- 每只股票的匹配理由
- 技术指标快照

使用示例：
- B1 买点选股：strategy="B1", limit=20
- 少妇战法选股：strategy="shaofu", limit=20`
}

func (t *ScreenerTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"strategy": map[string]interface{}{
				"type":        "string",
				"description": "选股策略",
				"enum":        []string{"B1", "B2", "SB1", "shaofu", "limit_up", "anomaly"},
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量（默认 20，最大 100）",
				"default":     20,
			},
		},
		"required": []string{"strategy"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ScreenerTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Strategy string `json:"strategy"`
		Limit    int    `json:"limit"`
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

	if params.Limit <= 0 {
		params.Limit = 20
	}
	if params.Limit > 100 {
		params.Limit = 100
	}

	// Call python-service HTTP API
	resp, err := t.client.Screen(ctx, params.Strategy, params.Limit)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("选股失败：%v。请检查：1) 策略名称是否正确；2) 数据是否已同步", err),
		}, nil
	}

	// Format output
	output := map[string]interface{}{
		"strategy": params.Strategy,
		"limit":    params.Limit,
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

// Ensure ScreenerTool implements the Tool interface.
var _ types.Tool = (*ScreenerTool)(nil)
