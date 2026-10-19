package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// LimitLadderDetailTool 获取 A 股连板梯队个股明细与空间板高度。
type LimitLadderDetailTool struct {
	config *hithink_finance.Config
}

func NewLimitLadderDetailTool(config *hithink_finance.Config) *LimitLadderDetailTool {
	return &LimitLadderDetailTool{config: config}
}

func (t *LimitLadderDetailTool) Name() string {
	return "hithink.finance.special.limit.ladder_detail"
}

func (t *LimitLadderDetailTool) Description() string {
	return `获取最新交易日连板梯队个股清单（市场最高空间板、各连板档位个股明细、封单金额、首次封板时间）。

返回 trade_date, continue_day_cnt, thscode, name, last_price, limit_up_time, seal_money。`
}

func (t *LimitLadderDetailTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"min_board": map[string]interface{}{
				"type":        "integer",
				"description": "最低连板天数门槛（默认 2，即 2 板及以上；设为 1 则包含首板）",
				"default":     2,
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回最多股票数（默认 50，最大 100）",
				"default":     50,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *LimitLadderDetailTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		MinBoard int `json:"min_board"`
		Limit    int `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.MinBoard <= 0 {
		params.MinBoard = 2
	}
	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 50
	}

	query := `SELECT trade_date, continue_day_cnt, thscode, name, last_price, limit_up_time, seal_money FROM v_limit_up_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool) AND continue_day_cnt >= ? ORDER BY continue_day_cnt DESC, seal_money DESC LIMIT ?`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", query, params.MinBoard, params.Limit)
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

var _ types.Tool = (*LimitLadderDetailTool)(nil)
