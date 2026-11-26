package index

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这两个工具的形状不一样：
//
//   - 板块成分是"一个参数两种形态"（板块代码走 thscode 列，中文名走 name 列），
//     声明里的 Variants.Match 按值的形态选分支；可选的 tag 消歧条件在 WHERE 里
//     占一个位置。
//   - 板块归属的输出不是行——里面还有一个 tags 字段（结果里出现过的板块类型），
//     而 tags 参数是逗号分隔的多值、过滤在 Go 侧做。所以它是"Go 侧计算"那一族。
//
// 板块类型（tag）的取值与分布见 v_index_universe.tag：cn_concept / industry /
// tszs / region。

// SectorConstituentsTool —— 查某个板块里有哪些股票。
type SectorConstituentsTool struct {
	spec.Tool
}

// NewSectorConstituentsTool 创建板块成分工具。
func NewSectorConstituentsTool(config *hithink_finance.Config) *SectorConstituentsTool {
	return &SectorConstituentsTool{spec.Tool{
		Config: config,
		Spec:   spec.IndexSectorConstituents,
	}}
}

// Name returns the tool name.
func (t *SectorConstituentsTool) Name() string {
	return "hithink.finance.index.sector.constituents"
}

// Description returns the tool description.
func (t *SectorConstituentsTool) Description() string { return spec.Description(t.Spec) }

// Parameters returns the tool parameter schema.
func (t *SectorConstituentsTool) Parameters() json.RawMessage { return spec.Parameters(t.Spec) }

// SectorMembershipTool —— 查一只股票属于哪些板块。
type SectorMembershipTool struct {
	spec.Tool
}

// NewSectorMembershipTool 创建板块归属工具。
func NewSectorMembershipTool(config *hithink_finance.Config) *SectorMembershipTool {
	return &SectorMembershipTool{spec.Tool{
		Config: config,
		Spec:   spec.IndexSectorMembership,
		Fetch:  fetchSectorMembership,
		Compose: func(rows any, values map[string]any) (*types.ToolResult, error) {
			return composeSectorMembership(rows, values)
		},
	}}
}

// Name returns the tool name.
func (t *SectorMembershipTool) Name() string {
	return "hithink.finance.index.sector.membership"
}

// Description returns the tool description.
func (t *SectorMembershipTool) Description() string { return spec.Description(t.Spec) }

// Parameters returns the tool parameter schema.
func (t *SectorMembershipTool) Parameters() json.RawMessage { return spec.Parameters(t.Spec) }

// fetchSectorMembership 是板块归属的取数：一条静态查询（两个视图同属 index 库，
// 可以直接 join）。
func fetchSectorMembership(
	ctx context.Context,
	cfg *hithink_finance.Config,
	values map[string]any,
) (any, error) {
	thscode, _ := values["thscode"].(string)
	rows, err := hithink_finance.QueryDuckDBParams(ctx, cfg, "index", spec.IndexSectorMembership.SQL, thscode)
	if err != nil {
		return nil, err
	}
	return rows, nil
}

// composeSectorMembership 是板块归属自己的计算：按 tags 过滤（Go 侧，不拼进 SQL），
// 再把结果里出现过的板块类型汇总成 tags 字段。
func composeSectorMembership(rows any, values map[string]any) (*types.ToolResult, error) {
	all, ok := rows.([]map[string]any)
	if !ok {
		return &types.ToolResult{Success: false, Error: "取数结果不是行集"}, nil
	}
	thscode, _ := values["thscode"].(string)
	tags, _ := values["tags"].([]string)

	// 可选的 tag 过滤放在 Go 侧：tags 是逗号分隔的自由文本，拼进 SQL 反而多一个
	// 注入面，而候选集最多几百行，内存过滤完全够用。
	if len(tags) > 0 {
		want := make(map[string]bool, len(tags))
		for _, v := range tags {
			want[v] = true
		}
		filtered := all[:0]
		for _, r := range all {
			if want[fmt.Sprint(r["tag"])] {
				filtered = append(filtered, r)
			}
		}
		all = filtered
	}

	output := map[string]interface{}{
		"thscode":      thscode,
		"sector_count": len(all),
		"tags":         formatTags(all, "tag"),
		"data":         all,
	}
	if len(all) == 0 {
		output["hint"] = "该股票在本地板块成分表里没有记录；它可能不在 v_index_constituents 的覆盖范围内（全 A 共 5,576 只）。"
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

// formatTags 把一批 tag 渲染成可读列表。
func formatTags(rows []map[string]interface{}, key string) string {
	seen := make(map[string]bool, 4)
	var parts []string
	for _, r := range rows {
		if v, ok := r[key].(string); ok && v != "" && !seen[v] {
			seen[v] = true
			parts = append(parts, v)
		}
	}
	if len(parts) == 0 {
		return "（无）"
	}
	return strings.Join(parts, ",")
}

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*SectorConstituentsTool)(nil)
	_ types.Tool = (*SectorMembershipTool)(nil)
)
