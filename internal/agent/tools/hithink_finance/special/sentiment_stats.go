package special

import (
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// SentimentStatsTool 获取短线市场情绪统计指标。
//
// 这个工具是"多查询 + Go 侧聚合"那一族的代表：三条查询（涨停池 / 跌停池 /
// 炸板池）在声明里，执行器跑；炸板率与最高连板在这里算。
type SentimentStatsTool struct {
	spec.Tool
}

// NewSentimentStatsTool 创建 SentimentStatsTool。
func NewSentimentStatsTool(config *hithink_finance.Config) *SentimentStatsTool {
	t := &SentimentStatsTool{}
	t.Tool = spec.Tool{
		Config: config,
		Spec:   spec.SpecialSentimentStats,
		Compose: func(rows any, values map[string]any) (*types.ToolResult, error) {
			return t.compose(rows)
		},
	}
	return t
}

// Name 返回工具名。
func (t *SentimentStatsTool) Name() string { return "hithink.finance.special.sentiment.stats" }

// Description 返回给模型看的说明。
func (t *SentimentStatsTool) Description() string { return spec.Description(t.Spec) }

// Parameters 返回给模型看的参数 schema。
func (t *SentimentStatsTool) Parameters() json.RawMessage { return spec.Parameters(t.Spec) }

// compose 是这个工具自己的聚合：把三张池子的行算成情绪统计。
//
// 三条查询在声明里（spec.SpecialSentimentStats），由执行器跑；这里只剩"算"。
func (t *SentimentStatsTool) compose(rows any) (*types.ToolResult, error) {
	sets, ok := rows.(map[string][]map[string]any)
	if !ok {
		return &types.ToolResult{Success: false, Error: "取数结果不是分组行集"}, nil
	}
	upRows := sets["limit_up"]
	downRows := sets["limit_down"]
	breakRows := sets["limit_break"]

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
