package fund

import (
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这一族四个工具的形状完全一样：一个（可选的）代码、一个数量、一张表、一句说明。
// 声明在 spec 包里，六步实现在 spec.Execute 里，这里只剩一个 Name()。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。

// ETFSnapshotTool 返回全市场 ETF 行情快照。
type ETFSnapshotTool struct{ spec.Tool }

// NewETFSnapshotTool 创建 ETF 快照工具。
func NewETFSnapshotTool(config *hithink_finance.Config) *ETFSnapshotTool {
	return &ETFSnapshotTool{spec.Tool{Config: config, Spec: spec.FundETFSnapshot}}
}

// Name 返回工具名。
func (t *ETFSnapshotTool) Name() string { return "hithink.finance.fund.etf.snapshot" }

// ETFInstitutionalRankingTool 返回机构与散户占比的排行。
type ETFInstitutionalRankingTool struct{ spec.Tool }

// NewETFInstitutionalRankingTool 创建机构占比排行工具。
func NewETFInstitutionalRankingTool(config *hithink_finance.Config) *ETFInstitutionalRankingTool {
	return &ETFInstitutionalRankingTool{spec.Tool{Config: config, Spec: spec.FundETFInstitutionalRanking}}
}

// Name 返回工具名。
func (t *ETFInstitutionalRankingTool) Name() string {
	return "hithink.finance.fund.etf.institutional_ranking"
}

// HoldingsPortfolioTool 返回指定基金的重仓股票组合。
type HoldingsPortfolioTool struct{ spec.Tool }

// NewHoldingsPortfolioTool 创建重仓组合工具。
func NewHoldingsPortfolioTool(config *hithink_finance.Config) *HoldingsPortfolioTool {
	return &HoldingsPortfolioTool{spec.Tool{Config: config, Spec: spec.FundHoldingsPortfolio}}
}

// Name 返回工具名。
func (t *HoldingsPortfolioTool) Name() string { return "hithink.finance.fund.holdings.portfolio" }

// HoldingsStockTool 返回持有指定股票的基金明细。
type HoldingsStockTool struct{ spec.Tool }

// NewHoldingsStockTool 创建反向穿透工具。
func NewHoldingsStockTool(config *hithink_finance.Config) *HoldingsStockTool {
	return &HoldingsStockTool{spec.Tool{Config: config, Spec: spec.FundHoldingsStock}}
}

// Name 返回工具名。
func (t *HoldingsStockTool) Name() string { return "hithink.finance.fund.holdings.stock" }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*ETFSnapshotTool)(nil)
	_ types.Tool = (*ETFInstitutionalRankingTool)(nil)
	_ types.Tool = (*HoldingsPortfolioTool)(nil)
	_ types.Tool = (*HoldingsStockTool)(nil)
)

// ETFHoldersStructureTool 返回基金/ETF 的持有人结构与前十大持有人。
//
// 两条查询在声明里，结果合成一个结构——合成这一步是 Go 侧的活，于是它以
// Compose 钩子的形式留在这里。
type ETFHoldersStructureTool struct{ spec.Tool }

// NewETFHoldersStructureTool 创建持有人结构工具。
func NewETFHoldersStructureTool(config *hithink_finance.Config) *ETFHoldersStructureTool {
	t := &ETFHoldersStructureTool{}
	t.Tool = spec.Tool{
		Config:  config,
		Spec:    spec.FundHoldersStructure,
		Compose: composeHoldersStructure,
	}
	return t
}

// Name 返回工具名。
func (t *ETFHoldersStructureTool) Name() string {
	return "hithink.finance.fund.etf.holders_structure"
}

// Ensure tool implements types.Tool
var _ types.Tool = (*ETFHoldersStructureTool)(nil)

// composeHoldersStructure 把两条查询的结果合成一个结构。
func composeHoldersStructure(rows any, values map[string]any) (*types.ToolResult, error) {
	sets, _ := rows.(map[string][]map[string]any)
	result := map[string]interface{}{
		"fund_thscode": values["fund_thscode"],
		"structure":    sets["structure"],
		"top_holders":  sets["top_holders"],
	}
	resBytes, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}
	return &types.ToolResult{Success: true, Output: string(resBytes)}, nil
}
