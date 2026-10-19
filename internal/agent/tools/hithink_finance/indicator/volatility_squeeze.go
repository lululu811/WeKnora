package indicator

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// VolatilitySqueezeTool 获取股票约翰·卡特 TTM Squeeze 波动挤压指标与动能状态。
type VolatilitySqueezeTool struct {
	config *hithink_finance.Config
}

// NewVolatilitySqueezeTool 创建 VolatilitySqueezeTool。
func NewVolatilitySqueezeTool(config *hithink_finance.Config) *VolatilitySqueezeTool {
	return &VolatilitySqueezeTool{config: config}
}

func (t *VolatilitySqueezeTool) Name() string {
	return "hithink.finance.indicator.volatility.squeeze"
}

func (t *VolatilitySqueezeTool) Description() string {
	return `获取股票经典 TTM Squeeze 波动挤压指标（布林线收缩于肯特纳通道内部的蓄势爆发状态）与动能柱。
返回 date, 布林上下轨, 肯特纳上下轨, MACD柱动能, 判定挤压状态 (is_squeezing)。`
}

func (t *VolatilitySqueezeTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 000001.SZ 或 600519.SH）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "回看天数（默认 30，最大 200）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VolatilitySqueezeTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	code := strings.TrimSpace(params.Thscode)
	if code == "" {
		return &types.ToolResult{Success: false, Error: "thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 30
	}
	if params.Days > 200 {
		params.Days = 200
	}

	query := fmt.Sprintf(`SELECT date, volatility_bbands_20_2_0_upper, volatility_bbands_20_2_0_lower, volatility_kc_20_2_upper, volatility_kc_20_2_lower, momentum_macd_12_26_9_hist FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT %d`, params.Days)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, code)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	output := map[string]interface{}{
		"thscode": code,
		"days":    len(rows),
		"data":    rows,
	}

	resBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

// TrendSupertrendTool 获取股票超级趋势 (Supertrend) 量化趋势追踪方向与贝塔系数。
type TrendSupertrendTool struct {
	config *hithink_finance.Config
}

// NewTrendSupertrendTool 创建 TrendSupertrendTool。
func NewTrendSupertrendTool(config *hithink_finance.Config) *TrendSupertrendTool {
	return &TrendSupertrendTool{config: config}
}

func (t *TrendSupertrendTool) Name() string {
	return "hithink.finance.indicator.trend.supertrend"
}

func (t *TrendSupertrendTool) Description() string {
	return `获取股票超级趋势 (Supertrend 10,3) 顺势指标与贝塔系数 Beta。
返回 date, trend (趋势轨), direction (1=多头顺势牛, -1=空头防守熊), long (多头保护位), short (空头保护位), beta (20日Beta)。`
}

func (t *TrendSupertrendTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 000001.SZ 或 600519.SH）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "回看天数（默认 30，最大 200）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *TrendSupertrendTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	code := strings.TrimSpace(params.Thscode)
	if code == "" {
		return &types.ToolResult{Success: false, Error: "thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 30
	}
	if params.Days > 200 {
		params.Days = 200
	}

	query := fmt.Sprintf(`SELECT date, trend_supertrend_10_3_0_trend, trend_supertrend_10_3_0_direction, trend_supertrend_10_3_0_long, trend_supertrend_10_3_0_short, statistics_beta_20 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT %d`, params.Days)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, code)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	output := map[string]interface{}{
		"thscode": code,
		"days":    len(rows),
		"data":    rows,
	}

	resBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var (
	_ types.Tool = (*VolatilitySqueezeTool)(nil)
	_ types.Tool = (*TrendSupertrendTool)(nil)
)
