package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// AnalyzeTool performs comprehensive stock analysis using the Zettaranc system.
// It calls the python-service HTTP API for analysis.
type AnalyzeTool struct {
	client *HTTPClient
}

// NewAnalyzeTool creates a new analyze tool.
func NewAnalyzeTool(client *HTTPClient) *AnalyzeTool {
	return &AnalyzeTool{client: client}
}

func (t *AnalyzeTool) Name() string {
	return "zettaranc.analyze"
}

func (t *AnalyzeTool) Description() string {
	// 这份清单必须与 python-service /zettaranc/analyze 实际返回的段对得上。
	// 刚删掉的「综合评分：B1评分、趋势评分、量价评分、风险评分」整条都是假的：
	// analyze 的返回只有 trend / volume / chart_pattern / levels 四段加若干
	// 元信息，一个评分字段都没有；而「B1评分」里的 B1 又与形态标注的 B1 撞名。
	//
	// 待办（未核实，不在本次改动范围）：本描述里的「三波理论阶段判断」「麒麟会」
	// 「砖型图」「四块砖」是否真有实现，需要对着 trend.py / pattern.py 逐条核。
	return `使用 Z哥交易体系对单只股票进行全面分析。

返回内容：
- 技术指标：KDJ、MACD、RSI、BBI、白线黄线、布林带、砖型图
- 波浪分析：三波理论阶段判断
- 麒麟会：庄家阶段和置信度
- 战法信号：30+ 种战法（超卖组合/B2、少妇战法、四块砖等）
- 综合诊断：买卖点判断、风险等级

数据源：DuckDB (market.duckdb + indicators.duckdb) + Python 计算

使用示例：
- 分析茅台：thscode="600519.SH"
- 分析宁德时代（60 天）：thscode="300750.SZ", days=60`
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
				"description": "分析天数（默认 120，最大 500）",
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
	if params.Days > 500 {
		params.Days = 500
	}

	// Call python-service via HTTP
	result, err := t.client.Analyze(ctx, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("分析失败：%v。请检查：1) 股票代码是否正确；2) 数据是否已同步；3) python-service 是否运行", err),
		}, nil
	}

	outputJSON, err := json.MarshalIndent(result, "", "  ")
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
