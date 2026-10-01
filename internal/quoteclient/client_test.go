package quoteclient

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// 真实的 /api/quotes 形状：data 是以 thscode 为键的 map，数值可为 null，
// 另有 missing/invalid 两个显式字段。
func TestFetchParsesReadingsAndPreservesNulls(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/api/quotes", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"code": 0,
			"data": {
				"600519.SH": {
					"thscode": "600519.SH", "name": "贵州茅台", "exchange": "SH",
					"date": "2026-10-02", "close": 1204.53, "change_pct": -3.2,
					"ma20": 1250.0, "volume_ratio": 2.31
				},
				"000001.SZ": {
					"thscode": "000001.SZ", "name": "平安银行",
					"date": null, "close": null, "change_pct": null, "ma20": null, "volume_ratio": null
				}
			},
			"missing": ["300750.SZ"],
			"invalid": []
		}`))
	}))
	defer srv.Close()

	c := NewClientWithBase(srv.URL, 0)
	readings, err := c.Fetch(context.Background(), []string{"600519.SH", "000001.SZ", "300750.SZ"})
	require.NoError(t, err)

	require.Contains(t, readings, "600519.SH")
	full := readings["600519.SH"]
	assert.Equal(t, "贵州茅台", full.Name)
	assert.Equal(t, "2026-10-02", full.Date)
	require.NotNil(t, full.Close)
	assert.InDelta(t, 1204.53, *full.Close, 1e-9)
	require.NotNil(t, full.ChangePct)
	assert.InDelta(t, -3.2, *full.ChangePct, 1e-9)
	require.NotNil(t, full.VolumeRatio)
	assert.InDelta(t, 2.31, *full.VolumeRatio, 1e-9)
	require.NotNil(t, full.MA20)

	// null 必须原样保留为 nil —— 0 在这套语义里是"真的等于零"。
	require.Contains(t, readings, "000001.SZ")
	empty := readings["000001.SZ"]
	assert.Nil(t, empty.Close)
	assert.Nil(t, empty.ChangePct)
	assert.Nil(t, empty.VolumeRatio)
	assert.Nil(t, empty.MA20)
	assert.Empty(t, empty.Date)

	// missing 不是错误：没有本地数据的票由评估器当作"判不了"。
	assert.NotContains(t, readings, "300750.SZ")
}

// 一次最多 200 只：超过就分块，且块内不含重复。
func TestFetchChunksAboveTheServiceLimit(t *testing.T) {
	var mu sync.Mutex
	var batchSizes []int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		symbols := strings.Split(r.URL.Query().Get("symbols"), ",")
		mu.Lock()
		batchSizes = append(batchSizes, len(symbols))
		mu.Unlock()
		data := map[string]any{}
		for _, s := range symbols {
			data[s] = map[string]any{"thscode": s, "date": "2026-10-02", "close": 1.0}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": data})
	}))
	defer srv.Close()

	symbols := make([]string, 0, 250)
	for i := range 250 {
		symbols = append(symbols, fmt.Sprintf("%06d.SH", i))
	}
	// 混入重复项：去重后应仍是 250 只，分两块（200 + 50）。
	symbols = append(symbols, symbols[0], symbols[1])

	c := NewClientWithBase(srv.URL, 0)
	readings, err := c.Fetch(context.Background(), symbols)
	require.NoError(t, err)
	assert.Len(t, readings, 250)
	mu.Lock()
	defer mu.Unlock()
	require.Len(t, batchSizes, 2, "250 只必须拆成两块")
	for _, n := range batchSizes {
		assert.LessOrEqual(t, n, MaxSymbolsPerRequest)
	}
	assert.Equal(t, 250, batchSizes[0]+batchSizes[1])
}

// 任一块失败 → 整次调用失败：局部结果会让作业悄悄跳过一半条件。
func TestFetchFailsWholeCallWhenAChunkFails(t *testing.T) {
	requests := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if requests == 2 {
			http.Error(w, "boom", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":0,"data":{"600519.SH":{"thscode":"600519.SH","date":"2026-10-02","close":1}}}`))
	}))
	defer srv.Close()

	symbols := make([]string, 0, 201)
	for i := range 201 {
		symbols = append(symbols, fmt.Sprintf("%06d.SH", i))
	}
	c := NewClientWithBase(srv.URL, 0)
	readings, err := c.Fetch(context.Background(), symbols)
	require.Error(t, err)
	assert.Nil(t, readings, "失败时不能返回部分结果")
}
