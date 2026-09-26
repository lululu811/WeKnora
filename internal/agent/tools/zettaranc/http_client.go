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
