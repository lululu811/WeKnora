package index

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// SectorDailyTool —— 查板块指数日线，用于板块轮动/强弱对比。
type SectorDailyTool struct {
	config *hithink_finance.Config
}

func NewSectorDailyTool(config *hithink_finance.Config) *SectorDailyTool {
	return &SectorDailyTool{config: config}
}

func (t *SectorDailyTool) Name() string {
	return "hithink.finance.index.sector.daily"
}

func (t *SectorDailyTool) Description() string {
	return `查板块指数的日线行情，用于板块轮动与强弱对比：拿到同一天多个板块的 close，
自己算区间涨幅即可比较哪个板块在领涨/掉队。

thscode 传板块代码（.TI 结尾，如 881101.TI）。不知道板块代码时先用
hithink.finance.query.sql 查：SELECT thscode, name, tag FROM v_index_universe WHERE name LIKE '%关键词%'

数据区间 2021-09-13 至今，约 977,170 条。
使用示例：thscode="881101.TI", days=30`
}

func (t *SectorDailyTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "板块指数代码，.TI 结尾，如 881101.TI（种植业与林业）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "返回最近多少个交易日（默认 30，最大 800）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SectorDailyTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	const toolName = "hithink.finance.index.sector.daily"
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if strings.TrimSpace(params.Thscode) == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 30
	}
	if params.Days > 800 {
		params.Days = 800
	}

	// 只取时间序列必需的列，避开 raw_payload（整行原始 JSON，会把工具输出预算吃光）。
	// 默认 MaxToolOutput 是 24000 rune，raw_payload 进来必然被截断。
	query := `
		SELECT trade_date, open, high, low, close, volume, turnover
		FROM v_index_daily
		WHERE thscode = ?
		ORDER BY trade_date DESC
		LIMIT ?
	`
	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "index", query, strings.ToUpper(params.Thscode), params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, toolName)}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"count":   len(results),
		"data":    results,
	}
	if len(results) == 0 {
		output["hint"] = "没有该板块的日线数据。板块代码是 .TI 结尾（不是 .SH/.SZ），" +
			"可用 hithink.finance.query.sql 查 v_index_universe 确认代码。"
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SectorDailyTool)(nil)
