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
	"regexp"
	"strconv"
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
//
// Params carries the bind values for the `?` placeholders in SQL, in order.
// Anything derived from user input (thscode above all) must go here rather
// than be formatted into the SQL string.
type QueryRequest struct {
	DB     string        `json:"db"`
	SQL    string        `json:"sql"`
	Limit  int           `json:"limit"`
	Params []interface{} `json:"params,omitempty"`
}

// QueryResponse represents a DuckDB query response.
type QueryResponse struct {
	Success bool                     `json:"success"`
	DB      string                   `json:"db"`
	Count   int                      `json:"count"`
	Data    []map[string]interface{} `json:"data"`
	Error   string                   `json:"error,omitempty"`
}

// QueryDuckDB executes a SQL query on a DuckDB database via Python service.
// The query must not embed user input; use QueryDuckDBParams for that.
func QueryDuckDB(ctx context.Context, config *Config, dbName, query string) ([]map[string]interface{}, error) {
	return QueryDuckDBParams(ctx, config, dbName, query)
}

// QueryDuckDBParams executes a parameterised SQL query.
//
// The LIMIT handling here mirrors what python-service does: the service
// wraps every statement in an outer LIMIT, so a "LIMIT" substring check on
// the caller's SQL (the old `strings.Contains(..., "LIMIT")`) was both
// redundant and bypassable via a SQL comment.
func QueryDuckDBParams(ctx context.Context, config *Config, dbName, query string, params ...interface{}) ([]map[string]interface{}, error) {
	if config == nil {
		config = DefaultConfig()
	}

	// No LIMIT is appended here: python-service wraps every statement in an
	// outer `SELECT * FROM (...) LIMIT n`, so appending one client-side was
	// redundant — and the old `strings.Contains(..., "LIMIT")` guard that
	// decided whether to append was defeated by a `-- limit` comment.
	request := QueryRequest{
		DB:     dbName,
		SQL:    query,
		Limit:  1000,
		Params: params,
	}
	reqBody, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, config.Timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, config.ServiceURL+"/query/", bytes.NewBuffer(reqBody))
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
		if result.Error != "" {
			return nil, fmt.Errorf("查询失败：%s", result.Error)
		}
		return nil, fmt.Errorf("查询失败")
	}

	injectProvenance(result.Data, dbName, query)
	return result.Data, nil
}

// provenanceRe 抽出 SQL 里 FROM / JOIN 后面的对象名。
// 与 schema_contract_test.go 里用的是同一套正则：那里的作用是抓表名漂移，
// 这里的作用是把"这些数字从哪张表来"如实附在结果上。
var provenanceRe = regexp.MustCompile(`(?i)\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)`)

// SQL 关键字不是表名，出现在 FROM/JOIN 后面的这些要排除。
var sqlKeywords = map[string]bool{
	"SELECT": true, "WHERE": true, "ON": true, "AND": true, "OR": true,
	"ORDER": true, "GROUP": true, "HAVING": true, "LIMIT": true,
	"UNION": true, "JOIN": true, "INNER": true, "LEFT": true, "RIGHT": true,
	"FULL": true, "CROSS": true, "LATERAL": true, "VALUES": true, "WITH": true,
}

// injectProvenance 给每行结果附上溯源信息。
//
// 为什么在这里做：19 个工具各自在 Execute 里拼结果，没有一处会交代"这些数字
// 从哪张表来"。逐个改既漏得掉也改不动，而在唯一的出口做一次，全部工具自动覆盖。
//
// 附加在行内（_source）而不是行外：ToolResult.Output 是这些行的 JSON 序列化，
// 行内字段会跟着一起进 LLM 的上下文，模型因而能在回答里引用出处；
// 挂在行外的话模型看不到，"有溯源"就等于没有。
func injectProvenance(rows []map[string]interface{}, dbName, sql string) {
	if len(rows) == 0 {
		return
	}
	tables := ExtractTables(sql)
	for _, row := range rows {
		row["_source"] = map[string]interface{}{
			"db":     dbName,
			"tables": tables,
		}
	}
}

// ExtractTables 返回 SQL 实际读到的表名（去重、保序）。
// 只做字面量识别：CTE 别名、子查询别名会原样返回，这是已知的保守行为 ——
// 多报一个别名好过漏掉一张真表，因为漏报会让"有出处"变成假出处。
func ExtractTables(sql string) []string {
	seen := make(map[string]bool)
	out := make([]string, 0, 3)
	for _, m := range provenanceRe.FindAllStringSubmatch(sql, -1) {
		name := m[1]
		if sqlKeywords[strings.ToUpper(name)] || seen[name] {
			continue
		}
		seen[name] = true
		out = append(out, name)
	}
	return out
}

// syncWindowEnv 覆盖同步窗口，格式 `HH:MM-HH:MM[,HH:MM-HH:MM...]`。
// 未设置时用 defaultSyncWindows；设为**空字符串**表示不做窗口拦截。
const syncWindowEnv = "HITHINK_SYNC_WINDOWS"

// defaultSyncWindows 是每日 ETL 重算的两个窗口。这段时间同步进程持有 DuckDB 写锁，
// 窗口内放行查询会撞 "database is locked"，因此直接拒绝。
const defaultSyncWindows = "17:25-17:35,02:55-03:05"

type syncWindow struct {
	start, end int // 当日分钟数
}

// CheckSyncWindow returns an error if the current time is within a sync window.
//
// 窗口来自 HITHINK_SYNC_WINDOWS：使用者的 ETL 时刻表与本仓库默认值不一定相同，
// 所以不把时刻表烧进代码。
func CheckSyncWindow() error {
	spec, ok := os.LookupEnv(syncWindowEnv)
	if !ok {
		spec = defaultSyncWindows
	}
	return checkSyncWindowAt(time.Now(), spec)
}

// checkSyncWindowAt 是 CheckSyncWindow 的纯函数内核，便于测试固定时刻。
func checkSyncWindowAt(now time.Time, spec string) error {
	current := now.Hour()*60 + now.Minute()
	for _, w := range parseSyncWindows(spec) {
		inWindow := current >= w.start && current <= w.end
		if w.start > w.end { // 跨零点窗口，例如 23:50-00:10
			inWindow = current >= w.start || current <= w.end
		}
		if inWindow {
			return fmt.Errorf("数据同步中（%s-%s），请稍后重试。建议等待 5-10 分钟后重试",
				formatClock(w.start), formatClock(w.end))
		}
	}
	return nil
}

// parseSyncWindows 解析 `HH:MM-HH:MM` 列表。
//
// 无法识别的片段被**跳过**而不是报错：这个函数每次工具调用都会执行，配置写错
// 不该让整族工具不可用，退化成「少拦截一个窗口」即可。
func parseSyncWindows(spec string) []syncWindow {
	var out []syncWindow
	for _, part := range strings.Split(spec, ",") {
		startStr, endStr, found := strings.Cut(strings.TrimSpace(part), "-")
		if !found {
			continue
		}
		start, okStart := parseClock(startStr)
		end, okEnd := parseClock(endStr)
		if !okStart || !okEnd {
			continue
		}
		out = append(out, syncWindow{start: start, end: end})
	}
	return out
}

// parseClock 把 `HH:MM` 解析为当日分钟数。
func parseClock(s string) (int, bool) {
	hh, mm, found := strings.Cut(strings.TrimSpace(s), ":")
	if !found {
		return 0, false
	}
	h, errH := strconv.Atoi(strings.TrimSpace(hh))
	m, errM := strconv.Atoi(strings.TrimSpace(mm))
	if errH != nil || errM != nil || h < 0 || h > 23 || m < 0 || m > 59 {
		return 0, false
	}
	return h*60 + m, true
}

func formatClock(minutes int) string {
	return fmt.Sprintf("%02d:%02d", minutes/60, minutes%60)
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
