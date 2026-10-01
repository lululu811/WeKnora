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
	// 这份清单逐条对着 python-service /zettaranc/analyze 的实际返回核过
	// (main.py:1136-1176 + trend.py/volume.py/pattern.py/levels.py 的返回键)。
	//
	// 此前描述里的话**逐条是假的**：
	//   - "三波理论阶段判断" / "麒麟会：庄家阶段和置信度" —— 两个都不存在，
	//     任何 Python 模块里都没有对应实现；
	//   - "砖型图" / "四块砖" —— 不在这四个模块里（ZX_BRICK 是工作台前端的
	//     指标，见 config/indicators.yaml）；
	//   - "30+ 种战法" —— analyze 不做战法识别，那是 zettaranc.screener 的事；
	//   - "综合评分 / 风险等级 / 买卖点判断" —— 返回里一个评分字段都没有。
	// 另有一处更早删掉的"综合评分：B1评分/趋势评分/量价评分/风险评分"，同样是假的。
	//
	// 假描述的代价是确定的：模型会向用户承诺这些段，返回里没有，于是要么
	// 编一个读数，要么回答"分析失败"。所以这里只写实际存在的四段。
	return `使用 Z哥交易体系对单只股票做单维度分析。

返回四个分析段（数据不足的段为 null，原因列在 insufficient_data）：

- trend（需 20 根 K 线）：道氏 HH/HL/LH/LL 结构、均线排列与金叉死叉、
  葛兰碧法则、ADX 与 DI 多空强度、Supertrend 方向，并给出方向与置信度
- volume（需 10 根 K 线）：威科夫四阶段判定、量价配合与背离、
  Spring/Upthrust、OBV 趋势、CMF/MFI 资金流、VWAP 位置与偏离
- chart_pattern（需 10 根 K 线）：11 种蜡烛形态、头肩顶/底、双顶/双底、
  三角形/楔形/旗形、布林带形态与带宽变化、量能确认
- levels：摆动高低点、均线、整数关口、布林带边界与共振区、斐波那契回撤、
  枢轴点 PP·R1-3·S1-3

本工具**不做**：战法信号识别（用 zettaranc.screener）、全市场选股
（用 zettaranc.screener）、技术指标数值读取（用 hithink.finance.indicator.*）、
以及四块砖/三波理论/麒麟会——这三项本地无实现，用户问起要说明去看 K 线工作台。

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
