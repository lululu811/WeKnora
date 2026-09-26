// Package zettarancserv provides registration for zettaranc tools.
package zettarancserv

import (
	"os"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/zettaranc"
)

// RegisterZettarancTools registers all zettaranc tools to the tool registry.
//
// analyze uses HTTPClient → python-service /zettaranc/analyze (DuckDB-based).
// backtest and screener still use CLIClient → Python CLI subprocess.
func RegisterZettarancTools(registry *tools.ToolRegistry, config *zettaranc.Config) error {
	// Subprocess client for backtest + screener
	subClient := zettaranc.NewCLIClient(config)
	registry.RegisterTool(zettaranc.NewBacktestTool(subClient))
	registry.RegisterTool(zettaranc.NewScreenerTool(subClient))

	// HTTP client for analyze (python-service)
	serviceURL := os.Getenv("PYTHON_SERVICE_URL")
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}
	httpClient := zettaranc.NewHTTPClient(serviceURL)
	registry.RegisterTool(zettaranc.NewAnalyzeTool(httpClient))

	return nil
}
