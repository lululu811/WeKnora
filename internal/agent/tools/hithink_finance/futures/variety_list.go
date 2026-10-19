package futures

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// VarietyListTool 列出全部/指定交易所的期货品种清单。
type VarietyListTool struct {
	config *hithink_finance.Config
}

// NewVarietyListTool 创建 VarietyListTool。
func NewVarietyListTool(config *hithink_finance.Config) *VarietyListTool {
	return &VarietyListTool{config: config}
}

func (t *VarietyListTool) Name() string {
	return "hithink.finance.futures.variety.list"
}

func (t *VarietyListTool) Description() string {
	return `获取期货品种清单（含品种代码、名称、交易所、保证金比例、主力合约代码等）。

支持大宗商品（铜、铝、原油、黄金、螺纹钢、生猪等）与金融期货（IF、IC、IM、IH、国债）。`
}

func (t *VarietyListTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"exchange_code": map[string]interface{}{
				"type":        "string",
				"description": "交易所代码可选过滤：SHFE(上期所)、DCE(大商所)、CZCE(郑商所)、CFFEX(中金所)、INE(能源中心)",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 100）",
				"default":     100,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VarietyListTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		ExchangeCode string `json:"exchange_code"`
		Limit        int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 || params.Limit > 200 {
		params.Limit = 100
	}

	query := `SELECT variety_code, exchange_code, name, has_night_session, margin_rate, main_contract_thscode, trade_unit FROM v_futures_varieties`
	var bind []interface{}
	if params.ExchangeCode != "" {
		query += ` WHERE exchange_code = ?`
		bind = append(bind, params.ExchangeCode)
	}
	query += fmt.Sprintf(` ORDER BY variety_code LIMIT %d`, params.Limit)

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

var _ types.Tool = (*VarietyListTool)(nil)
