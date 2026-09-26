// Package zettarancserv provides registration for zettaranc tools.
package zettarancserv

import (
	"os"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/zettaranc"
)

// RegisterZettarancTools registers all zettaranc tools to the tool registry.
//
// All tools (analyze, screener, backtest) now call the python-service HTTP API.
// The legacy zettaranc-skill Python CLI is no longer required.
func RegisterZettarancTools(registry *tools.ToolRegistry, config *zettaranc.Config) error {
	serviceURL := os.Getenv("PYTHON_SERVICE_URL")
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}
	httpClient := zettaranc.NewHTTPClient(serviceURL)

	registry.RegisterTool(zettaranc.NewAnalyzeTool(httpClient))
	registry.RegisterTool(zettaranc.NewScreenerTool(httpClient))
	registry.RegisterTool(zettaranc.NewBacktestTool(httpClient))

	return nil
}