package halo

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// VerifyTool 复算并校验综合评分。
type VerifyTool struct {
	client *HTTPClient
}

// NewVerifyTool 创建校验工具。
func NewVerifyTool(client *HTTPClient) *VerifyTool {
	return &VerifyTool{client: client}
}

func (t *VerifyTool) Name() string {
	return "halo.verify"
}

func (t *VerifyTool) Description() string {
	return `综合评分复算与校验。给 halo.analyze 返回的七个定性维度打分之后，
用本工具复算综合分。

**必须用它算综合分，不要自己心算。** 权重是确定的九个维度（其中风险是
(10−risk)×0.10 的正向贡献），手算最容易漏掉风险那一项，或者把反向的负权重
写成正数 —— 两种错误都会静默改变全部评分。

**HALO 与成长性由服务端从事实库重算**，不接受你传入这两个值：数据层由
Python 锁定，模型不覆写。你只需要给七个定性维度的分。

**两道校验**：
1. 数值：声明分与复算分差 ≤ 0.05
2. 档位：声明评级必须与复算分同档。这一条不能省 —— 6.4955 与 6.50 只差
   0.0045，却分属「中等」和「强」两档，只比数值会放过

返回 verdict.ok 为 false 时，issues 里说明哪里不合格，按它修正重报；
不要把不合格的分数写进最终结论。

使用示例：thscode="600519", scores={moat:8, stag:7, esg:6, management:7, shareholder:5, valuation:6, risk:4}, declared_total=7.2, declared_rating="强"`

}

func (t *VerifyTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "6 位股票代码",
			},
			"period": map[string]interface{}{
				"type":        "string",
				"description": "报告期日期，如 2025-12-31。不传则取最新已落库期次。",
			},
			"report_type": map[string]interface{}{
				"type":        "string",
				"description": "披露类型：annual / h1 / q1 / q3",
				"enum":        []string{"annual", "h1", "q1", "q3"},
				"default":     "annual",
			},
			"scores": map[string]interface{}{
				"type": "object",
				"description": "七个定性维度的分数（0-10），键必须是 " +
					"moat/stag/esg/management/shareholder/valuation/risk。" +
					"HALO 与成长性由服务端重算，不要传。",
				"properties": map[string]interface{}{
					"moat":        map[string]interface{}{"type": "number", "description": "护城河"},
					"stag":        map[string]interface{}{"type": "number", "description": "滞胀防御"},
					"esg":         map[string]interface{}{"type": "number", "description": "ESG"},
					"management":  map[string]interface{}{"type": "number", "description": "管理层"},
					"shareholder": map[string]interface{}{"type": "number", "description": "股东资金面"},
					"valuation":   map[string]interface{}{"type": "number", "description": "估值"},
					"risk":        map[string]interface{}{"type": "number", "description": "风险（越高越差）"},
				},
				"required":             []string{"moat", "stag", "esg", "management", "shareholder", "valuation", "risk"},
				"additionalProperties": false,
			},
			"declared_total": map[string]interface{}{
				"type":        "number",
				"description": "你算出的综合分（0-10）。传了就校验；不传则只返回复算值。",
			},
			"declared_rating": map[string]interface{}{
				"type":        "string",
				"description": "你给出的评级：极强/强/中等/弱。传了就校验是否与复算分同档。",
				"enum":        []string{"极强", "强", "中等", "弱"},
			},
		},
		"required": []string{"thscode", "scores"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VerifyTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Thscode        string             `json:"thscode"`
		Period         string             `json:"period"`
		ReportType     string             `json:"report_type"`
		Scores         map[string]float64 `json:"scores"`
		DeclaredTotal  *float64           `json:"declared_total"`
		DeclaredRating string             `json:"declared_rating"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if len(params.Scores) == 0 {
		return &types.ToolResult{
			Success: false,
			Error:   "scores 不能为空。综合分需要七个定性维度全给分，缺一维无法复算。",
		}, nil
	}
	if params.ReportType == "" {
		params.ReportType = "annual"
	}

	payload := map[string]any{
		"thscode":         params.Thscode,
		"report_type":     params.ReportType,
		"scores":          params.Scores,
		"declared_rating": params.DeclaredRating,
	}
	if params.Period != "" {
		payload["period"] = params.Period
	}
	if params.DeclaredTotal != nil {
		payload["declared_total"] = *params.DeclaredTotal
	}

	var out map[string]any
	if err := t.client.post(ctx, "/halo/verify", payload, queryTimeout, &out); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	// 复算**成功**执行但判定不合格时，仍然 Success=true：这是一次有结果的
	// 校验，不是调用失败。issues 在返回体里，模型据此修正重报即可。
	outputJSON, _ := json.MarshalIndent(out, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*VerifyTool)(nil)
