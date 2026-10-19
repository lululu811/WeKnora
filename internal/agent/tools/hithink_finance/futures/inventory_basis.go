package futures

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// WarehouseReceiptsTool provides futures warehouse receipts and inventory changes.
type WarehouseReceiptsTool struct {
	config *hithink_finance.Config
}

// NewWarehouseReceiptsTool creates a new warehouse receipts tool.
func NewWarehouseReceiptsTool(config *hithink_finance.Config) *WarehouseReceiptsTool {
	return &WarehouseReceiptsTool{config: config}
}

func (t *WarehouseReceiptsTool) Name() string {
	return "hithink.finance.futures.inventory.warehouse_receipts"
}

func (t *WarehouseReceiptsTool) Description() string {
	return `获取期货交易所注册仓单数据与日变动（amount 仓单量、amount_change 日增减量）。
仓单大幅注销流出代表现货紧缺/去库强烈，是商品与对应 A 股周期股行情的先行指标；仓单大幅累积则预示现货供过于求。

返回字段：
- trade_date: 交易日期
- thscode: 期货合约代码（如 AL2611.SHF、RB2701.SHF）
- amount: 注册仓单数量
- amount_change: 仓单日变动量（正数累库、负数去库）
- equivalent_lots: 折合手/标准手`
}

func (t *WarehouseReceiptsTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "可选。期货合约代码，如 AL2611.SHF、RB2701.SHF。指定时查询该合约历史仓单走势",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回条数限制（默认 30）",
				"default":     30,
			},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *WarehouseReceiptsTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 {
		params.Limit = 30
	}
	if params.Limit > 100 {
		params.Limit = 100
	}

	var query string
	var bindArgs []interface{}

	if params.Thscode != "" {
		query = `
			SELECT trade_date, thscode, amount, amount_change, equivalent_lots
			FROM raw_futures_warehouse_receipts
			WHERE thscode = ?
			ORDER BY trade_date DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.Thscode, params.Limit)
	} else {
		query = `
			SELECT trade_date, thscode, amount, amount_change, equivalent_lots
			FROM raw_futures_warehouse_receipts
			WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_warehouse_receipts)
			ORDER BY ABS(amount_change) DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.Limit)
	}

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "futures", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.futures.inventory.warehouse_receipts")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未查询到仓单数据"}, nil
	}

	output := map[string]interface{}{
		"count": len(results),
		"items": results,
	}
	if params.Thscode != "" {
		output["thscode"] = params.Thscode
	}

	data, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("结果序列化失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(data),
	}, nil
}

// FuturesBasisRateTool provides spot-futures basis and contango/backwardation rate.
type FuturesBasisRateTool struct {
	config *hithink_finance.Config
}

// NewFuturesBasisRateTool creates a new futures basis rate tool.
func NewFuturesBasisRateTool(config *hithink_finance.Config) *FuturesBasisRateTool {
	return &FuturesBasisRateTool{config: config}
}

func (t *FuturesBasisRateTool) Name() string {
	return "hithink.finance.futures.basis.rate"
}

func (t *FuturesBasisRateTool) Description() string {
	return `获取大宗商品期货与现货基差及升贴水率（spot_price 现货价、close_price 期货收盘价、close_basis 基差、close_basis_rate 基差率）。
基差深贴水通常代表期货大幅折价或现货紧俏，存在基差修复与期现套利机会。`
}

func (t *FuturesBasisRateTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "可选。期货合约代码，如 CU2611.SHF、AU2612.SHF。指定时查询该合约历史基差",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回条数限制（默认 30）",
				"default":     30,
			},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *FuturesBasisRateTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 {
		params.Limit = 30
	}
	if params.Limit > 100 {
		params.Limit = 100
	}

	var query string
	var bindArgs []interface{}

	if params.Thscode != "" {
		query = `
			SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
			FROM raw_futures_basis
			WHERE thscode = ?
			ORDER BY trade_date DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.Thscode, params.Limit)
	} else {
		query = `
			SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
			FROM raw_futures_basis
			WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_basis) AND close_basis IS NOT NULL
			ORDER BY ABS(close_basis_rate) DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.Limit)
	}

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "futures", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.futures.basis.rate")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未查询到基差数据"}, nil
	}

	output := map[string]interface{}{
		"count": len(results),
		"items": results,
	}
	if params.Thscode != "" {
		output["thscode"] = params.Thscode
	}

	data, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("结果序列化失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(data),
	}, nil
}

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*WarehouseReceiptsTool)(nil)
	_ types.Tool = (*FuturesBasisRateTool)(nil)
)
