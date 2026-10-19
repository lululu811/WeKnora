package index

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// SectorRankingTool 获取行业或概念板块涨跌幅与成交额排行。
type SectorRankingTool struct {
	config *hithink_finance.Config
}

// NewSectorRankingTool 创建 SectorRankingTool。
func NewSectorRankingTool(config *hithink_finance.Config) *SectorRankingTool {
	return &SectorRankingTool{config: config}
}

func (t *SectorRankingTool) Name() string {
	return "hithink.finance.index.sector.ranking"
}

func (t *SectorRankingTool) Description() string {
	return `获取申万行业细分或热门概念板块（如 AI、半导体、低空经济等）的最新排行。

用于发现市场领涨领跌主线及资金成交焦点。`
}

func (t *SectorRankingTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"tag": map[string]interface{}{
				"type":        "string",
				"description": "板块类型：'industry' (行业细分，默认) 或 'cn_concept' (前沿概念)",
				"default":     "industry",
			},
			"order_by": map[string]interface{}{
				"type":        "string",
				"description": "排序方式：'gainers' (领涨榜，默认) 或 'losers' (领跌榜) 或 'turnover' (成交额榜)",
				"default":     "gainers",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 20，最大 60）",
				"default":     20,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SectorRankingTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Tag     string `json:"tag"`
		OrderBy string `json:"order_by"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Tag != "cn_concept" {
		params.Tag = "industry"
	}

	if params.Limit <= 0 || params.Limit > 60 {
		params.Limit = 20
	}

	orderClause := "price_change_ratio DESC"
	switch params.OrderBy {
	case "losers":
		orderClause = "price_change_ratio ASC"
	case "turnover":
		orderClause = "turnover DESC"
	}

	query := fmt.Sprintf(`SELECT snapshot_date, thscode, name, tag, last_price, price_change, price_change_ratio, volume, turnover FROM v_index_latest WHERE tag = ? ORDER BY %s LIMIT %d`, orderClause, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "index", query, params.Tag)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	resBytes, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*SectorRankingTool)(nil)
