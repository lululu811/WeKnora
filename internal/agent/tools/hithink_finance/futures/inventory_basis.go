package futures

import (
	"encoding/json"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这两个工具的形状是「一个（可选的）代码 + 一个条数 + 一张表」：给了合约代码就
// 查那个合约的历史，没给就查最新交易日全市场。两条 SQL 与绑定值都写在声明里
// （spec.FuturesInventoryWarehouseReceipts / spec.FuturesBasisRate），这里只剩
// 一个 Name()。

// WarehouseReceiptsTool provides futures warehouse receipts and inventory changes.
type WarehouseReceiptsTool struct {
	spec.Tool
}

// NewWarehouseReceiptsTool creates a new warehouse receipts tool.
func NewWarehouseReceiptsTool(config *hithink_finance.Config) *WarehouseReceiptsTool {
	return &WarehouseReceiptsTool{spec.Tool{
		Config: config,
		Spec:   spec.FuturesInventoryWarehouseReceipts,
	}}
}

// Name returns the tool name.
func (t *WarehouseReceiptsTool) Name() string {
	return "hithink.finance.futures.inventory.warehouse_receipts"
}

// Description returns the tool description.
func (t *WarehouseReceiptsTool) Description() string { return spec.Description(t.Spec) }

// Parameters returns the tool parameter schema.
func (t *WarehouseReceiptsTool) Parameters() json.RawMessage { return spec.Parameters(t.Spec) }

// FuturesBasisRateTool provides spot-futures basis and contango/backwardation rate.
type FuturesBasisRateTool struct {
	spec.Tool
}

// NewFuturesBasisRateTool creates a new futures basis rate tool.
func NewFuturesBasisRateTool(config *hithink_finance.Config) *FuturesBasisRateTool {
	return &FuturesBasisRateTool{spec.Tool{
		Config: config,
		Spec:   spec.FuturesBasisRate,
	}}
}

// Name returns the tool name.
func (t *FuturesBasisRateTool) Name() string {
	return "hithink.finance.futures.basis.rate"
}

// Description returns the tool description.
func (t *FuturesBasisRateTool) Description() string { return spec.Description(t.Spec) }

// Parameters returns the tool parameter schema.
func (t *FuturesBasisRateTool) Parameters() json.RawMessage { return spec.Parameters(t.Spec) }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*WarehouseReceiptsTool)(nil)
	_ types.Tool = (*FuturesBasisRateTool)(nil)
)
