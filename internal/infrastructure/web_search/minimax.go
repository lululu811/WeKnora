package web_search

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

const (
	defaultMiniMaxSearchURL = "https://api.minimaxi.com/v1/coding_plan/search"
	globalMiniMaxSearchURL  = "https://api.minimax.io/v1/coding_plan/search"
	defaultMiniMaxTimeout   = 30 * time.Second
	defaultMiniMaxResults   = 10
	maxMiniMaxResults       = 10
	maxMiniMaxResponseBytes = 4 << 20
	defaultMiniMaxRegion    = "cn"
)

var validMiniMaxRegions = map[string]struct{}{
	"cn":     {},
	"global": {},
}

// MiniMaxProvider implements web search using the MiniMax Web Search API.
type MiniMaxProvider struct {
	client  *http.Client
	baseURL string
	apiKey  string
	region  string
}

// NewMiniMaxProvider creates a new MiniMax web search provider instance.
func NewMiniMaxProvider(params types.WebSearchProviderParameters) (interfaces.WebSearchProvider, error) {
	if err := ValidateMiniMaxParameters(params); err != nil {
		return nil, err
	}
	client, err := NewSearchHTTPClient(defaultMiniMaxTimeout, params.ProxyURL)
	if err != nil {
		return nil, err
	}
	region := miniMaxRegion(params.ExtraConfig)
	baseURL := defaultMiniMaxSearchURL
	if region == "global" {
		baseURL = globalMiniMaxSearchURL
	}
	return &MiniMaxProvider{
		client:  client,
		baseURL: baseURL,
		apiKey:  strings.TrimSpace(params.APIKey),
		region:  region,
	}, nil
}

// ValidateMiniMaxParameters validates configuration parameters for MiniMax provider.
func ValidateMiniMaxParameters(params types.WebSearchProviderParameters) error {
	if strings.TrimSpace(params.APIKey) == "" {
		return fmt.Errorf("API key is required for MiniMax provider")
	}
	region := miniMaxRegion(params.ExtraConfig)
	if _, ok := validMiniMaxRegions[region]; !ok {
		return fmt.Errorf("invalid MiniMax region: %s", region)
	}
	return nil
}

func miniMaxRegion(extraConfig map[string]string) string {
	if region := strings.TrimSpace(extraConfig["region"]); region != "" {
		return strings.ToLower(region)
	}
	return defaultMiniMaxRegion
}

// Name returns the provider name.
func (p *MiniMaxProvider) Name() string { return "minimax" }

// Search executes a web search query against MiniMax API.
func (p *MiniMaxProvider) Search(ctx context.Context, query string, maxResults int, includeDate bool) ([]*types.WebSearchResult, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, fmt.Errorf("query is empty")
	}
	if maxResults <= 0 {
		maxResults = defaultMiniMaxResults
	}
	if maxResults > maxMiniMaxResults {
		maxResults = maxMiniMaxResults
	}

	body, err := json.Marshal(miniMaxSearchRequest{
		Query: query,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to marshal MiniMax request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create MiniMax request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+p.apiKey)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")

	logger.Infof(ctx, "[WebSearch][MiniMax] query=%q maxResults=%d region=%s", query, maxResults, p.region)
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute MiniMax request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := readMiniMaxResponseBody(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, miniMaxHTTPError(resp.StatusCode, respBody)
	}

	var response miniMaxSearchResponse
	if err := json.Unmarshal(respBody, &response); err != nil {
		return nil, fmt.Errorf("failed to parse MiniMax response: %w", err)
	}

	if response.BaseResp != nil && response.BaseResp.StatusCode != 0 {
		return nil, fmt.Errorf("minimax API error: %s (status_code %d)", response.BaseResp.StatusMsg, response.BaseResp.StatusCode)
	}
	if response.Error != nil && response.Error.Message != "" {
		return nil, fmt.Errorf("minimax API error: %s", response.Error.Message)
	}

	results := make([]*types.WebSearchResult, 0, len(response.Organic))
	for _, item := range response.Organic {
		title := strings.TrimSpace(item.Title)
		link := strings.TrimSpace(item.Link)
		if title == "" && link == "" {
			continue
		}
		result := &types.WebSearchResult{
			Title:   title,
			URL:     link,
			Snippet: strings.TrimSpace(item.Snippet),
			Source:  "minimax",
			Age:     strings.TrimSpace(item.Date),
		}
		if includeDate && item.Date != "" {
			if parsed, ok := parseMiniMaxDate(item.Date); ok {
				result.PublishedAt = &parsed
			}
		}
		results = append(results, result)
		if len(results) >= maxResults {
			break
		}
	}

	logger.Infof(ctx, "[WebSearch][MiniMax] returned %d results", len(results))
	return results, nil
}

func readMiniMaxResponseBody(reader io.Reader) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(reader, maxMiniMaxResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("failed to read MiniMax response: %w", err)
	}
	if len(body) > maxMiniMaxResponseBytes {
		return nil, fmt.Errorf("MiniMax response exceeds %d bytes", maxMiniMaxResponseBytes)
	}
	return body, nil
}

func miniMaxHTTPError(statusCode int, body []byte) error {
	var apiError struct {
		BaseResp *struct {
			StatusCode int    `json:"status_code"`
			StatusMsg  string `json:"status_msg"`
		} `json:"base_resp"`
		Error *struct {
			Message string `json:"message"`
		} `json:"error"`
		Message string `json:"message"`
	}
	if json.Unmarshal(body, &apiError) == nil {
		if apiError.BaseResp != nil && apiError.BaseResp.StatusMsg != "" {
			return fmt.Errorf("MiniMax API returned status %d: %s (status_code %d)", statusCode, apiError.BaseResp.StatusMsg, apiError.BaseResp.StatusCode)
		}
		if apiError.Error != nil && apiError.Error.Message != "" {
			return fmt.Errorf("MiniMax API returned status %d: %s", statusCode, apiError.Error.Message)
		}
		if apiError.Message != "" {
			return fmt.Errorf("MiniMax API returned status %d: %s", statusCode, apiError.Message)
		}
	}
	detail := strings.TrimSpace(string(body))
	if len(detail) > 4096 {
		detail = detail[:4096]
	}
	if detail == "" {
		return fmt.Errorf("MiniMax API returned status %d", statusCode)
	}
	return fmt.Errorf("MiniMax API returned status %d: %s", statusCode, detail)
}

func parseMiniMaxDate(value string) (time.Time, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return time.Time{}, false
	}
	layouts := []string{
		"2006/01/02 15:04:05",
		"2006/01/02",
		"2006-01-02 15:04:05",
		"2006-01-02",
		time.RFC3339Nano,
		time.RFC3339,
	}
	for _, layout := range layouts {
		if parsed, err := time.Parse(layout, value); err == nil {
			return parsed, true
		}
		// If string does not have timezone offset, also try parsing in Local timezone
		if parsed, err := time.ParseInLocation(layout, value, time.Local); err == nil {
			return parsed, true
		}
	}
	return time.Time{}, false
}

type miniMaxSearchRequest struct {
	Query string `json:"q"`
}

type miniMaxSearchResponse struct {
	Organic  []miniMaxOrganicItem `json:"organic"`
	BaseResp *miniMaxBaseResp     `json:"base_resp,omitempty"`
	Error    *miniMaxError        `json:"error,omitempty"`
}

type miniMaxOrganicItem struct {
	Title   string `json:"title"`
	Link    string `json:"link"`
	Snippet string `json:"snippet"`
	Date    string `json:"date"`
}

type miniMaxBaseResp struct {
	StatusCode int    `json:"status_code"`
	StatusMsg  string `json:"status_msg"`
}

type miniMaxError struct {
	Message string `json:"message"`
	Type    string `json:"type"`
	Code    any    `json:"code"`
}
