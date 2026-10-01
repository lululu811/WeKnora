// Package quoteclient fetches batch quote snapshots from python-service.
//
// It is the read side of the condition notifier: the daily job asks it for the
// readings, hands them to the pure evaluator, and never talks to python-service
// itself. Following the established hithink_finance client pattern, the service
// URL comes from PYTHON_SERVICE_URL with the same container-internal default.
package quoteclient

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/watchcond"
)

// DefaultBaseURL matches the docker-compose service name, same as the
// hithink_finance client's fallback.
const DefaultBaseURL = "http://python-service:50052"

// MaxSymbolsPerRequest mirrors python-service's MAX_QUOTE_SYMBOLS. A larger
// batch is rejected by the service (422), so the client chunks below it rather
// than discovering the limit at runtime.
const MaxSymbolsPerRequest = 200

// defaultTimeout is generous for a local DuckDB read of up to 200 symbols, but
// bounded so a hung python-service cannot stall the 08:30 run forever.
const defaultTimeout = 30 * time.Second

// Client is a stateless HTTP client for GET /api/quotes.
type Client struct {
	baseURL string
	http    *http.Client
}

// NewClient builds a client from PYTHON_SERVICE_URL (empty → DefaultBaseURL).
// Like halo.NewHTTPClient and hithink_finance.DefaultConfig, the env read is
// explicit: viper.AutomaticEnv has no WEKNORA_ prefix binding for it.
func NewClient() *Client {
	base := strings.TrimSpace(os.Getenv("PYTHON_SERVICE_URL"))
	if base == "" {
		base = DefaultBaseURL
	}
	return NewClientWithBase(base, defaultTimeout)
}

// NewClientWithBase is the test seam: an explicit base URL and timeout.
func NewClientWithBase(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: timeout},
	}
}

// quotesResponse is the subset of python-service's /api/quotes envelope this
// client reads. `missing` and `invalid` are deliberately not surfaced as an
// error: a symbol with no local history is a legitimate reading of "no data",
// and the evaluator already treats an absent symbol as undecidable. Folding it
// into an error here would make one newly listed symbol abort the whole run.
type quotesResponse struct {
	Code int                        `json:"code"`
	Data map[string]json.RawMessage `json:"data"`
}

type quoteEntry struct {
	THSCode     string   `json:"thscode"`
	Name        string   `json:"name"`
	Date        string   `json:"date"`
	Close       *float64 `json:"close"`
	ChangePct   *float64 `json:"change_pct"`
	VolumeRatio *float64 `json:"volume_ratio"`
	MA20        *float64 `json:"ma20"`
}

// Fetch retrieves readings for the given symbols, chunking to the service's
// per-request cap. Any chunk failing aborts the whole call: a partial map would
// let the job silently skip half its conditions, so the caller gets an error
// and (crucially) writes nothing.
func (c *Client) Fetch(ctx context.Context, symbols []string) (map[string]watchcond.Reading, error) {
	unique := dedupeSymbols(symbols)
	out := make(map[string]watchcond.Reading, len(unique))
	for start := 0; start < len(unique); start += MaxSymbolsPerRequest {
		end := start + MaxSymbolsPerRequest
		if end > len(unique) {
			end = len(unique)
		}
		chunk, err := c.fetchChunk(ctx, unique[start:end])
		if err != nil {
			return nil, err
		}
		for code, reading := range chunk {
			out[code] = reading
		}
	}
	return out, nil
}

func (c *Client) fetchChunk(ctx context.Context, symbols []string) (map[string]watchcond.Reading, error) {
	endpoint := fmt.Sprintf("%s/api/quotes?symbols=%s",
		c.baseURL, url.QueryEscape(strings.Join(symbols, ",")))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("quotes: build request: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("quotes: request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("quotes: read response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("quotes: HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var parsed quotesResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, fmt.Errorf("quotes: decode response: %w", err)
	}
	if parsed.Code != 0 {
		return nil, fmt.Errorf("quotes: service returned code %d", parsed.Code)
	}

	out := make(map[string]watchcond.Reading, len(parsed.Data))
	for code, raw := range parsed.Data {
		var entry quoteEntry
		if err := json.Unmarshal(raw, &entry); err != nil {
			return nil, fmt.Errorf("quotes: decode %s: %w", code, err)
		}
		// The map key is authoritative: it is what the caller asked for, and an
		// entry whose own thscode disagrees is malformed rather than a
		// different symbol.
		out[code] = watchcond.Reading{
			Name:        entry.Name,
			Date:        entry.Date,
			Close:       entry.Close,
			ChangePct:   entry.ChangePct,
			VolumeRatio: entry.VolumeRatio,
			MA20:        entry.MA20,
		}
	}
	return out, nil
}

func dedupeSymbols(symbols []string) []string {
	seen := make(map[string]bool, len(symbols))
	out := make([]string, 0, len(symbols))
	for _, s := range symbols {
		s = strings.TrimSpace(s)
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
