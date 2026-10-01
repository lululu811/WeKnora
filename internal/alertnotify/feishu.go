// Package alertnotify pushes condition-trigger alerts to a Feishu custom bot.
//
// It is deliberately a single-POST, best-effort channel: the watchlist page is
// the primary surface (the trigger events are written regardless of delivery),
// and the push is the bonus. A failed push must therefore never be able to
// retract or hide the fact recorded in the event log — it only records its own
// failure.
package alertnotify

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/config"
)

// maxAttempts is the total number of HTTP attempts for one run's message
// (1 initial + 2 retries), matching the "retry transient failures up to 3
// times" contract.
const maxAttempts = 3

// defaultTimeout bounds one attempt. The message is a few hundred bytes; the
// slack is for a slow webhook, not for hangs.
const defaultTimeout = 10 * time.Second

// FeishuNotifier posts one aggregated message to a Feishu custom-bot webhook.
type FeishuNotifier struct {
	webhook string
	http    *http.Client
	// sleep is the retry backoff seam; tests replace it with a no-op.
	sleep func(time.Duration)
}

// NewFeishuNotifier builds the notifier from config (which has already applied
// the WEKNORA_FEISHU_ALERT_WEBHOOK env override — see config.LoadConfig). An
// empty webhook is a valid configuration: Enabled() reports false and Send
// becomes a no-op, so the job can still write the trigger events.
func NewFeishuNotifier(cfg *config.Config) *FeishuNotifier {
	webhook := ""
	if cfg != nil && cfg.StockWatch != nil {
		webhook = strings.TrimSpace(cfg.StockWatch.FeishuAlertWebhook)
	}
	return newFeishuNotifier(webhook, defaultTimeout)
}

func newFeishuNotifier(webhook string, timeout time.Duration) *FeishuNotifier {
	return &FeishuNotifier{
		webhook: strings.TrimSpace(webhook),
		http:    &http.Client{Timeout: timeout},
		sleep:   time.Sleep,
	}
}

// Enabled reports whether a webhook is configured. The job checks this before
// calling Send so it can log the honest reason ("no webhook → events written,
// nothing pushed") in one place.
func (n *FeishuNotifier) Enabled() bool {
	return n != nil && n.webhook != ""
}

// feishuPayload is the custom-bot text message shape.
type feishuPayload struct {
	MsgType string `json:"msg_type"`
	Content struct {
		Text string `json:"text"`
	} `json:"content"`
}

// feishuResponse covers both shapes a custom bot can answer with: the modern
// {"code":0,"msg":"success"} and the legacy {"StatusCode":0,...}.
type feishuResponse struct {
	Code          *int   `json:"code"`
	Msg           string `json:"msg"`
	StatusCode    *int   `json:"StatusCode"`
	StatusMessage string `json:"StatusMessage"`
}

// Send posts one message, retrying transient failures.
//
// It returns the number of attempts made so the audit row can say "tried 3
// times and all failed" rather than merely "failed". A permanent failure (a
// 4xx that is not 429 — a revoked webhook, a malformed payload) is not retried:
// hammering a door that is nailed shut only burns the scheduler.
func (n *FeishuNotifier) Send(ctx context.Context, message string) (int, error) {
	if !n.Enabled() {
		return 0, nil
	}
	body, err := json.Marshal(feishuPayload{
		MsgType: "text",
		Content: struct {
			Text string `json:"text"`
		}{Text: message},
	})
	if err != nil {
		return 0, fmt.Errorf("feishu: encode payload: %w", err)
	}

	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		retryable, err := n.post(ctx, body)
		if err == nil {
			return attempt, nil
		}
		lastErr = err
		if !retryable {
			return attempt, err
		}
		if attempt < maxAttempts {
			// Linear backoff: 1s, 2s. Long enough for a blip, short enough that
			// the 08:30 run still finishes well before anyone reads it.
			n.sleep(time.Duration(attempt) * time.Second)
		}
	}
	return maxAttempts, fmt.Errorf("feishu: giving up after %d attempts: %w", maxAttempts, lastErr)
}

// post performs one attempt. retryable reports whether the failure is worth
// another try.
func (n *FeishuNotifier) post(ctx context.Context, body []byte) (retryable bool, err error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, n.webhook, bytes.NewReader(body))
	if err != nil {
		return false, fmt.Errorf("feishu: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := n.http.Do(req)
	if err != nil {
		// Transport-level failures (DNS, connection reset, timeout) are exactly
		// the transient class retries exist for.
		return true, fmt.Errorf("feishu: request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return true, fmt.Errorf("feishu: read response: %w", readErr)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500,
			fmt.Errorf("feishu: HTTP %d: %s", resp.StatusCode, truncate(string(respBody), 200))
	}

	// A 2xx with a non-zero business code is still a failed delivery; Feishu
	// uses 200 + code for "your webhook is wrong".
	var parsed feishuResponse
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return false, fmt.Errorf("feishu: decode response: %w (body: %s)", err, truncate(string(respBody), 200))
	}
	if parsed.Code != nil && *parsed.Code != 0 {
		return false, fmt.Errorf("feishu: rejected with code %d: %s", *parsed.Code, parsed.Msg)
	}
	if parsed.StatusCode != nil && *parsed.StatusCode != 0 {
		return false, fmt.Errorf("feishu: rejected with status %d: %s", *parsed.StatusCode, parsed.StatusMessage)
	}
	return false, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
