package financial

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这一族四个工具的形状完全一样：一个代码、一个报告期数、一张多期表、一句说明。
// 声明在 spec 包里，六步实现在 spec.Execute 里，这里只剩一个 Name()。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。

// BalanceSheetTool 返回资产负债表。
type BalanceSheetTool struct{ spec.Tool }

// NewBalanceSheetTool 创建资产负债表工具。
func NewBalanceSheetTool(config *hithink_finance.Config) *BalanceSheetTool {
	return &BalanceSheetTool{spec.Tool{Config: config, Spec: spec.FinancialBalanceSheet}}
}

// Name 返回工具名。
func (t *BalanceSheetTool) Name() string { return "hithink.finance.financial.statement.balance" }

// IncomeStatementTool 返回利润表。
type IncomeStatementTool struct{ spec.Tool }

// NewIncomeStatementTool 创建利润表工具。
func NewIncomeStatementTool(config *hithink_finance.Config) *IncomeStatementTool {
	return &IncomeStatementTool{spec.Tool{Config: config, Spec: spec.FinancialIncomeStatement}}
}

// Name 返回工具名。
func (t *IncomeStatementTool) Name() string { return "hithink.finance.financial.statement.income" }

// CashFlowStatementTool 返回现金流量表。
type CashFlowStatementTool struct{ spec.Tool }

// NewCashFlowStatementTool 创建现金流量表工具。
func NewCashFlowStatementTool(config *hithink_finance.Config) *CashFlowStatementTool {
	return &CashFlowStatementTool{spec.Tool{Config: config, Spec: spec.FinancialCashFlow}}
}

// Name 返回工具名。
func (t *CashFlowStatementTool) Name() string { return "hithink.finance.financial.statement.cashflow" }

// FinancialIndicatorDetailTool 返回财务能力指标明细。
type FinancialIndicatorDetailTool struct{ spec.Tool }

// NewFinancialIndicatorDetailTool 创建财务指标工具。
func NewFinancialIndicatorDetailTool(config *hithink_finance.Config) *FinancialIndicatorDetailTool {
	return &FinancialIndicatorDetailTool{spec.Tool{Config: config, Spec: spec.FinancialIndicatorDetail}}
}

// Name 返回工具名。
func (t *FinancialIndicatorDetailTool) Name() string {
	return "hithink.finance.financial.indicator.detail"
}

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*BalanceSheetTool)(nil)
	_ types.Tool = (*IncomeStatementTool)(nil)
	_ types.Tool = (*CashFlowStatementTool)(nil)
	_ types.Tool = (*FinancialIndicatorDetailTool)(nil)
)

// ValuationSnapshotTool 返回单只股票的最新估值快照。声明在 spec 包里，这里只转发。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。
type ValuationSnapshotTool struct{ spec.Tool }

// NewValuationSnapshotTool 创建估值快照工具。
func NewValuationSnapshotTool(config *hithink_finance.Config) *ValuationSnapshotTool {
	return &ValuationSnapshotTool{spec.Tool{Config: config, Spec: spec.FinancialValuationSnapshot}}
}

// Name 返回工具名。
func (t *ValuationSnapshotTool) Name() string { return "hithink.finance.financial.valuation.snapshot" }

// Ensure tool implements types.Tool
var _ types.Tool = (*ValuationSnapshotTool)(nil)
