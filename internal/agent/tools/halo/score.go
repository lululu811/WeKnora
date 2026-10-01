package halo

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// ScoreTool 调用 python-service 的评分端点。
type ScoreTool struct {
	client *HTTPClient
}

// NewScoreTool 创建评分工具。
func NewScoreTool(client *HTTPClient) *ScoreTool {
	return &ScoreTool{client: client}
}

func (t *ScoreTool) Name() string {
	return "halo.analyze"
}

func (t *ScoreTool) Description() string {
	return `对一只 A 股出 HALO 评分：Python 锁定数据层算出 HALO 六维与成长性，
并返回 7 个定性维度的**待判分槽位**及其量化锚点。

**工具算出什么**（确定性，可直接引用）：
  asset_type  重/混合/轻资产，按「固定资产占总资产」判定，行业名仅作校验
  halo        HALO 六维总分（5 分制）与逐维得分
  growth      成长性总分（10 分制）与四个子项
  facts       治理诚信事实：审计意见类型、内控审计意见、是否被出具非标内控意见、
              近三年是否受证券监管处罚、董监高是否被处罚（均来自年报固定章节）
  ai_slots    7 个定性维度的量化锚点，score 字段为 null，等你填

**你负责的部分**：ai_slots 里 7 个维度各给 0–10 分。给分前先看 anchors，
不要凭印象；has_anchor 为 false 的维度只做定性判断，缺失锚点已列出。

**综合分不要自己算**：九个维度分给全之后调 halo.verify 复算。权重是确定的，
手算容易漏掉风险的负向项。校验容差 0.05，且声明评级必须与复算分同档
（分差 0.0045 但跨了 6.5 档位也会被判不合格）。

**缺数据的处理**：HALO 缺任一必需输入时，halo.ok 为 false 且 reason 说明缺哪项。
此时**不要**自己补数或用别的口径顶替，综合分的 HALO 一项也无法计算 ——
按铁律标缺失，并在回答里如实说明。

只有 status=verified 的事实才会进入评分，disputed/pending 的记录已被排除。

使用示例：thscode="600519", report_type="annual"`

}

func (t *ScoreTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "6 位股票代码，如 600519",
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
			"scope": map[string]interface{}{
				"type":        "string",
				"description": "报表口径：consolidated=合并报表（默认）/ parent=母公司报表",
				"enum":        []string{"consolidated", "parent"},
				"default":     "consolidated",
			},
			"include_external": map[string]interface{}{
				"type": "boolean",
				"description": "额外拉外网数据：治理与风险硬信号（股权质押、股东增减持、业绩预告、" +
					"机构调研）、PE/PB/PS/PCF 历史分位、研报评级。默认关闭 —— 分析是按需行为，" +
					"外网慢且会触发 IP 封禁；用户问「最近有什么减持/研报怎么看/现在贵不贵」时再开。",
				"default": false,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ScoreTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Thscode    string `json:"thscode"`
		Period     string `json:"period"`
		ReportType string `json:"report_type"`
		Scope      string `json:"scope"`
		IncludeExt bool   `json:"include_external"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.ReportType == "" {
		params.ReportType = "annual"
	}
	if params.Scope == "" {
		params.Scope = "consolidated"
	}

	var out map[string]any
	req := map[string]any{
		"thscode":          params.Thscode,
		"period":           params.Period,
		"report_type":      params.ReportType,
		"scope":            params.Scope,
		"include_external": params.IncludeExt,
	}
	if err := t.client.post(ctx, "/halo/score", req, queryTimeout, &out); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	// 事实库为空时返回的是"没数据"，不是错误。明确区分开，否则 agent 会把
	// "该股票还没同步年报"当成"服务挂了"而反复重试。
	if ok, exists := out["ok"]; exists {
		if b, isBool := ok.(bool); isBool && !b {
			reason, _ := out["reason"].(string)
			if reason == "" {
				reason = "该股票没有已落库的年报事实"
			}
			return &types.ToolResult{Success: false, Error: reason}, nil
		}
	}

	outputJSON, _ := json.MarshalIndent(out, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*ScoreTool)(nil)
