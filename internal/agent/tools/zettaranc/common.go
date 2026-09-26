// Package zettaranc provides tools for the Zettaranc (Z哥) trading system.
//
// These tools wrap the Python CLI from zettaranc-skill, providing:
// - Strategy detection (30+ patterns)
// - Backtesting
// - Stock screening
// - Portfolio diagnosis
//
// Design notes:
//   - All tools call the Python CLI via subprocess
//   - Input is passed as JSON via --input flag
//   - Output is parsed from JSON stdout
//   - Errors are surfaced with actionable messages
package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// Config holds the configuration for zettaranc tools.
type Config struct {
	// PythonPath is the path to the Python interpreter.
	PythonPath string

	// CLIDir is the directory containing the zettaranc CLI.
	CLIDir string

	// DataMode is the data mode: "jnb" (real data) or "websearch" (no data).
	DataMode string

	// Timeout is the maximum execution time for CLI commands.
	Timeout time.Duration
}

// DefaultConfig returns the default configuration.
func DefaultConfig() *Config {
	// Use environment variables for paths, with defaults for local development
	pythonPath := os.Getenv("ZETTARANC_PYTHON_PATH")
	if pythonPath == "" {
		pythonPath = "/Users/chenlei/007_DB/Financial-API/.venv/bin/python3"
	}

	cliDir := os.Getenv("ZETTARANC_CLI_DIR")
	if cliDir == "" {
		cliDir = "/Users/chenlei/005_skill/skills/zettaranc-skill"
	}

	dataMode := os.Getenv("ZETTARANC_DATA_MODE")
	if dataMode == "" {
		dataMode = "jnb"
	}

	return &Config{
		PythonPath: pythonPath,
		CLIDir:     cliDir,
		DataMode:   dataMode,
		Timeout:    30 * time.Second,
	}
}

// CLIClient wraps calls to the zettaranc Python CLI.
type CLIClient struct {
	config *Config
}

// NewCLIClient creates a new CLI client.
func NewCLIClient(config *Config) *CLIClient {
	if config == nil {
		config = DefaultConfig()
	}
	return &CLIClient{config: config}
}

// CLIResponse represents the response from the Python CLI.
type CLIResponse struct {
	Success bool        `json:"success"`
	Data    interface{} `json:"data"`
	Error   string      `json:"error,omitempty"`
	Meta    struct {
		DurationMs int    `json:"duration_ms"`
		DataDate   string `json:"data_date,omitempty"`
	} `json:"meta,omitempty"`
}

// Execute runs a CLI command and returns the parsed response.
func (c *CLIClient) Execute(ctx context.Context, command string, input interface{}) (*CLIResponse, error) {
	// Marshal input to JSON
	inputJSON, err := json.Marshal(input)
	if err != nil {
		return nil, fmt.Errorf("输入序列化失败：%v", err)
	}

	// Build command
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, c.config.PythonPath, "-m", "modules.cli",
		command,
		"--input", string(inputJSON),
		"--json")
	cmd.Dir = c.config.CLIDir

	// Set environment variables
	cmd.Env = append(cmd.Env, fmt.Sprintf("DATA_MODE=%s", c.config.DataMode))

	// Execute
	output, err := cmd.Output()
	if err != nil {
		// Check if it's a timeout
		if ctx.Err() == context.DeadlineExceeded {
			return nil, fmt.Errorf("CLI 执行超时（%v）。请尝试减少查询天数或简化查询条件", c.config.Timeout)
		}

		// Check for stderr
		if exitErr, ok := err.(*exec.ExitError); ok {
			stderr := string(exitErr.Stderr)
			return nil, fmt.Errorf("CLI 执行失败：%v。错误详情：%s", err, stderr)
		}

		return nil, fmt.Errorf("CLI 执行失败：%v", err)
	}

	// Parse response
	var resp CLIResponse
	if err := json.Unmarshal(output, &resp); err != nil {
		return nil, fmt.Errorf("结果解析失败：%v。原始输出：%s", err, string(output))
	}

	if !resp.Success {
		return nil, fmt.Errorf("CLI 返回错误：%s", resp.Error)
	}

	return &resp, nil
}

// GetConfig returns the hithink finance config (for shared DB pool).
func GetConfig() *hithink_finance.Config {
	return hithink_finance.DefaultConfig()
}
