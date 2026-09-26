// Package zettarancserv provides registration for zettaranc tools.
package zettarancserv

import (
	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/zettaranc"
)

// RegisterZettarancTools registers all zettaranc tools to the tool registry.
func RegisterZettarancTools(registry *tools.ToolRegistry, config *zettaranc.Config) error {
	client := zettaranc.NewCLIClient(config)

	// Register analyze tool
	registry.RegisterTool(zettaranc.NewAnalyzeTool(client))

	// Register backtest tool
	registry.RegisterTool(zettaranc.NewBacktestTool(client))

	// Register screener tool
	registry.RegisterTool(zettaranc.NewScreenerTool(client))

	return nil
}
