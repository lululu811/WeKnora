package futures

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// PriceSnapshotTool 获取期货最新行情快照。
type PriceSnapshotTool struct {
	config *hithink_finance.Config
}

// NewPriceSnapshotTool 创建 PriceSnapshotTool。
func NewPriceSnapshotTool(config *hithink_finance.Config) *PriceSnapshotTool {
	return &PriceSnapshotTool{config: config}
}

func (t *PriceSnapshotTool) Name() string {
	return "hithink.finance.futures.price.snapshot"
}

func (t *PriceSnapshotTool) Description() string {
	return `获取指定期货品种（如 CU、RB、SC、IF、LH、AU）或合约的最新行情快照。

返回 close_price, volume, turnover, trade_date 等。`
}

func (t *PriceSnapshotTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"variety_code": map[string]interface{}{
				"type":        "string",
				"description": "期货品种代码，如 CU(沪铜)、RB(螺纹钢)、SC(原油)、IF(沪深300股指)、AU(黄金)",
			},
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "具体合约代码，如 IFZL.CFE(沪深300主力连续)、CU2705.SHF",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 20）",
				"default":     20,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *PriceSnapshotTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		VarietyCode string `json:"variety_code"`
		Thscode     string `json:"thscode"`
		Limit       int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 || params.Limit > 100 {
		params.Limit = 20
	}

	query := `SELECT thscode, variety_code, exchange_code, name, trade_date, close_price, volume, turnover FROM v_futures_latest`
	var bind []interface{}
	var conds []string

	if params.VarietyCode != "" {
		conds = append(conds, "variety_code = ?")
		bind = append(bind, strings.ToUpper(strings.TrimSpace(params.VarietyCode)))
	}
	if params.Thscode != "" {
		conds = append(conds, "thscode = ?")
		bind = append(bind, strings.TrimSpace(params.Thscode))
	}

	if len(conds) > 0 {
		query += " WHERE " + strings.Join(conds, " OR ")
	}
	query += fmt.Sprintf(` ORDER BY turnover DESC LIMIT %d`, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "futures", query, bind...)
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

var _ types.Tool = (*PriceSnapshotTool)(nil)
