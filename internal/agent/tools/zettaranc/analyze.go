package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// AnalyzeTool performs comprehensive stock analysis using the Zettaranc system.
// It returns technical indicators, strategy signals, and diagnosis.
type AnalyzeTool struct {
	client *CLIClient
}

// NewAnalyzeTool creates a new analyze tool.
func NewAnalyzeTool(client *CLIClient) *AnalyzeTool {
	return &AnalyzeTool{client: client}
}

func (t *AnalyzeTool) Name() string {
	return "zettaranc.analyze"
}

func (t *AnalyzeTool) Description() string {
	return `使用 Z哥交易体系对单只股票进行全面分析。

返回内容：
- 技术指标：KDJ、MACD、RSI、BBI、白线黄线等
- 波浪分析：三波理论阶段判断
- 麒麟会：阶段和置信度
- 战法信号：30+ 种战法（B1/B2、少妇战法、四块砖等）
- 综合诊断：买卖点判断

使用示例：
- 分析茅台：thscode="600519.SH"
- 分析宁德时代（120 天）：thscode="300750.SZ", days=120`
}

func (t *AnalyzeTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "分析天数（默认 120）",
				"default":     120,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *AnalyzeTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
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

	if params.Days <= 0 {
		params.Days = 120
	}

	// Call Python CLI
	input := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    params.Days,
	}

	resp, err := t.client.Execute(ctx, "analyze", input)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("分析失败：%v。请检查：1) 股票代码是否正确；2) 数据是否已同步；3) Python 环境是否正常", err),
		}, nil
	}

	// Format output
	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    params.Days,
		"data":    resp.Data,
		"meta":    resp.Meta,
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

// Ensure AnalyzeTool implements the Tool interface.
var _ types.Tool = (*AnalyzeTool)(nil)
