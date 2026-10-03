// Package halo provides tools for the HALO annual-report fact pipeline.
//
// 数据链路是单向的：巨潮年报 PDF → 抽取 → 本地事实库 → 评分内核读表。
// 本包只做 HTTP 薄封装，真正的抓取/解析/对账都在 python-service
// （python-service/halo/）里，Go 侧不碰业务逻辑。
package halo

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

// HTTPClient 调用 python-service 的 /halo/* 端点。
type HTTPClient struct {
	serviceURL string
	apiKey     string
	httpClient *http.Client
	// gate 串行化 /halo/sync。见 Sync 里的说明。
	gate *syncGate
}

// NewHTTPClient 创建客户端。serviceURL 为空时读 PYTHON_SERVICE_URL。
//
// apiKey 读 WEKNORA_PY_SERVICE_API_KEY，与 python-service 的 require_api_key
// 同一个变量。/halo/sync 会向巨潮发起请求并落盘，是有外部副作用的端点，
// 不该像 zettaranc 那几个一样裸露着（那些端点只读本地数据，暴露面不同）。
// 该变量未设置时 python-service 侧不强制鉴权，这里也就不带头，两边行为一致。
func NewHTTPClient(serviceURL string) *HTTPClient {
	if serviceURL == "" {
		serviceURL = os.Getenv("PYTHON_SERVICE_URL")
	}
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}
	return &HTTPClient{
		serviceURL: serviceURL,
		apiKey:     os.Getenv("WEKNORA_PY_SERVICE_API_KEY"),
		httpClient: &http.Client{},
		gate:       newSyncGate(maxConcurrentSync),
	}
}

// SyncRequest 是 /halo/sync 的请求体。
//
// Force 存在的理由是缓存键是 (thscode, report_type, year)：年报一年只变一次，
// 正常情况下重复调用应直接命中缓存。但抽取逻辑修好之后需要能强制重跑，
// 否则修好的代码要等到下一年才会生效。
type SyncRequest struct {
	Thscode    string `json:"thscode"`
	ReportType string `json:"report_type"`
	Force      bool   `json:"force"`
}

// QueryRequest 是 /halo/query 的请求体。
type QueryRequest struct {
	Thscode      string   `json:"thscode"`
	Period       string   `json:"period,omitempty"`
	ReportType   string   `json:"report_type,omitempty"`
	Fields       []string `json:"fields,omitempty"`
	Scope        string   `json:"scope,omitempty"`
	OnlyVerified *bool    `json:"only_verified,omitempty"`
}

// ScoreRequest 是 /halo/score 的请求体。
//
// IncludeExternal 与 IncludeAnnouncements 都默认关闭，与端点侧一致：打开会分别
// 去打东财的易封子域（push2his）和巨潮（限速），是**按需行为**而不是默认行为。
// 归档时尤其不要默认打开 external —— 归档一次报告不该把 IP 风险搭进去；公告则
// 由归档路径显式打开（报告里需要它，且只多一次请求）。
type ScoreRequest struct {
	Thscode              string `json:"thscode"`
	Period               string `json:"period,omitempty"`
	ReportType           string `json:"report_type,omitempty"`
	Scope                string `json:"scope,omitempty"`
	IncludeExt           bool   `json:"include_external"`
	IncludeAnnouncements bool   `json:"include_announcements"`
}

// post 发一个 JSON 请求并解出响应体。
//
// timeout 必须按端点给足：sync 要下载年报 PDF（单份 1–10 MB）再逐页解析，
// 实测茅台 143 页年报的文本提取就要几十秒。用默认的 30s 会在最正常的工作
// 上超时，然后调用方看到的是一个毫无信息量的 "context deadline exceeded"。
func (c *HTTPClient) post(ctx context.Context, path string, body any, timeout time.Duration, out *map[string]any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.serviceURL+path, bytes.NewBuffer(raw))
	if err != nil {
		return fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("HALO 服务不可用：%v。请检查 python-service 是否运行", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("读取响应失败：%v", err)
	}

	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusNotFound:
		return fmt.Errorf("未找到对应披露文件：%s", string(respBody))
	case http.StatusUnprocessableEntity:
		return fmt.Errorf("参数错误：%s", string(respBody))
	case http.StatusServiceUnavailable:
		return fmt.Errorf("数据源不可用：%s", string(respBody))
	default:
		return fmt.Errorf("HALO 请求失败（HTTP %d）：%s", resp.StatusCode, string(respBody))
	}

	if err := json.Unmarshal(respBody, out); err != nil {
		return fmt.Errorf("结果解析失败：%v", err)
	}
	return nil
}

// syncTimeout 覆盖「检索巨潮 → 下载 PDF → 逐页解析 → 抽取 → 对账 → 落表」
// 整条链路。10 分钟是给最坏情况留的余量：单份年报 143 页实测几十秒，
// 加上巨潮限速（默认 500ms 间隔）与偶发重试，分钟级是常态。
const syncTimeout = 10 * time.Minute

// queryTimeout 只读 SQLite，秒级足够。
const queryTimeout = 30 * time.Second

// scoreTimeout 覆盖 analyze()：本地读表与评分是秒级，但 IncludeExternal
// 会去打东财三个子域（本模块串行 + 最小间隔），实测几十秒。给 2 分钟。
const scoreTimeout = 2 * time.Minute

// Sync 触发一只股票的年报抽取与落表。
func (c *HTTPClient) Sync(ctx context.Context, thscode, reportType string, force bool) (map[string]any, error) {
	// 并发保护放在这里而不是 handler 里：/halo/sync 的成本在**巨潮侧**，而它现在
	// 有两条调用方 —— agent 的 halo.filing.sync 工具与工作台的 /halo/sync HTTP
	// 端点。只在 handler 加闸门的话，agent 与用户同时点同一只票仍会并发抓两次
	// PDF，两边都白等 1–3 分钟，还会多消耗一次巨潮的限流额度。
	release, err := c.gate.acquire(ctx, thscode)
	if err != nil {
		return nil, err
	}
	defer release()

	var out map[string]any
	err = c.post(ctx, "/halo/sync", SyncRequest{
		Thscode:    thscode,
		ReportType: reportType,
		Force:      force,
	}, syncTimeout, &out)
	return out, err
}

// Query 读取已落库的事实。
func (c *HTTPClient) Query(ctx context.Context, req QueryRequest) (map[string]any, error) {
	var out map[string]any
	err := c.post(ctx, "/halo/query", req, queryTimeout, &out)
	return out, err
}

// Score 取评分结果。返回体含预渲染的 markdown 骨架（见 analyze.render_markdown），
// 归档就是把它写进知识库，所以调用方不必自己拼报告。
func (c *HTTPClient) Score(ctx context.Context, req ScoreRequest) (map[string]any, error) {
	var out map[string]any
	err := c.post(ctx, "/halo/score", req, scoreTimeout, &out)
	return out, err
}
