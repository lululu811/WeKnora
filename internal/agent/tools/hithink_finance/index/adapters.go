package index

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// SectorDailyTool 返回板块指数日线。声明在 spec 包里，这里只转发。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。
type SectorDailyTool struct{ spec.Tool }

// NewSectorDailyTool 创建板块日线工具。
func NewSectorDailyTool(config *hithink_finance.Config) *SectorDailyTool {
	return &SectorDailyTool{spec.Tool{Config: config, Spec: spec.IndexSectorDaily}}
}

// Name 返回工具名。
func (t *SectorDailyTool) Name() string { return "hithink.finance.index.sector.daily" }

// Ensure tool implements types.Tool
var _ types.Tool = (*SectorDailyTool)(nil)

// SectorRankingTool 返回申万行业细分与概念板块的排行。
type SectorRankingTool struct{ spec.Tool }

// NewSectorRankingTool 创建板块排行工具。
func NewSectorRankingTool(config *hithink_finance.Config) *SectorRankingTool {
	return &SectorRankingTool{spec.Tool{Config: config, Spec: spec.IndexSectorRanking}}
}

// Name 返回工具名。
func (t *SectorRankingTool) Name() string { return "hithink.finance.index.sector.ranking" }

// Ensure tool implements types.Tool
var _ types.Tool = (*SectorRankingTool)(nil)

// IndexOverviewTool 返回核心宽基指数的最新点位。
type IndexOverviewTool struct{ spec.Tool }

// NewIndexOverviewTool 创建大盘概览工具。
func NewIndexOverviewTool(config *hithink_finance.Config) *IndexOverviewTool {
	return &IndexOverviewTool{spec.Tool{Config: config, Spec: spec.IndexOverview}}
}

// Name 返回工具名。
func (t *IndexOverviewTool) Name() string { return "hithink.finance.index.overview" }

// Ensure tool implements types.Tool
var _ types.Tool = (*IndexOverviewTool)(nil)
