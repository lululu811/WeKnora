package futures

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// PriceHistoricalTool 返回期货合约历史日线。声明在 spec 包里，这里只转发。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。
type PriceHistoricalTool struct{ spec.Tool }

// NewPriceHistoricalTool 创建期货历史日线工具。
func NewPriceHistoricalTool(config *hithink_finance.Config) *PriceHistoricalTool {
	return &PriceHistoricalTool{spec.Tool{Config: config, Spec: spec.FuturesPriceHistorical}}
}

// Name 返回工具名。
func (t *PriceHistoricalTool) Name() string { return "hithink.finance.futures.price.historical" }

// Ensure tool implements types.Tool
var _ types.Tool = (*PriceHistoricalTool)(nil)

// PriceSnapshotTool 返回期货品种或合约的最新行情快照。
type PriceSnapshotTool struct{ spec.Tool }

// NewPriceSnapshotTool 创建期货快照工具。
func NewPriceSnapshotTool(config *hithink_finance.Config) *PriceSnapshotTool {
	return &PriceSnapshotTool{spec.Tool{Config: config, Spec: spec.FuturesPriceSnapshot}}
}

// Name 返回工具名。
func (t *PriceSnapshotTool) Name() string { return "hithink.finance.futures.price.snapshot" }

// VarietyListTool 返回期货品种清单。
type VarietyListTool struct{ spec.Tool }

// NewVarietyListTool 创建品种清单工具。
func NewVarietyListTool(config *hithink_finance.Config) *VarietyListTool {
	return &VarietyListTool{spec.Tool{Config: config, Spec: spec.FuturesVarietyList}}
}

// Name 返回工具名。
func (t *VarietyListTool) Name() string { return "hithink.finance.futures.variety.list" }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*PriceSnapshotTool)(nil)
	_ types.Tool = (*VarietyListTool)(nil)
)
