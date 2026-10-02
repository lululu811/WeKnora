// Command gencatalog 从 ../testdata/schema.json 快照生成 SQLQueryTool.Description()
// 使用的表/列目录（query/sql_query_catalog.go）。
//
// 为什么生成：工具描述是模型看到的**唯一** schema 目录，而它此前是手写的，
// 2026-09 排查发现它与真实库不符（v_symbol/v_futures_daily/v_fund_nav 的列、
// statistic_* 前缀等）。快照 schema.json 是 schema_contract_test.go 的既有基线，
// 这里直接拿它当目录数据源，再手写只会再次腐烂。
//
// 生成命令（从仓库根）：
//
//	go generate ./internal/agent/tools/hithink_finance/query
//
// 或：
//
//	cd internal/agent/tools/hithink_finance/query && go run ./gencatalog
//
// 产物是**静态字符串**：工具描述在运行时不读任何文件，无文件系统依赖也要稳定。
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"sort"
	"strings"
)

const (
	schemaPath = "../testdata/schema.json"
	outputPath = "sql_query_catalog.go"

	// 列数 ≤ fullListMax 的视图完整列出；更宽的视图只列前 truncateHead 列，
	// 再附「… 共 N 列」。宽表全列会淹没描述，而宽表的用法本就该先读快照。
	fullListMax  = 24
	truncateHead = 8
)

type object struct {
	Type    string   `json:"type"`
	Columns []string `json:"columns"`
}

// dbSpec 描述目录里一个库的展示：中文小节标题、视图展示顺序（未列出的按字母序
// 补在末尾）、每个视图的一句人工注释，以及追加在视图列表后的运营说明。
//
// notes / extra 是快照里没有的**人工运营知识**（表体积、刷新时间、跨库 join
// 约束、语义提示等），随目录一起维护；改动它们不需要动快照。
type dbSpec struct {
	header string
	order  []string
	notes  map[string]string
	extra  string
}

var dbSpecs = []dbSpec{
	{
		header: "【market】行情",
		order:  []string{"v_daily_qfq", "v_daily_hfq", "v_daily", "v_symbol"},
		notes: map[string]string{
			"v_daily_qfq": "前复权日线，做价量分析默认用这个",
			"v_daily_hfq": "后复权，看历史涨幅连续性",
			"v_daily":     "原始不复权",
			"v_symbol":    "代码与名称对照，做 join 用这个",
		},
	},
	{
		header: "【indicators】技术指标",
		order:  []string{"v_indicators_daily"},
		notes: map[string]string{
			"v_indicators_daily": "列族见下方说明",
		},
		extra: `  列族前缀（其余为指标列，完整列名见快照）：
    momentum_*（rsi_*、macd_12_26_9_*、stoch_*…）、overlap_*（ma/ema/boll…）
    trend_*、volume_*、volatility_*、cycles_*（横向/纵向周期）
    statistics_*（beta、correl、linearreg、stddev…）、candles_cdl_*（K线形态）
  该表约 10GB，每天 19:00 刷新。
  ⚠ 没有 price 列；要价量必须与 market.v_daily_qfq 按 (thscode, date) join（两个库是独立只读连接，无法 ATTACH）。
`,
	},
	{
		header: "【financials】基本面（全部以 thscode + period 为键，period 形如 2026Q2）",
		order: []string{
			"v_income_statement", "v_balance_sheet", "v_cash_flow_statement",
			"v_financial_indicators", "v_financial_indicators_detail",
			"v_valuation_latest", "v_trading_calendar",
		},
		notes: map[string]string{
			"v_income_statement":            "利润表",
			"v_balance_sheet":               "资产负债表",
			"v_cash_flow_statement":         "现金流量表",
			"v_financial_indicators":        "能力指标打包在 abilities_json",
			"v_financial_indicators_detail": "abilities 的展开列（yoy / roe / 毛利率等）",
			"v_valuation_latest":            "最新估值快照",
			"v_trading_calendar":            "交易日历",
		},
	},
	{
		header: "【index】指数与行业/概念归属（做行业对比、板块轮动的核心）",
		order:  []string{"v_index_universe", "v_index_constituents", "v_index_daily", "v_index_latest"},
		notes: map[string]string{
			"v_index_universe":     "tag 取值 cn_concept / industry / region / tszs",
			"v_index_constituents": "某指数/板块的成分股；⚠ 只有当前成分快照，无历史成分，历史回测有幸存者偏差",
			"v_index_daily":        "指数/板块行情",
			"v_index_latest":       "指数最新快照",
		},
	},
	{
		header: "【special】特色资金与情绪数据（按 trade_date / snapshot_date + thscode）",
		order: []string{
			"v_limit_up_pool", "v_limit_down_pool", "v_limit_break_pool",
			"v_auction_snapshot", "v_auction_benchmark", "v_dragon_tiger",
			"v_hot_stock", "v_hot_stock_history", "v_hot_stock_rank_trend",
			"v_anomaly_list", "v_skyrocket",
		},
		notes: map[string]string{
			"v_limit_up_pool":        "涨停池",
			"v_limit_down_pool":      "跌停池",
			"v_limit_break_pool":     "炸板池 = 冲高回落 = 派发信号",
			"v_auction_snapshot":     "集合竞价，盘前信号",
			"v_auction_benchmark":    "集合竞价基准",
			"v_dragon_tiger":         "龙虎榜",
			"v_hot_stock":            "热股榜",
			"v_hot_stock_history":    "热股榜历史",
			"v_hot_stock_rank_trend": "热股排名趋势",
			"v_anomaly_list":         "异动",
			"v_skyrocket":            "飙升榜",
		},
	},
	{
		header: "【fund】基金与 ETF",
		order: []string{
			"v_etf_daily", "v_etf_universe", "v_etf_latest",
			"v_fund_nav", "v_fund_profile", "v_fund_holdings",
			"v_fund_returns", "v_fund_holders", "v_fund_top_holders", "v_fund_company",
		},
		notes: map[string]string{
			"v_etf_daily":        "ETF 日线，可做 ETF 轮动",
			"v_etf_universe":     "ETF 列表",
			"v_etf_latest":       "ETF 最新快照",
			"v_fund_nav":         "基金净值（unit_nav / adj_nav / unit_nav_usable）",
			"v_fund_profile":     "基金概况",
			"v_fund_holdings":    "基金重仓股",
			"v_fund_returns":     "阶段收益",
			"v_fund_holders":     "持有人结构",
			"v_fund_top_holders": "前十大持有人",
			"v_fund_company":     "基金公司",
		},
	},
	{
		header: "【futures】期货",
		order:  []string{"v_futures_daily", "v_futures_varieties", "v_futures_contracts", "v_futures_latest", "v_futures_intraday"},
		notes: map[string]string{
			"v_futures_daily":     "期货日线（open_price / high_price / low_price / close_price；无 open_interest）",
			"v_futures_varieties": "品种",
			"v_futures_contracts": "合约",
			"v_futures_latest":    "最新快照",
			"v_futures_intraday":  "分时",
		},
		extra: "- 品种多空持仓只有 raw_futures_positions_variety（无 v_ 视图）\n",
	},
}

func main() {
	raw, err := os.ReadFile(schemaPath)
	if err != nil {
		log.Fatalf("读取 schema 快照失败：%v（请在 query/ 目录下运行）", err)
	}
	var snap map[string]map[string]object
	if err := json.Unmarshal(raw, &snap); err != nil {
		log.Fatalf("schema 快照不是合法 JSON：%v", err)
	}

	catalog := buildCatalog(snap)
	if strings.ContainsRune(catalog, '`') {
		log.Fatal("目录里出现反引号，无法作为 Go 原始字符串字面量内联")
	}

	var out bytes.Buffer
	out.WriteString("// Code generated by \"go generate ./internal/agent/tools/hithink_finance/query\"; DO NOT EDIT.\n")
	out.WriteString("//\n")
	out.WriteString("// 数据源：internal/agent/tools/hithink_finance/testdata/schema.json（真库快照）。\n")
	out.WriteString("// 再生成：go generate ./internal/agent/tools/hithink_finance/query\n")
	out.WriteString("// 或：cd internal/agent/tools/hithink_finance/query && go run ./gencatalog\n")
	out.WriteString("//\n")
	out.WriteString("// 快照被内联为静态字符串：工具描述在运行时不读任何文件。\n\n")
	out.WriteString("package query\n\n")
	out.WriteString("// schemaCatalog 是 SQLQueryTool.Description() 的表/列目录主体：各库的 v_* 视图\n")
	out.WriteString("// 及其列清单（列名一律取自 testdata/schema.json），以及快照里没有的人工运营说明。\n")
	out.WriteString("// 手改本文件会在下一次 go generate 时丢失；要改请改 query/gencatalog/main.go。\n")
	out.WriteString("const schemaCatalog = `")
	out.WriteString(catalog)
	out.WriteString("`\n")

	if err := os.WriteFile(outputPath, out.Bytes(), 0o644); err != nil {
		log.Fatalf("写入 %s 失败：%v", outputPath, err)
	}
	fmt.Fprintf(os.Stderr, "已生成 %s（%d 字节）\n", outputPath, out.Len())
}

// buildCatalog 按 dbSpecs 的顺序渲染目录。快照里出现、但 dbSpecs.order 没列出的
// 视图按字母序补在该库末尾——新增视图不会静默消失，也不会阻断再生成。
func buildCatalog(snap map[string]map[string]object) string {
	var b strings.Builder
	for i, spec := range dbSpecs {
		objs, ok := snap[spec.dbKey()]
		if !ok {
			log.Fatalf("快照里没有库 %q（dbSpecs 的表头：%s）", spec.dbKey(), spec.header)
		}
		if i > 0 {
			b.WriteString("\n")
		}
		b.WriteString(spec.header + "\n")
		for _, name := range orderedViews(spec, objs) {
			line := viewLine(name, objs[name])
			if note := spec.notes[name]; note != "" {
				line += " — " + note
			}
			b.WriteString(line + "\n")
		}
		b.WriteString(spec.extra)
	}
	return b.String()
}

// dbKey 从小节标题里取库名：`【market】行情` → `market`。
func (s dbSpec) dbKey() string {
	h := s.header
	h = strings.TrimPrefix(h, "【")
	if i := strings.Index(h, "】"); i >= 0 {
		return h[:i]
	}
	log.Fatalf("无法从表头 %q 解析库名", s.header)
	return ""
}

func orderedViews(spec dbSpec, objs map[string]object) []string {
	seen := make(map[string]bool, len(objs))
	out := make([]string, 0, len(objs))
	for _, n := range spec.order {
		if _, ok := objs[n]; !ok {
			log.Fatalf("dbSpecs 的 order 列出的 %q 不在快照的 %s 库里（视图被删/改名？）", n, spec.dbKey())
		}
		out = append(out, n)
		seen[n] = true
	}
	var rest []string
	for n := range objs {
		if !seen[n] {
			rest = append(rest, n)
		}
	}
	sort.Strings(rest)
	return append(out, rest...)
}

func viewLine(name string, o object) string {
	cols := o.Columns
	suffix := ""
	if len(cols) > fullListMax {
		cols = cols[:truncateHead]
		suffix = fmt.Sprintf(", … 共 %d 列", len(o.Columns))
	}
	return fmt.Sprintf("- %s(%s%s)", name, strings.Join(cols, ", "), suffix)
}
