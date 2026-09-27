package index

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// SectorConstituentsTool —— 查某个板块里有哪些股票。
type SectorConstituentsTool struct {
	config *hithink_finance.Config
}

func NewSectorConstituentsTool(config *hithink_finance.Config) *SectorConstituentsTool {
	return &SectorConstituentsTool{config: config}
}

func (t *SectorConstituentsTool) Name() string {
	return "hithink.finance.index.sector.constituents"
}

func (t *SectorConstituentsTool) Description() string {
	return `查某个板块的成分股列表。配合 index.sector.membership 使用即可做「同行对比」：
先查某只票在哪些板块，再用本工具拉同板块的其它票。

sector 可以传板块名（如 "白酒"）或板块代码（如 "881101.TI"）。
板块名是中文且可能重名，强烈建议带上 tag 消歧（如 tag="industry"）。

` + describeTags() + `

注意：v_index_constituents 只有**当前**成分快照，没有历史成分，做历史回测会有幸存者偏差。
使用示例：sector="白酒", tag="industry", limit=50`
}

func (t *SectorConstituentsTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"sector": map[string]interface{}{
				"type":        "string",
				"description": "板块名（中文，如 白酒）或板块代码（如 881101.TI）",
			},
			"tag": map[string]interface{}{
				"type":        "string",
				"description": "板块类型，可选：cn_concept / industry / tszs / region，用于同名消歧",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回条数上限（默认 50，最大 500）",
				"default":     50,
			},
		},
		"required": []string{"sector"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SectorConstituentsTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.index.sector.constituents"
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	var params struct {
		Sector string `json:"sector"`
		Tag    string `json:"tag"`
		Limit  int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if strings.TrimSpace(params.Sector) == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：sector 不能为空"}, nil
	}
	if params.Limit <= 0 {
		params.Limit = 50
	}
	if params.Limit > 500 {
		params.Limit = 500
	}

	// sector 可能是板块代码（881101.TI）也可能是中文名，两种都支持。
	// tag 作为可选的 SQL 条件用绑定参数传入，不做字符串拼接。
	byCode := strings.HasSuffix(strings.ToUpper(params.Sector), ".TI")

	var where string
	var bind []interface{}
	if byCode {
		where = "u.thscode = ?"
		bind = append(bind, strings.ToUpper(params.Sector))
	} else {
		where = "u.name = ?"
		bind = append(bind, strings.TrimSpace(params.Sector))
	}
	if tag := strings.TrimSpace(params.Tag); tag != "" {
		where += " AND u.tag = ?"
		bind = append(bind, tag)
	}
	bind = append(bind, params.Limit)

	query := `
		SELECT c.thscode, c.ticker, c.name, u.name AS board_name, u.tag
		FROM v_index_constituents c
		JOIN v_index_universe u ON u.thscode = c.index_thscode
		WHERE ` + where + `
		ORDER BY c.thscode
		LIMIT ?
	`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "index", query, bind...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	output := map[string]interface{}{
		"sector": params.Sector,
		"tag":    params.Tag,
		"count":  len(results),
		"data":   results,
	}
	if len(results) == 0 {
		// 板块名重名是真实存在的（848 个板块里中文名可能重复），所以命中失败时
		// 直接告诉模型怎么改写查询，而不是只说"没找到"。
		output["hint"] = "没有匹配的板块成分。可先用 hithink.finance.query.sql 执行 " +
			"SELECT thscode, name, tag FROM v_index_universe WHERE name LIKE '%关键词%' 确认板块名与 tag 的准确写法，" +
			"再带 tag 参数重试。"
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SectorConstituentsTool)(nil)
