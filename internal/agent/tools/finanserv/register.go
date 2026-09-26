// Package finanserv provides registration for hithink finance tools.
// This package exists to avoid import cycles.
package finanserv

import (
	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/financial"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/indicator"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/market"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/query"
)

// RegisterHithinkFinanceTools registers all hithink finance tools to the tool registry.
func RegisterHithinkFinanceTools(registry *tools.ToolRegistry, config *hithink_finance.Config) error {
	// Register discovery tool
	registry.RegisterTool(hithink_finance.NewDiscoverTool(registry))

	// Register market tools
	registry.RegisterTool(market.NewPriceSnapshotTool(config))
	registry.RegisterTool(market.NewPriceHistoricalTool(config))

	// Register financial tools
	registry.RegisterTool(financial.NewValuationSnapshotTool(config))

	// Register indicator tools
	registry.RegisterTool(indicator.NewTrendMATool(config))

	// Register query tools
	registry.RegisterTool(query.NewSQLQueryTool(config))

	return nil
}
