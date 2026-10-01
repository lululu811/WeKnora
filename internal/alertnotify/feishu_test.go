package alertnotify

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/config"
)

// noSleep removes the retry backoff so the tests do not actually wait.
func noSleep(n *FeishuNotifier) *FeishuNotifier {
	n.sleep = func(time.Duration) {}
	return n
}

func TestEnabledReflectsWebhookConfiguration(t *testing.T) {
	assert.False(t, newFeishuNotifier("", time.Second).Enabled())
	assert.True(t, newFeishuNotifier("https://example.com/hook", time.Second).Enabled())

	// 配置为空：Send 是 no-op，不返回错误 —— 作业据此照常写事件。
	attempts, err := noSleep(newFeishuNotifier("", time.Second)).Send(context.Background(), "hi")
	require.NoError(t, err)
	assert.Zero(t, attempts)

	// 配置字段的接线。
	cfg := &config.Config{StockWatch: &config.StockWatchConfig{FeishuAlertWebhook: " https://example.com/hook "}}
	assert.True(t, NewFeishuNotifier(cfg).Enabled())
	assert.False(t, NewFeishuNotifier(&config.Config{}).Enabled())
}

func TestSendPostsOneTextMessage(t *testing.T) {
	var got feishuPayload
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		require.NoError(t, json.Unmarshal(body, &got))
		assert.Equal(t, "application/json", r.Header.Get("Content-Type"))
		_, _ = w.Write([]byte(`{"code":0,"msg":"success"}`))
	}))
	defer srv.Close()

	attempts, err := noSleep(newFeishuNotifier(srv.URL, time.Second)).
		Send(context.Background(), "【个股条件提醒】…")
	require.NoError(t, err)
	assert.Equal(t, 1, attempts)
	assert.Equal(t, "text", got.MsgType)
	assert.Equal(t, "【个股条件提醒】…", got.Content.Text)
}

// 暂时性失败重试，最多 3 次。
func TestSendRetriesTransientFailures(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if atomic.AddInt32(&calls, 1) < 3 {
			http.Error(w, "flaky", http.StatusBadGateway)
			return
		}
		_, _ = w.Write([]byte(`{"code":0,"msg":"success"}`))
	}))
	defer srv.Close()

	attempts, err := noSleep(newFeishuNotifier(srv.URL, time.Second)).Send(context.Background(), "hi")
	require.NoError(t, err)
	assert.Equal(t, 3, attempts, "前两次 502 应被重试，第三次成功")
	assert.Equal(t, int32(3), atomic.LoadInt32(&calls))
}

func TestSendGivesUpAfterThreeTransientFailures(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "down", http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	attempts, err := noSleep(newFeishuNotifier(srv.URL, time.Second)).Send(context.Background(), "hi")
	require.Error(t, err)
	assert.Equal(t, 3, attempts)
	assert.Equal(t, int32(3), atomic.LoadInt32(&calls), "不能超过 3 次")
}

// 永久性失败不重试：420 之外的 4xx 是"门钉死了"，砸它只浪费调度。
func TestSendDoesNotRetryPermanentFailures(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "bad webhook", http.StatusBadRequest)
	}))
	defer srv.Close()

	attempts, err := noSleep(newFeishuNotifier(srv.URL, time.Second)).Send(context.Background(), "hi")
	require.Error(t, err)
	assert.Equal(t, 1, attempts)
	assert.Equal(t, int32(1), atomic.LoadInt32(&calls))
}

// HTTP 200 但业务码非 0 仍是失败，且不重试（这是配置错了，不是网络抖动）。
func TestSendTreatsNonZeroBusinessCodeAsPermanentFailure(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		_, _ = w.Write([]byte(`{"code":19021,"msg":"sign match fail"}`))
	}))
	defer srv.Close()

	attempts, err := noSleep(newFeishuNotifier(srv.URL, time.Second)).Send(context.Background(), "hi")
	require.Error(t, err)
	assert.Equal(t, 1, attempts)
	assert.Equal(t, int32(1), atomic.LoadInt32(&calls))
}
