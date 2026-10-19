package special

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// SentimentStatsTool 获取短线市场情绪统计指标。
type SentimentStatsTool struct {
	config *hithink_finance.Config
}

// NewSentimentStatsTool 创建 SentimentStatsTool。
func NewSentimentStatsTool(config *hithink_finance.Config) *SentimentStatsTool {
	return &SentimentStatsTool{config: config}
}

func (t *SentimentStatsTool) Name() string {
	return "hithink.finance.special.sentiment.stats"
}

func (t *SentimentStatsTool) Description() string {
	return `获取市场短线情绪综合统计（涨停家数、跌停家数、炸板家数、炸板率、最高连板高度等）。

用于研判短线情绪周期（启动/发酵/高潮/退潮）。`
}

func (t *SentimentStatsTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type":       "object",
		"properties": map[string]interface{}{},
		"required":   []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SentimentStatsTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	upQuery := `SELECT trade_date, thscode, name, continue_day_cnt, seal_money FROM v_limit_up_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool)`
	upRows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", upQuery)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	downQuery := `SELECT trade_date, thscode, name, price_change_ratio FROM v_limit_down_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_down_pool)`
	downRows, _ := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", downQuery)

	breakQuery := `SELECT trade_date, thscode, name, open_times, price_change_ratio FROM v_limit_break_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_break_pool)`
	breakRows, _ := hithink_finance.QueryDuckDBParams(ctx, t.config, "special", breakQuery)

	tradeDate := ""
	limitUpCount := len(upRows)
	limitDownCount := len(downRows)
	brokenCount := len(breakRows)

	maxStreak := 1
	var streakName string
	for _, r := range upRows {
		if d, ok := r["trade_date"].(string); ok && tradeDate == "" {
			tradeDate = d
		}
		if cnt, ok := r["continue_day_cnt"].(float64); ok && int(cnt) > maxStreak {
			maxStreak = int(cnt)
			if n, ok := r["name"].(string); ok {
				streakName = n
			}
		}
	}

	brokenRate := 0.0
	totalUpAndBreak := limitUpCount + brokenCount
	if totalUpAndBreak > 0 {
		brokenRate = float64(brokenCount) / float64(totalUpAndBreak) * 100.0
	}

	result := map[string]interface{}{
		"trade_date":       tradeDate,
		"limit_up_count":   limitUpCount,
		"limit_down_count": limitDownCount,
		"broken_count":     brokenCount,
		"broken_rate_pct":  fmt.Sprintf("%.1f%%", brokenRate),
		"max_streak":       maxStreak,
		"max_streak_stock": streakName,
	}

	resBytes, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*SentimentStatsTool)(nil)
