// Package fakeservice 是金融工具契约测试用的假 python-service。
//
// 它按查询里的表名返回不同的数据：行情表给一段确定的价格序列，指标表给一段
// 对应的指标序列（**同一个日期序列**——analysis 的两个 DuckDB 是独立只读连接、
// ATTACH 不了，合并在 Go 里按日期做，两边日期对不上就合不出指标）。
//
// 为什么它住在仓库里而不是测试文件里：契约测试要拿它跟"迁移前的行为"逐字比对，
// 而那份行为是用同一套假数据采下来的。两边必须用**同一份**假服务，否则比的是
// 两套不同的数据，比出来的差异没有意义。
//
// 采集程序（迁移前那份代码的快照）也用这个包——它在 git HEAD 的工作树里跑，
// 所以要先把本包拷进去。
package fakeservice

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

// SeriesDays 是行情/指标序列的长度——技术分析工具至少要 20 根才肯出结论。
const SeriesDays = 60

// NoDataCode 是"查不到数据"的探针代码：绑它就返回空集。
const NoDataCode = "NO_DATA"

// Request 是 python-service 收到的一次查询。
type Request struct {
	DB     string        `json:"db"`
	SQL    string        `json:"sql"`
	Params []interface{} `json:"params"`
}

// Server 是假 python-service。
type Server struct {
	// URL 是服务的地址。
	URL string
	// Last 是最后一次收到的查询。
	Last Request
	// Requests 是收到的全部查询，按顺序。
	//
	// 多查询的工具（市场情绪统计要查涨停池、跌停池、炸板池）一次调用会发好几条，
	// 只记最后一条就漏掉了前面几条——契约测试要逐条比。
	Requests []Request

	mu  sync.Mutex
	srv *httptest.Server
}

// New 起一个假 python-service。
func New() *Server {
	s := &Server{}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 解到一个**全新的**变量里：json 解码进已用过的结构体是"合并"而不是
		// "替换"——上一次的切片会留下残留，下一次读到的参数就串了。
		var req Request
		_ = json.NewDecoder(r.Body).Decode(&req)
		s.mu.Lock()
		s.Last = req
		s.Requests = append(s.Requests, req)
		s.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"success": true, "db": s.Last.DB, "count": len(s.rows()), "data": s.rows(),
		})
	}))
	s.URL = s.srv.URL
	return s
}

// Close 关掉服务。
func (s *Server) Close() { s.srv.Close() }

// Snapshot 返回当前记录的请求列表的一份拷贝。
func (s *Server) Snapshot() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.Requests))
	copy(out, s.Requests)
	return out
}

// Reset 清掉已记录的请求。
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Requests = nil
	s.Last = Request{}
}

// rows 按查询里的表名决定回什么数据。
func (s *Server) rows() []map[string]any {
	noData := false
	for _, p := range s.Last.Params {
		if v, ok := p.(string); ok && v == NoDataCode {
			noData = true
		}
	}
	if noData {
		return nil
	}
	n := s.limit()
	switch {
	case strings.Contains(s.Last.SQL, "information_schema.columns"):
		return catalogRows()
	case strings.Contains(s.Last.SQL, "v_index_daily") && strings.Contains(s.Last.SQL, "LAG("):
		// 指数概览：自己用窗口函数算了涨跌，所以按"这个查询自己算了涨跌"回数据。
		//
		// 只按表名分派不够——index.sector.daily 查的也是 v_index_daily，只是它
		// 查的是**板块**日线、且不算涨跌。
		return indexDailyRows()
	case strings.Contains(s.Last.SQL, "v_indicators_daily"):
		return indicatorRows(n)
	case strings.Contains(s.Last.SQL, "v_daily_qfq"):
		return marketRows(n)
	default:
		return []map[string]any{SampleRow()}
	}
}

// indexDailyRows 是 v_index_daily 的应答：index.overview 读的就是它。
//
// 形状与真库一致——七个宽基指数，每个带点位、涨跌与涨跌幅。**这不是随手编的
// 数字**：字段名是这个工具与模型之间的契约，快照记的是"输出长什么样"，而
// 假服务给的行就是快照里那些行。返回一行形状不对的样本，会让快照把一个
// 根本不存在的输出结构记成基线。
//
// 点位与涨跌自成一体：涨跌 = 点位 - 前收，涨跌幅 = 涨跌 / 前收 × 100。
func indexDailyRows() []map[string]any {
	type idx struct {
		code string
		name string
		last float64
		prev float64
	}
	list := []idx{
		{"000001.SH", "上证指数", 3690.48, 3658.03},
		{"399001.SZ", "深证成指", 11673.826, 11357.316},
		{"399006.SZ", "创业板指", 2684.097, 2576.897},
		{"000688.SH", "科创50", 1345.51, 1305.87},
		{"000300.SH", "沪深300", 4105.33, 4036.30},
		{"000905.SH", "中证500", 6814.44, 6655.25},
		{"000852.SH", "中证1000", 6734.55, 6578.27},
	}
	out := make([]map[string]any, 0, len(list))
	for _, x := range list {
		change := round2(x.last - x.prev)
		ratio := 0.0
		if x.prev != 0 {
			ratio = round2(change / x.prev * 100)
		}
		out = append(out, map[string]any{
			"trade_date":         "2026-10-23",
			"thscode":            x.code,
			"name":               x.name,
			"tag":                "index",
			"last_price":         x.last,
			"price_change":       change,
			"price_change_ratio": ratio,
			"open":               x.last - 10,
			"high":               x.last + 20,
			"low":                x.last - 30,
			"volume":             5.0e9,
			"turnover":           8.9e11,
		})
	}
	return out
}

// round2 四舍五入到两位小数——与 SQL 里 ROUND(x, 2) 对齐。
func round2(v float64) float64 {
	return math.Round(v*100) / 100
}

// catalogRows 是 information_schema.columns 的应答：discover 工具的列清单模式
// 读的就是它。
//
// 列名刻意**不与任何工具的 SELECT 清单重叠**：discover 的输出里有一项
// "consumed_total"（被现成工具消费的列数），它由嵌入的源码算出来。列名一旦
// 与真实指标列重名，那一项就跟着源码漂——冻结下来的快照会在下一次迁移时
// 莫名失配，而失配的原因与被测行为无关。
//
// 列名同时带上不同列族前缀，好让分组概览那条路径也有东西可分。
func catalogRows() []map[string]any {
	names := []string{
		"momentum_probe_alpha", "momentum_probe_beta", "volatility_probe_gamma",
		"trend_probe_delta", "statistics_probe_epsilon", "probe_zeta",
	}
	out := make([]map[string]any, 0, len(names))
	for _, name := range names {
		out = append(out, map[string]any{
			"column_name": name, "data_type": "DOUBLE",
			"total_columns": len(names),
		})
	}
	return out
}

// limit 从请求里取出这条 SQL 实际会取多少行。
func (s *Server) limit() int {
	if m := limitRe.FindStringSubmatch(s.Last.SQL); m != nil {
		n, _ := strconv.Atoi(m[1])
		return n
	}
	if limitParamRe.MatchString(strings.TrimSpace(s.Last.SQL)) && len(s.Last.Params) > 0 {
		if f, ok := s.Last.Params[len(s.Last.Params)-1].(float64); ok {
			return int(f)
		}
	}
	return 1
}

// SampleRow 是"不属于行情/指标"的查询得到的那一行。
//
// 字段取各工具 SELECT 到的列的超集；各字段的值**互不相同**——否则"取错了列"
// 这种错误根本看不出来。日期取未来值，数据新鲜度告警于是不会触发，冻结下来的
// 输出不会随时间漂移。
func SampleRow() map[string]any {
	return map[string]any{
		"capture_date": "2026-12-30", "code": "CODE001", "close": 100.0,
		"date": "2026-12-31", "high": 101.0, "last_price": 100.0, "low": 98.0,
		"name": "样本股", "open": 99.0, "price_change_ratio": 1.0,
		"snapshot_date": "2026-12-29", "ticker": "TICKER001", "thscode": "600519.SH",
		"trade_date": "2026-12-28", "turnover": 100000.0, "volume": 1000.0,
	}
}

// Dates 造出一段确定的交易日序列。
func Dates() []string {
	out := make([]string, 0, SeriesDays)
	d := 20260803
	for len(out) < SeriesDays {
		y, m, dd := d/10000, (d/100)%100, d%100
		wd := (y*372 + m*31 + dd) % 7
		if wd != 0 && wd != 6 {
			out = append(out, fmt.Sprintf("%04d-%02d-%02d", y, m, dd))
		}
		d++
	}
	return out
}

func marketRows(n int) []map[string]any {
	dates := Dates()
	if n > len(dates) {
		n = len(dates)
	}
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		c := 100.0 + float64(i)*0.35 + float64(i%7)*0.2
		out = append(out, map[string]any{
			"date": dates[i], "open": c - 0.4, "high": c + 1.1, "low": c - 1.3,
			"close": c, "vol": 1000.0 + float64(i)*17,
		})
	}
	return out
}

// indicatorRow 的列名与 analysis/data.go 的 indicatorSQL 别名一致。
func indicatorRows(n int) []map[string]any {
	dates := Dates()
	if n > len(dates) {
		n = len(dates)
	}
	out := make([]map[string]any, 0, n)
	for i := 0; i < n; i++ {
		c := 100.0 + float64(i)*0.35 + float64(i%7)*0.2
		r := map[string]any{"date": dates[i]}
		ma := map[string]float64{
			"ma5": c - 0.2, "ma10": c - 0.6, "ma20": c - 1.1,
			"ma60": c - 2.0, "ma120": c - 3.4, "ma250": c - 5.0,
			"dif": c - 4.0, "dea": c - 4.3, "macd_hist": 0.6,
			"rsi6": 52.0 + float64(i%13), "rsi14": 55.0 + float64(i%9),
			"kdj_k": 68.0 + float64(i%5), "kdj_d": 62.0 + float64(i%4), "kdj_j": 74.0 + float64(i%6),
			"adx": 21.0 + float64(i%17), "di_plus": 15.0 + float64(i%8), "di_minus": 11.0 + float64(i%6),
			"st_dir": 1.0, "st_val": c - 3.0,
			"bb_upper": c + 4.0, "bb_mid": c, "bb_lower": c - 4.0,
			"atr": 2.4, "cmf": 0.06 + float64(i%5)*0.01, "mfi": 58.0 + float64(i%11),
			"obv": 1000.0 + float64(i)*13, "vwap": c - 0.3,
			"aroon_up": 70.0 + float64(i%12), "aroon_down": 30.0 + float64(i%7),
			"lin_slope": 0.11 + float64(i%3)*0.02, "zscore": 0.4 + float64(i%5)*0.1,
			"cdl_hammer": 0.0, "cdl_shooting_star": 0.0, "cdl_doji": 0.0,
			"cdl_engulfing": 0.0, "cdl_harami": 0.0, "cdl_morning_star": 0.0,
			"cdl_evening_star": 0.0, "cdl_piercing": 0.0, "cdl_dark_cloud": 0.0,
			"cdl_3white": 0.0, "cdl_3black": 0.0,
			"ztr_white": c - 2.2, "ztr_yellow": c - 1.8, "ztr_bbi": c - 0.9,
		}
		for k, v := range ma {
			r[k] = v
		}
		out = append(out, r)
	}
	return out
}

var (
	limitRe      = regexp.MustCompile(`LIMIT\s+(\d+)`)
	limitParamRe = regexp.MustCompile(`LIMIT\s+\?\s*$`)
)
