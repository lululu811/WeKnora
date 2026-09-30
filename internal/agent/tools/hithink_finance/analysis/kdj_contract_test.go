package analysis

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// 本文件只证明一件事：KDJ 三个值**真的**从 DuckDB 读进了 marketRow，而不是
// 只在 SQL 里"选了个看起来对的名字"然后恒读出 0。
//
// 选一列但读到 0 和根本没选这一列，在下游是完全同一种故障：K<20 会被读成
// 超卖，K>80 会被读成超买。所以只断言列名字符串出现是不够的——必须断言
// 真实数值能穿过 FetchMarketData 落到结构体字段上。

// realKdjRow 是从真实 DuckDB 里取的 600519.SH 五根 K 线。
// 数据来源（只读查询，未改动任何数据库）：
//
//	duckdb -readonly ~/.hithink-finance/indicators.duckdb -c \
//	  "SELECT CAST(date AS VARCHAR), momentum_kdj_9_3_k, momentum_kdj_9_3_d,
//	   momentum_kdj_9_3_j FROM v_indicators_daily
//	   WHERE thscode='600519.SH' ORDER BY date DESC LIMIT 5"
//
//	duckdb -readonly ~/.hithink-finance/market.duckdb -c \
//	  "SELECT CAST(date AS VARCHAR), open, high, low, close, volume
//	   FROM v_daily_qfq WHERE thscode='600519.SH' ORDER BY date DESC LIMIT 5"
//
// 两个库的日期一一对应，所以 FetchMarketData 的按日期合并能真正配上行。
type realBar struct {
	date                        string
	open, high, low, close, vol float64
	k, d, j                     float64
}

var realBars = []realBar{
	{"2026-09-29", 1244.60, 1245.87, 1230.88, 1235.58, 2636630, 15.923718364528511, 13.834851623295632, 20.10145184699427},
	{"2026-09-28", 1236.00, 1244.01, 1228.10, 1243.88, 2821830, 15.907761847134065, 12.790418252679192, 22.142449036043807},
	{"2026-09-24", 1250.01, 1256.13, 1231.05, 1237.00, 3123935, 9.872281068573285, 11.231746455451756, 7.153350294816342},
	{"2026-09-23", 1255.03, 1271.50, 1250.89, 1251.24, 3098122, 9.347702072757098, 11.911479148890992, 4.22014792048931},
	{"2026-09-22", 1252.15, 1265.88, 1248.10, 1253.80, 2457294, 9.895403306244603, 13.193367686957938, 3.299474544817933},
}

// kdjServer 起一个假 python-service /query/ 端点：把 FetchMarketData 真实发出
// 的 SQL 接住，并回放上面那批真实行。captured 收下 SQL 供断言使用。
func kdjServer(t *testing.T, captured *[]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			DB  string `json:"db"`
			SQL string `json:"sql"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("解请求失败: %v", err)
		}
		*captured = append(*captured, req.SQL)

		var data []map[string]interface{}
		if strings.Contains(req.SQL, "v_indicators_daily") {
			// 只放 date 和 kdj 三列：其余指标键缺失会 toF64→0，
			// 这样一旦 KDJ 读成 0，就一定是 KDJ 自己没接上。
			for _, b := range realBars {
				data = append(data, map[string]interface{}{
					"date": b.date, "kdj_k": b.k, "kdj_d": b.d, "kdj_j": b.j,
				})
			}
		} else {
			for _, b := range realBars {
				data = append(data, map[string]interface{}{
					"date": b.date, "open": b.open, "high": b.high,
					"low": b.low, "close": b.close, "vol": b.vol,
				})
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"success": true, "db": req.DB, "count": len(data), "data": data,
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// kdjConfig 指向假服务。Timeout 必须显式给：QueryDuckDBParams 用
// context.WithTimeout(ctx, config.Timeout)，零值会让请求瞬间超时。
func kdjConfig(srv *httptest.Server) *hithink_finance.Config {
	return &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 10 * time.Second}
}

// TestIndicatorSQLSelectsKDJColumns 锁住 SQL 契约：跨栈列名必须与
// python-service/zettaranc/data_loader.py 逐字相同，否则两个栈算的
// 根本不是同一个 KDJ。internal/indicators 的 conformance 测试也查这一条。
func TestIndicatorSQLSelectsKDJColumns(t *testing.T) {
	var captured []string
	srv := kdjServer(t, &captured)
	cfg := kdjConfig(srv)

	if _, err := FetchMarketData(context.Background(), cfg, "600519.SH", 20); err != nil {
		t.Fatalf("FetchMarketData: %v", err)
	}

	var indSQL string
	for _, q := range captured {
		if strings.Contains(q, "v_indicators_daily") {
			indSQL = q
		}
	}
	if indSQL == "" {
		t.Fatal("没有抓到 indicatorSQL")
	}
	for _, col := range []string{"momentum_kdj_9_3_k", "momentum_kdj_9_3_d", "momentum_kdj_9_3_j"} {
		if !strings.Contains(indSQL, col) {
			t.Errorf("indicatorSQL 里没有 %s", col)
		}
	}
}

// TestKDJValuesReachMarketRowNonZero 是核心断言：真实 DuckDB 的 K/D/J 数值
// 穿过 FetchMarketData 之后，在 marketRow 上仍是**非零且与源值相等**的。
// 它挡住"列选了但键名拼错 → 恒读 0"这种换了个马甲的同一个静默故障。
func TestKDJValuesReachMarketRowNonZero(t *testing.T) {
	var captured []string
	srv := kdjServer(t, &captured)
	cfg := kdjConfig(srv)

	rows, err := FetchMarketData(context.Background(), cfg, "600519.SH", 20)
	if err != nil {
		t.Fatalf("FetchMarketData: %v", err)
	}
	if len(rows) != len(realBars) {
		t.Fatalf("拿到 %d 行，期望 %d", len(rows), len(realBars))
	}

	// rows[0] 是最新一根（ORDER BY date DESC），与 realBars 同序。
	nonZero := 0
	for i, r := range rows {
		b := realBars[i]
		if r.Date != b.date {
			t.Fatalf("第 %d 行日期 %s，期望 %s", i, r.Date, b.date)
		}
		if !r.IndicatorValid {
			t.Errorf("%s 应当匹配到指标行", r.Date)
		}
		if r.KDJK == 0 || r.KDJD == 0 || r.KDJJ == 0 {
			t.Errorf("%s KDJ 读成 0（K=%v D=%v J=%v）—— 列选了但没接上",
				r.Date, r.KDJK, r.KDJD, r.KDJJ)
			continue
		}
		nonZero++
		if math.Abs(r.KDJK-b.k) > 1e-9 || math.Abs(r.KDJD-b.d) > 1e-9 || math.Abs(r.KDJJ-b.j) > 1e-9 {
			t.Errorf("%s KDJ 与 DuckDB 源值不符：got K=%v D=%v J=%v, want K=%v D=%v J=%v",
				r.Date, r.KDJK, r.KDJD, r.KDJJ, b.k, b.d, b.j)
		}
	}
	if nonZero != len(realBars) {
		t.Fatalf("只有 %d/%d 行的 KDJ 非零", nonZero, len(realBars))
	}
	t.Logf("真实 fixture 上 %d/%d 行 KDJ 非零，最新一根 K=%.6f D=%.6f J=%.6f",
		nonZero, len(realBars), rows[0].KDJK, rows[0].KDJD, rows[0].KDJJ)
}
