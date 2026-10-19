package fund

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// ETFSnapshotTool 获取全市场 ETF 的最新行情快照。
type ETFSnapshotTool struct {
	config *hithink_finance.Config
}

// NewETFSnapshotTool 创建 ETFSnapshotTool。
func NewETFSnapshotTool(config *hithink_finance.Config) *ETFSnapshotTool {
	return &ETFSnapshotTool{config: config}
}

func (t *ETFSnapshotTool) Name() string {
	return "hithink.finance.fund.etf.snapshot"
}

func (t *ETFSnapshotTool) Description() string {
	return `获取全市场 ETF 最新行情快照（包含宽基 ETF、行业主题 ETF、商品 ETF 等）。

支持按成交额排序（发现活跃 ETF）或按涨幅排序。`
}

func (t *ETFSnapshotTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"order_by": map[string]interface{}{
				"type":        "string",
				"description": "排序方式：'turnover' (成交额优先，默认) 或 'gainers' (涨幅优先) 或 'losers' (跌幅优先)",
				"default":     "turnover",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 30，最大 100）",
				"default":     30,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ETFSnapshotTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		OrderBy string `json:"order_by"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 30
	}

	orderClause := "turnover DESC"
	switch params.OrderBy {
	case "gainers":
		orderClause = "change_pct DESC"
	case "losers":
		orderClause = "change_pct ASC"
	}

	query := fmt.Sprintf(`SELECT thscode, ticker, name, exchange, asset_type, trade_date, last_price, change_pct, volume, turnover, turnover_ratio_pct FROM v_etf_latest ORDER BY %s LIMIT %d`, orderClause, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "fund", query)
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

var _ types.Tool = (*ETFSnapshotTool)(nil)
