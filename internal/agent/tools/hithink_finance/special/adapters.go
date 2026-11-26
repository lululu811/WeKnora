package special

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// HotStockTrendTool 返回个股的人气排名趋势。声明在 spec 包里，这里只转发。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。
type HotStockTrendTool struct{ spec.Tool }

// NewHotStockTrendTool 创建人气排名趋势工具。
func NewHotStockTrendTool(config *hithink_finance.Config) *HotStockTrendTool {
	return &HotStockTrendTool{spec.Tool{Config: config, Spec: spec.SpecialHotStockRankTrend}}
}

// Name 返回工具名。
func (t *HotStockTrendTool) Name() string { return "hithink.finance.special.hot_stock.rank_trend" }

// Ensure tool implements types.Tool
var _ types.Tool = (*HotStockTrendTool)(nil)

// AnomalyStockTool 返回个股异动归因。
type AnomalyStockTool struct{ spec.Tool }

// NewAnomalyStockTool 创建异动归因工具。
func NewAnomalyStockTool(config *hithink_finance.Config) *AnomalyStockTool {
	return &AnomalyStockTool{spec.Tool{Config: config, Spec: spec.SpecialAnomalyStockReason}}
}

// Name 返回工具名。
func (t *AnomalyStockTool) Name() string { return "hithink.finance.special.anomaly.stock_reason" }

// AuctionSnapshotTool 返回 9:25 盘前集合竞价异动。
type AuctionSnapshotTool struct{ spec.Tool }

// NewAuctionSnapshotTool 创建集合竞价工具。
func NewAuctionSnapshotTool(config *hithink_finance.Config) *AuctionSnapshotTool {
	return &AuctionSnapshotTool{spec.Tool{Config: config, Spec: spec.SpecialAuctionSnapshot}}
}

// Name 返回工具名。
func (t *AuctionSnapshotTool) Name() string { return "hithink.finance.special.auction.snapshot" }

// LimitBreakPoolTool 返回最新交易日的炸板池。
type LimitBreakPoolTool struct{ spec.Tool }

// NewLimitBreakPoolTool 创建炸板池工具。
func NewLimitBreakPoolTool(config *hithink_finance.Config) *LimitBreakPoolTool {
	return &LimitBreakPoolTool{spec.Tool{Config: config, Spec: spec.SpecialLimitBreakPool}}
}

// Name 返回工具名。
func (t *LimitBreakPoolTool) Name() string { return "hithink.finance.special.limit.break_pool" }

// LimitLadderDetailTool 返回最新交易日的连板梯队明细。
type LimitLadderDetailTool struct{ spec.Tool }

// NewLimitLadderDetailTool 创建连板梯队工具。
func NewLimitLadderDetailTool(config *hithink_finance.Config) *LimitLadderDetailTool {
	return &LimitLadderDetailTool{spec.Tool{Config: config, Spec: spec.SpecialLimitLadderDetail}}
}

// Name 返回工具名。
func (t *LimitLadderDetailTool) Name() string { return "hithink.finance.special.limit.ladder_detail" }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*AnomalyStockTool)(nil)
	_ types.Tool = (*AuctionSnapshotTool)(nil)
	_ types.Tool = (*LimitBreakPoolTool)(nil)
	_ types.Tool = (*LimitLadderDetailTool)(nil)
)

// DragonTigerTool 返回龙虎榜。
type DragonTigerTool struct{ spec.Tool }

// NewDragonTigerTool 创建龙虎榜工具。
func NewDragonTigerTool(config *hithink_finance.Config) *DragonTigerTool {
	return &DragonTigerTool{spec.Tool{Config: config, Spec: spec.SpecialDragonTigerList}}
}

// Name 返回工具名。
func (t *DragonTigerTool) Name() string { return "hithink.finance.special.dragon_tiger.list" }

// HotStockTool 返回飙升榜。
type HotStockTool struct{ spec.Tool }

// NewHotStockTool 创建飙升榜工具。
func NewHotStockTool(config *hithink_finance.Config) *HotStockTool {
	return &HotStockTool{spec.Tool{Config: config, Spec: spec.SpecialHotStockSkyrocket}}
}

// Name 返回工具名。
func (t *HotStockTool) Name() string { return "hithink.finance.special.hot_stock.skyrocket" }

// HotStockListTool 返回全市场人气总榜。
type HotStockListTool struct{ spec.Tool }

// NewHotStockListTool 创建人气总榜工具。
func NewHotStockListTool(config *hithink_finance.Config) *HotStockListTool {
	return &HotStockListTool{spec.Tool{Config: config, Spec: spec.SpecialHotStockTopList}}
}

// Name 返回工具名。
func (t *HotStockListTool) Name() string { return "hithink.finance.special.hot_stock.top_list" }

// LimitUpPoolTool 返回涨停池。
type LimitUpPoolTool struct{ spec.Tool }

// NewLimitUpPoolTool 创建涨停池工具。
func NewLimitUpPoolTool(config *hithink_finance.Config) *LimitUpPoolTool {
	return &LimitUpPoolTool{spec.Tool{Config: config, Spec: spec.SpecialLimitUpPool}}
}

// Name 返回工具名。
func (t *LimitUpPoolTool) Name() string { return "hithink.finance.special.limit.limit_up_pool" }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*DragonTigerTool)(nil)
	_ types.Tool = (*HotStockTool)(nil)
	_ types.Tool = (*HotStockListTool)(nil)
	_ types.Tool = (*LimitUpPoolTool)(nil)
)
