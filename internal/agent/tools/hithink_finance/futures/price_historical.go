package futures

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// PriceHistoricalTool 获取期货合约历史日K数据。
type PriceHistoricalTool struct {
	config *hithink_finance.Config
}

// NewPriceHistoricalTool 创建 PriceHistoricalTool。
func NewPriceHistoricalTool(config *hithink_finance.Config) *PriceHistoricalTool {
	return &PriceHistoricalTool{config: config}
}

func (t *PriceHistoricalTool) Name() string {
	return "hithink.finance.futures.price.historical"
}

func (t *PriceHistoricalTool) Description() string {
	return `获取指定期货合约（如 IFZL.CFE、CU2705.SHF）的历史日线 K 线行情。

返回 open_price, high_price, low_price, close_price, volume, turnover 等。`
}

func (t *PriceHistoricalTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "期货合约代码，如 IFZL.CFE(沪深300主力连续)、CU2705.SHF、RB2611.SHF",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "查询天数（默认 30，最大 200）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *PriceHistoricalTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
	} else if params.Days > 200 {
		params.Days = 200
	}

	query := fmt.Sprintf(`SELECT thscode, trade_date, open_price, high_price, low_price, close_price, volume, turnover FROM v_futures_daily WHERE thscode = ? ORDER BY trade_date DESC LIMIT %d`, params.Days)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "futures", query, code)
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

var _ types.Tool = (*PriceHistoricalTool)(nil)
