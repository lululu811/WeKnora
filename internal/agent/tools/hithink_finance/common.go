// Package hithink_finance provides HTTP client for Python service.
package hithink_finance

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Config holds the configuration for hithink finance tools.
type Config struct {
	ServiceURL string
	Timeout    time.Duration
}

// DefaultConfig returns the default configuration.
func DefaultConfig() *Config {
	serviceURL := os.Getenv("PYTHON_SERVICE_URL")
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}

	return &Config{
		ServiceURL: serviceURL,
		Timeout:    10 * time.Second,
	}
}

// QueryRequest represents a DuckDB query request.
type QueryRequest struct {
	DB    string `json:"db"`
	SQL   string `json:"sql"`
	Limit int    `json:"limit"`
}

// QueryResponse represents a DuckDB query response.
type QueryResponse struct {
	Success bool                     `json:"success"`
	DB      string                   `json:"db"`
	Count   int                      `json:"count"`
	Data    []map[string]interface{} `json:"data"`
}

// QueryDuckDB executes a SQL query on a DuckDB database via Python service.
func QueryDuckDB(ctx context.Context, config *Config, dbName, query string) ([]map[string]interface{}, error) {
	if config == nil {
		config = DefaultConfig()
	}

	// Add LIMIT if not present
	if !strings.Contains(strings.ToUpper(query), "LIMIT") {
		query = fmt.Sprintf("%s LIMIT 1000", strings.TrimRight(query, "; \n"))
	}

	request := QueryRequest{
		DB:    dbName,
		SQL:   query,
		Limit: 1000,
	}

	reqBody, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, config.Timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, "POST", config.ServiceURL+"/query", bytes.NewBuffer(reqBody))
	if err != nil {
		return nil, fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("查询失败（Python 服务不可用）：%v。请检查：1) python-service 容器是否运行；2) 网络连接是否正常", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败：%v", err)
	}

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("查询失败（HTTP %d）：%s", resp.StatusCode, string(body))
	}

	var result QueryResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("结果解析失败：%v", err)
	}

	if !result.Success {
		return nil, fmt.Errorf("查询失败")
	}

	return result.Data, nil
}

// CheckSyncWindow returns an error if the current time is within a sync window.
func CheckSyncWindow() error {
	now := time.Now()
	currentMinutes := now.Hour()*60 + now.Minute()

	syncWindows := []struct {
		start, end int
		label      string
	}{
		{17*60 + 25, 17*60 + 35, "日终同步"},
		{2*60 + 55, 3*60 + 5, "夜间同步"},
	}

	for _, w := range syncWindows {
		if currentMinutes >= w.start && currentMinutes <= w.end {
			return fmt.Errorf("数据同步中（%s，每日 17:30 和 03:00），请稍后重试。建议等待 5-10 分钟后重试", w.label)
		}
	}
	return nil
}

// DBNames returns the list of available database names.
func DBNames() []string {
	return []string{
		"market", "financials", "fund", "special",
		"futures", "index", "indicators",
	}
}

// EnsureLimit adds a LIMIT clause to the SQL query if not present.
func EnsureLimit(sqlStr string, defaultLimit int) string {
	upper := strings.ToUpper(sqlStr)
	if strings.Contains(upper, "LIMIT") {
		return sqlStr
	}
	return fmt.Sprintf("%s LIMIT %d", strings.TrimRight(sqlStr, "; \n"), defaultLimit)
}

// ValidateReadOnlySQL checks that the SQL query is read-only.
func ValidateReadOnlySQL(sqlStr string) error {
	upper := strings.ToUpper(strings.TrimSpace(sqlStr))

	if !strings.HasPrefix(upper, "SELECT") {
		return fmt.Errorf("安全限制：只允许 SELECT 查询")
	}

	dangerous := []string{
		"INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
		"COPY", "ATTACH", "DETACH", "CALL", "EXECUTE",
	}
	for _, keyword := range dangerous {
		if strings.Contains(upper, keyword) {
			return fmt.Errorf("安全限制：禁止 %s 操作", keyword)
		}
	}

	return nil
}

// DefaultLimit is the default LIMIT for SQL queries.
const DefaultLimit = 1000
