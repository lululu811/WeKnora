package halo

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// QueryTool 读取已落库的年报事实。
type QueryTool struct {
	client *HTTPClient
}

// NewQueryTool 创建查询工具。
func NewQueryTool(client *HTTPClient) *QueryTool {
	return &QueryTool{client: client}
}

func (t *QueryTool) Name() string {
	return "halo.filing.query"
}

func (t *QueryTool) Description() string {
	return `读取已落库的年报事实（来自 halo.filing.sync）。用于取本地财务数据源没有的字段：
固定资产、在建工程、存货、无形资产、商誉、员工人数。

**取哪些字段**：
  fixed_assets 固定资产 / construction_in_progress 在建工程 /
  inventory 存货 / intangible_assets 无形资产 / goodwill 商誉 /
  employees_total 在职员工数量合计

**默认只返回 verified**（两条抽取通道一致且与本地库对账通过）。
把 only_verified 设为 false 可以看到 disputed / pending 的记录——但那些数字
**未经交叉验证，不能当分析依据**，回答用户时必须标注存疑，更不能拿它做算术。
任何情况下都不要因为某个字段取不到就估算或用别的口径顶替，直接说取不到。

**scope 区分报表口径**：consolidated=合并报表（默认，HALO 用这个）、
parent=母公司报表。同一家公司两套数字不同，混用会得出错误结论。
若同一字段同时返回两种 scope，说明你误查了，按 consolidated 取。

**金额单位统一为元**（CNY），员工人数单位为人（person）。

**没数据时先看 status_summary**：verified 为 0 通常说明该股票还没同步过，
调用 halo.filing.sync 补数据，而不是改用别的数据源凑。

使用示例：thscode="600519", report_type="annual"`

}

func (t *QueryTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "6 位股票代码，如 600519",
			},
			"period": map[string]interface{}{
				"type":        "string",
				"description": "报告期日期，如 2025-12-31。不传则返回全部已落库期次。",
			},
			"report_type": map[string]interface{}{
				"type":        "string",
				"description": "披露类型过滤：annual / h1 / q1 / q3",
				"enum":        []string{"annual", "h1", "q1", "q3"},
			},
			"fields": map[string]interface{}{
				"type":        "array",
				"items":       map[string]interface{}{"type": "string"},
				"description": "只要指定字段。不传则返回该股票所有已落库字段。",
			},
			"scope": map[string]interface{}{
				"type":        "string",
				"description": "报表口径：consolidated=合并报表（默认）/ parent=母公司报表",
				"enum":        []string{"consolidated", "parent"},
			},
			"only_verified": map[string]interface{}{
				"type":        "boolean",
				"description": "是否只返回可信记录。默认 true；设 false 会带回 disputed/pending，这些数未经交叉验证。",
				"default":     true,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *QueryTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params QueryRequest
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}

	result, err := t.client.Query(ctx, params)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	outputJSON, _ := json.MarshalIndent(result, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*QueryTool)(nil)
