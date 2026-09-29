// Package zettaranc provides tools for the Zettaranc (Z哥) trading system.
//
// All tools (analyze, screener, backtest) call the python-service HTTP API,
// defined in http_client.go. The legacy zettaranc-skill Python CLI dependency
// has been removed.
package zettaranc

import (
	"os"
)

// Config holds legacy configuration. The python-service URL and HTTP timeout
// are now configured in HTTPClient. This struct is kept for backward
// compatibility with callers that still pass a Config into RegisterZettarancTools.
type Config struct {
	// PythonPath is retained for backward compatibility but no longer used.
	PythonPath string

	// CLIDir is retained for backward compatibility but no longer used.
	CLIDir string

	// DataMode is retained for backward compatibility but no longer used.
	DataMode string

	// Timeout is retained for backward compatibility but no longer used.
	Timeout interface{}
}

// DefaultConfig returns a default configuration. Most fields are placeholders
// since all tools now go through python-service.
func DefaultConfig() *Config {
	return &Config{
		PythonPath: "",
		CLIDir:     "",
		DataMode:   "jnb",
		Timeout:    nil,
	}
}

// GetServiceURL returns the python-service URL from environment variables.
// Kept as a helper for external code that still imports this package.
func GetServiceURL() string {
	url := os.Getenv("PYTHON_SERVICE_URL")
	if url == "" {
		url = "http://python-service:50052"
	}
	return url
}
