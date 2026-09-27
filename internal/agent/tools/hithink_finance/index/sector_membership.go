// Package index 提供行业 / 概念 / 地域板块的工具。
//
// 这个库此前完全没有任何工具覆盖，但它恰好是"同行对比"和"板块轮动"的唯一数据源：
//
//	v_index_universe    —— 848 个板块的目录（thscode 后缀 .TI，tag 区分类型）
//	v_index_constituents —— 122,368 条成分记录，5,576 只个股（覆盖全 A）
//	v_index_daily       —— 977,170 条板块日线，2021-09-13 至今
//
// 注意 v_index_universe 是**板块目录**而不是「股票→板块」映射；归属关系在
// v_index_constituents 的 index_thscode 上。拿 universe 直接按 thscode 查股票
// 永远查不到东西。
package index

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// 板块类型。值取自 v_index_universe.tag 的真实分布：
// cn_concept 390 / industry 320 / tszs 105 / region 33。
const (
	TagConcept   = "cn_concept"
	TagIndustry  = "industry"
	TagSpecial   = "tszs"
	TagRegion    = "region"
	tagSeparator = ","
)

// describeTags 生成给模型看的板块类型说明，避免它自己猜 tag 的字面量。
func describeTags() string {
	return fmt.Sprintf(
		"板块类型 tag 取值：%s(概念板块) / %s(行业板块) / %s(特色指数，含全A与涨跌停统计) / %s(地域板块)",
		TagConcept, TagIndustry, TagSpecial, TagRegion,
	)
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
	return strings.Join(parts, tagSeparator)
}

// SectorMembershipTool —— 查一只股票属于哪些板块。
type SectorMembershipTool struct {
	config *hithink_finance.Config
}

func NewSectorMembershipTool(config *hithink_finance.Config) *SectorMembershipTool {
	return &SectorMembershipTool{config: config}
}

func (t *SectorMembershipTool) Name() string {
	return "hithink.finance.index.sector.membership"
}

func (t *SectorMembershipTool) Description() string {
	return `查一只股票所属的行业 / 概念 / 地域板块。这是做「同行对比」和「板块轮动」分析的前提：
先查这只票在哪些板块里，再用 index.sector.constituents 拉同板块的其它股票做对比。

` + describeTags() + `

返回字段：board_name(板块名), tag(板块类型), board_thscode(板块代码, .TI 结尾)
使用示例：thscode="600519.SH"`
}

func (t *SectorMembershipTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "个股代码，如 600519.SH",
			},
			"tags": map[string]interface{}{
				"type":        "string",
				"description": "可选，只返回指定类型的板块，逗号分隔。留空返回全部。",
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SectorMembershipTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.index.sector.membership"
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Tags    string `json:"tags"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}

	// 归属在 v_index_constituents 上，板块名与类型要 join v_index_universe 才拿得到。
	// 两个视图同属 index 库，可以直接 join。
	query := `
		SELECT u.name AS board_name, u.tag, u.thscode AS board_thscode
		FROM v_index_constituents c
		JOIN v_index_universe u ON u.thscode = c.index_thscode
		WHERE c.thscode = ?
		ORDER BY u.tag, u.name
	`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "index", query, params.Thscode)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	// 可选的 tag 过滤放在 Go 侧：tags 是逗号分隔的自由文本，拼进 SQL 反而多一个
	// 注入面，而候选集最多几百行，内存过滤完全够用。
	if raw := strings.TrimSpace(params.Tags); raw != "" {
		want := make(map[string]bool)
		for _, t := range strings.Split(raw, tagSeparator) {
			if v := strings.TrimSpace(t); v != "" {
				want[v] = true
			}
		}
		filtered := results[:0]
		for _, r := range results {
			if want[fmt.Sprint(r["tag"])] {
				filtered = append(filtered, r)
			}
		}
		results = filtered
	}

	output := map[string]interface{}{
		"thscode":      params.Thscode,
		"sector_count": len(results),
		"tags":         formatTags(results, "tag"),
		"data":         results,
	}
	if len(results) == 0 {
		output["hint"] = "该股票在本地板块成分表里没有记录；它可能不在 v_index_constituents 的覆盖范围内（全 A 共 5,576 只）。"
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SectorMembershipTool)(nil)
