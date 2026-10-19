package index

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// IndexOverviewTool 获取核心宽基大盘指数最新行情概览。
type IndexOverviewTool struct {
	config *hithink_finance.Config
}

// NewIndexOverviewTool 创建 IndexOverviewTool。
func NewIndexOverviewTool(config *hithink_finance.Config) *IndexOverviewTool {
	return &IndexOverviewTool{config: config}
}

func (t *IndexOverviewTool) Name() string {
	return "hithink.finance.index.overview"
}

func (t *IndexOverviewTool) Description() string {
	return `获取核心宽基指数（上证指数、深证成指、创业板指、科创50、沪深300、中证500、中证1000等）的最新点位、涨跌幅、成交额。

用于研判全市场大盘走势与宏观赚钱效应。`
}

func (t *IndexOverviewTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type":       "object",
		"properties": map[string]interface{}{},
		"required":   []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *IndexOverviewTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	query := `SELECT snapshot_date, thscode, name, tag, last_price, price_change, price_change_ratio, open, high, low, volume, turnover FROM v_index_latest WHERE thscode IN ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH', '000300.SH', '000905.SH', '000852.SH') ORDER BY thscode`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "index", query)
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

var _ types.Tool = (*IndexOverviewTool)(nil)
