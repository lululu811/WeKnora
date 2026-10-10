// Package industry fetches batch industry classifications from python-service.
//
// The grading job uses this to group symbols by industry before calling the
// LLM, reducing call count from ~500 (chunked by 20) to ~300 (one per industry).
package industry

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

// DefaultBaseURL matches the docker-compose service name.
const DefaultBaseURL = "http://python-service:50052"

// MaxSymbolsPerRequest mirrors the python endpoint's max_items.
const MaxSymbolsPerRequest = 500

const defaultTimeout = 15 * time.Second

// Industry is one symbol's classification.
type Industry struct {
	Level1 string `json:"level1"`
	Level2 string `json:"level2"`
}

// Fetcher retrieves industry classifications for a batch of symbols.
type Fetcher interface {
	Fetch(ctx context.Context, thscodes []string) (map[string]Industry, error)
}

// Client is a stateless HTTP client for POST /watchlist/industry-map.
type Client struct {
	baseURL string
	http    *http.Client
}

// NewClient builds a client from PYTHON_SERVICE_URL (empty → DefaultBaseURL).
func NewClient() *Client {
	base := strings.TrimSpace(os.Getenv("PYTHON_SERVICE_URL"))
	if base == "" {
		base = DefaultBaseURL
	}
	return NewClientWithBase(base, defaultTimeout)
}

// NewClientWithBase is the test seam.
func NewClientWithBase(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: timeout},
	}
}

type industryRequest struct {
	Thscodes []string `json:"thscodes"`
}

type industryResponse struct {
	Code int                 `json:"code"`
	Data map[string]Industry `json:"data"`
}

// Fetch retrieves industry classifications for the given symbols, chunking to
// the service's per-request cap.
func (c *Client) Fetch(ctx context.Context, thscodes []string) (map[string]Industry, error) {
	unique := dedupe(thscodes)
	out := make(map[string]Industry, len(unique))
	for start := 0; start < len(unique); start += MaxSymbolsPerRequest {
		end := start + MaxSymbolsPerRequest
		if end > len(unique) {
			end = len(unique)
		}
		chunk, err := c.fetchChunk(ctx, unique[start:end])
		if err != nil {
			return nil, err
		}
		for code, ind := range chunk {
			out[code] = ind
		}
	}
	return out, nil
}

func (c *Client) fetchChunk(ctx context.Context, thscodes []string) (map[string]Industry, error) {
	payload, err := json.Marshal(industryRequest{Thscodes: thscodes})
	if err != nil {
		return nil, fmt.Errorf("industry: marshal request: %w", err)
	}
	endpoint := c.baseURL + "/watchlist/industry-map"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("industry: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("industry: request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("industry: read response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("industry: HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var parsed industryResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, fmt.Errorf("industry: decode response: %w", err)
	}
	if parsed.Code != 0 {
		return nil, fmt.Errorf("industry: service returned code %d", parsed.Code)
	}
	return parsed.Data, nil
}

func dedupe(s []string) []string {
	seen := make(map[string]bool, len(s))
	out := make([]string, 0, len(s))
	for _, v := range s {
		v = strings.TrimSpace(v)
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		out = append(out, v)
	}
	return out
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
