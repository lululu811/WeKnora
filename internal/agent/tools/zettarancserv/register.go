// Package zettarancserv provides registration for zettaranc tools.
package zettarancserv

import (
	"os"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/zettaranc"
)

// RegisterZettarancTools registers all zettaranc tools to the tool registry.
//
// All tools (analyze, screener) now call the python-service HTTP API.
// The legacy zettaranc-skill Python CLI is no longer required.
func RegisterZettarancTools(registry *tools.ToolRegistry, config *zettaranc.Config) error {
	serviceURL := os.Getenv("PYTHON_SERVICE_URL")
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}
	httpClient := zettaranc.NewHTTPClient(serviceURL)

	registry.RegisterTool(zettaranc.NewAnalyzeTool(httpClient))
	registry.RegisterTool(zettaranc.NewScreenerTool(httpClient))

	// zettaranc.backtest is deliberately NOT registered. It is a stub whose
	// Execute always returns Success:false, so registering it only offers the
	// model a guaranteed failure. The tool source (zettaranc/backtest.go) stays
	// on disk and re-registers here when real backtesting (逐日重放信号、持仓与
	// 撮合、绩效统计) lands. TestZettarancWhitelistMatchesRegisteredTools fails
	// loudly if the two ever drift apart again — that test is why this comment
	// exists rather than a silent removal.

	return nil
}
