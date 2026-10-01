package zettaranc

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// HTTPClient calls the python-service HTTP API for zettaranc analysis.
type HTTPClient struct {
	serviceURL string
	timeout    time.Duration
	httpClient *http.Client
}

// NewHTTPClient creates a new HTTP client for the python-service.
func NewHTTPClient(serviceURL string) *HTTPClient {
	if serviceURL == "" {
		serviceURL = "http://python-service:50052"
	}
	return &HTTPClient{
		serviceURL: serviceURL,
		timeout:    30 * time.Second,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// AnalyzeRequest is the request body for the /zettaranc/analyze endpoint.
type AnalyzeRequest struct {
	Thscode string `json:"thscode"`
	Days    int    `json:"days"`
}

// AnalyzeResponse is the response from the /zettaranc/analyze endpoint.
type AnalyzeResponse struct {
	// The full response body is passed through as a map since the
	// structure is rich and the Go layer does not inspect individual fields.
	Data map[string]interface{} `json:"-"`
}

// FourBricks calls the python-service /zettaranc/four-bricks endpoint.
//
// 新增于 2026-10-01。四块砖此前只在工作台前端算得出，服务端取不到，
// 所以 agent_system_prompt 命令 agent 对"四块砖什么状态"一律回答"算不出来"。
// 端点落地后这条限制可以撤销。
func (c *HTTPClient) FourBricks(ctx context.Context, thscode string, days int) (map[string]interface{}, error) {
	reqBody := AnalyzeRequest{Thscode: thscode, Days: days}
	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.serviceURL+"/zettaranc/four-bricks", bytes.NewBuffer(body))
	if err != nil {
		return nil, fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("四块砖服务不可用：%v。请检查 python-service 是否运行", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败：%v", err)
	}
	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("股票未找到：%s", string(respBody))
	}
	if resp.StatusCode == http.StatusServiceUnavailable {
		return nil, fmt.Errorf("数据不可用：%s", string(respBody))
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("四块砖计算失败（HTTP %d）：%s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("结果解析失败：%v", err)
	}
	return result, nil
}

// Analyze calls the python-service /zettaranc/analyze endpoint.
func (c *HTTPClient) Analyze(ctx context.Context, thscode string, days int) (map[string]interface{}, error) {
	reqBody := AnalyzeRequest{
		Thscode: thscode,
		Days:    days,
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.serviceURL+"/zettaranc/analyze", bytes.NewBuffer(body))
	if err != nil {
		return nil, fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("分析服务不可用：%v。请检查 python-service 是否运行", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败：%v", err)
	}

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("股票未找到：%s", string(respBody))
	}
	if resp.StatusCode == http.StatusServiceUnavailable {
		return nil, fmt.Errorf("数据不可用：%s", string(respBody))
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("分析失败（HTTP %d）：%s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("结果解析失败：%v", err)
	}

	return result, nil
}

// ScreenRequest is the request body for the /zettaranc/screen endpoint.
//
// 形态信号只回答"图形像不像"，不回答"会不会暴雷"。下面的可选筛选构成
// 第二道关：板块限定 + 财务风险代理 + ST 排除。全部可选，不传就是纯形态
// 选股。
//
// 风险维度只覆盖本地库真正算得出的三项（资产负债率/流动比率/应收占比，
// 出处 financials.v_balance_sheet）与 ST 名称匹配（market.v_symbol.name，
// 库里没有 ST 标记列）。商誉、股权质押、大股东减持、审计意见、监管处罚
// 本地无数据源 —— 不提供对应参数，因为给了也只会是假阈值。
type ScreenRequest struct {
	Strategy string `json:"strategy"`
	Limit    int    `json:"limit"`

	Sector string `json:"sector,omitempty"`

	MaxDebtRatio       *float64 `json:"max_debt_ratio,omitempty"`
	MinCurrentRatio    *float64 `json:"min_current_ratio,omitempty"`
	MaxReceivableRatio *float64 `json:"max_receivable_ratio,omitempty"`
	RequireProfit      bool     `json:"require_profit,omitempty"`
	ExcludeST          bool     `json:"exclude_st,omitempty"`
}

// ScreenOption 可选筛选条件。用选项而不是加一堆位置参数：
// 现有调用方（backtest 曾调用过、测试）不必全部改签名。
type ScreenOption func(*ScreenRequest)

// WithSector 把候选集限定在某个板块内（支持片段匹配，如 "半导体"）。
// 服务端按板块名反查成分股，不展开全量成员关系（后者 122,368 行，
// 超过单查询 100k 上限）。
func WithSector(name string) ScreenOption {
	return func(r *ScreenRequest) { r.Sector = name }
}

// WithMaxDebtRatio 设置资产负债率上限。本地分布 p50=0.40 p90=0.71。
func WithMaxDebtRatio(v float64) ScreenOption {
	return func(r *ScreenRequest) { r.MaxDebtRatio = &v }
}

// WithMinCurrentRatio 设置流动比率下限。本地分布 p50=1.50 p10=0.60。
func WithMinCurrentRatio(v float64) ScreenOption {
	return func(r *ScreenRequest) { r.MinCurrentRatio = &v }
}

// WithMaxReceivableRatio 设置应收账款占总资产上限。
func WithMaxReceivableRatio(v float64) ScreenOption {
	return func(r *ScreenRequest) { r.MaxReceivableRatio = &v }
}

// WithRequireProfit 要求最新期归母净利润为正。
func WithRequireProfit() ScreenOption {
	return func(r *ScreenRequest) { r.RequireProfit = true }
}

// WithExcludeST 排除 ST/*ST。服务端按 v_symbol.name 匹配 —— 库里没有
// ST 标记列，这是唯一取法。
func WithExcludeST() ScreenOption {
	return func(r *ScreenRequest) { r.ExcludeST = true }
}

// Screen calls the python-service /zettaranc/screen endpoint.
func (c *HTTPClient) Screen(ctx context.Context, strategy string, limit int, opts ...ScreenOption) (map[string]interface{}, error) {
	reqBody := ScreenRequest{
		Strategy: strategy,
		Limit:    limit,
	}
	for _, opt := range opts {
		if opt != nil {
			opt(&reqBody)
		}
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("请求序列化失败：%v", err)
	}

	ctx, cancel := context.WithTimeout(ctx, c.timeout*4) // 选股耗时较长
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.serviceURL+"/zettaranc/screen", bytes.NewBuffer(body))
	if err != nil {
		return nil, fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("选股服务不可用：%v。请检查 python-service 是否运行", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败：%v", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("选股失败（HTTP %d）：%s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("结果解析失败：%v", err)
	}

	return result, nil
}
