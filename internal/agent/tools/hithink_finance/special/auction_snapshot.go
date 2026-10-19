package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// AuctionSnapshotTool 获取盘前 9:25 集合竞价异动快照。
type AuctionSnapshotTool struct {
	config *hithink_finance.Config
}

// NewAuctionSnapshotTool 创建 AuctionSnapshotTool。
func NewAuctionSnapshotTool(config *hithink_finance.Config) *AuctionSnapshotTool {
	return &AuctionSnapshotTool{config: config}
}

func (t *AuctionSnapshotTool) Name() string {
	return "hithink.finance.special.auction.snapshot"
}

func (t *AuctionSnapshotTool) Description() string {
	return `获取 9:25 盘前集合竞价异动股票（抢筹爆量、大幅超预期高开/低开）。

返回 auction_price, auction_pct, auction_amount, auction_volume_ratio, auction_turnover_pct 等。`
}

func (t *AuctionSnapshotTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"order_by": map[string]interface{}{
				"type":        "string",
				"description": "排序方式：'amount' (竞价成交额，默认) 或 'gainers' (竞价涨幅最高) 或 'losers' (竞价跌幅最深)",
				"default":     "amount",
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

func (t *AuctionSnapshotTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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

	orderClause := "auction_amount DESC"
	switch params.OrderBy {
	case "gainers":
		orderClause = "auction_pct DESC"
	case "losers":
		orderClause = "auction_pct ASC"
	}

	query := fmt.Sprintf(`SELECT snapshot_date, thscode, ticker, name, auction_price, auction_pct, auction_volume, auction_amount, auction_unmatched, auction_turnover_pct, auction_yesterday_ratio_pct, auction_volume_ratio, pre_close_price, open_price, last_price, float_market_cap, stage FROM v_auction_snapshot WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_auction_snapshot) ORDER BY %s LIMIT %d`, orderClause, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query)
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

var _ types.Tool = (*AuctionSnapshotTool)(nil)
