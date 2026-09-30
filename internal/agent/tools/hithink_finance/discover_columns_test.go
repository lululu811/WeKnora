package hithink_finance

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/types"
)

// ---------- 测试替身 ----------

type fakeTool struct {
	name string
	desc string
}

func (f fakeTool) Name() string                { return f.name }
func (f fakeTool) Description() string         { return f.desc }
func (f fakeTool) Parameters() json.RawMessage { return json.RawMessage(`{"type":"object"}`) }
func (f fakeTool) Execute(context.Context, json.RawMessage) (*types.ToolResult, error) {
	return &types.ToolResult{Success: true, Output: "unused"}, nil
}

type queryReq struct {
	DB     string        `json:"db"`
	SQL    string        `json:"sql"`
	Limit  int           `json:"limit"`
	Params []interface{} `json:"params"`
}

// fakeIndicatorDB 起一个假 python-service /query/，按 indicators 库的
// information_schema 语义应答。返回的第二个值记录收到的请求，测试据此断言
// 工具查的是**哪个库、带什么参数**——只断言结果不看请求，就无法证明
// "列清单来自实时库而不是某份内置副本"。
func fakeIndicatorDB(t *testing.T, cols []string, declaredTotal int, status int) (*httptest.Server, *[]queryReq) {
	t.Helper()
	var got []queryReq
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/query/") {
			t.Errorf("假服务收到非预期请求：%s %s", r.Method, r.URL.Path)
		}
		var req queryReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("请求体解析失败：%v", err)
		}
		got = append(got, req)
		if status != 0 && status != http.StatusOK {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(`{"success":false,"error":"induced failure"}`))
			return
		}
		total := declaredTotal
		if total == 0 {
			total = len(cols)
		}
		rows := make([]map[string]interface{}, 0, len(cols))
		for _, c := range cols {
			rows = append(rows, map[string]interface{}{
				"column_name":   c,
				"data_type":     "DOUBLE",
				"total_columns": total,
			})
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"success": true, "db": req.DB, "count": len(rows), "data": rows,
		})
	}))
	t.Cleanup(srv.Close)
	return srv, &got
}

func newColumnsTool(t *testing.T, srvURL string) *DiscoverTool {
	t.Helper()
	return NewDiscoverToolWithConfig(tools.NewToolRegistry(), &Config{
		ServiceURL: srvURL,
		Timeout:    5 * time.Second,
	})
}

func callTool(t *testing.T, tool *DiscoverTool, args string) *struct {
	Success bool                   `json:"success"`
	Output  string                 `json:"output"`
	Data    map[string]interface{} `json:"data"`
	Error   string                 `json:"error"`
} {
	t.Helper()
	res, err := tool.Execute(context.Background(), json.RawMessage(args))
	if err != nil {
		t.Fatalf("Execute 返回 error（工具内部失败应通过 ToolResult 表达）：%v", err)
	}
	return &struct {
		Success bool                   `json:"success"`
		Output  string                 `json:"output"`
		Data    map[string]interface{} `json:"data"`
		Error   string                 `json:"error"`
	}{Success: res.Success, Output: res.Output, Data: res.Data, Error: res.Error}
}

func decodeOutput(t *testing.T, out string) map[string]interface{} {
	t.Helper()
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("输出不是合法 JSON：%v\n%s", err, out)
	}
	return m
}

// realCatalog 是 v_indicators_daily 的真实列名子集：覆盖 4 个非指标列、
// 以及有/无"现成工具消费"的各一组。名字必须与真实库一致，否则过滤测试
// 只是在测一个假世界。
var realCatalog = []string{
	"thscode", "date", "backend", "computed_at",
	"overlap_sma_5", "overlap_sma_20", "overlap_ema_12",
	"momentum_rsi_14", "momentum_kdj_9_3_k", "momentum_trix_15", "momentum_macd_12_26_9_macd",
	"volatility_atr_14", "volatility_bbands_20_2_0_upper", "volatility_kc_20_2_upper",
	"volume_obv", "volume_vwap",
	"trend_adx_14", "cycles_ht_dcperiod_0", "statistics_zscore_20",
	"candles_cdl_hammer_0", "zettaranc_score", "performance_1d",
}

// ---------- #5 回归：kind 省略时行为逐字节不变 ----------

func TestDiscoverToolsModeUnchangedWhenKindOmitted(t *testing.T) {
	reg := tools.NewToolRegistry()
	// 名字深度不同，用来钉住 depth 的真实语义：depth 数的是"相对 prefix 的段数"，
	// "hithink.finance.market.price.snapshot" 相对 "hithink.finance.market" 是 2 段。
	reg.RegisterTool(fakeTool{"hithink.finance.market.price", "行情大类"})
	reg.RegisterTool(fakeTool{"hithink.finance.market.price.snapshot", "行情快照"})
	reg.RegisterTool(fakeTool{"hithink.finance.financial.income", "利润表"})
	reg.RegisterTool(fakeTool{"other.tool", "别的系统的工具"})

	tool := NewDiscoverTool(reg)

	t.Run("省略 kind 与显式 kind=tools 输出完全一致", func(t *testing.T) {
		omitted := callTool(t, tool, `{"prefix":"hithink.finance.market","depth":2}`)
		explicit := callTool(t, tool, `{"kind":"tools","prefix":"hithink.finance.market","depth":2}`)
		singular := callTool(t, tool, `{"kind":"tool","prefix":"hithink.finance.market","depth":2}`)
		if !omitted.Success {
			t.Fatalf("kind 省略时不应失败：%s", omitted.Error)
		}
		if omitted.Output != explicit.Output || omitted.Output != singular.Output {
			t.Fatalf("kind 省略与显式 kind=tools 输出不一致：\n省略:\n%s\n显式:\n%s", omitted.Output, explicit.Output)
		}
	})

	t.Run("字段集合与取值与原实现一致", func(t *testing.T) {
		got := decodeOutput(t, callTool(t, tool, `{"prefix":"hithink.finance.market","depth":2}`).Output)
		wantKeys := []string{"count", "depth", "prefix", "tools"}
		keys := make([]string, 0, len(got))
		for k := range got {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		if strings.Join(keys, ",") != strings.Join(wantKeys, ",") {
			t.Fatalf("tools 模式输出字段变了：%v，期望 %v", keys, wantKeys)
		}
		if got["prefix"] != "hithink.finance.market" {
			t.Errorf("prefix = %v", got["prefix"])
		}
		if got["depth"].(float64) != 2 {
			t.Errorf("depth = %v，期望 2", got["depth"])
		}
		if got["count"].(float64) != 2 {
			t.Errorf("count = %v，期望 2", got["count"])
		}
		toolsOut := got["tools"].([]interface{})
		first := toolsOut[0].(map[string]interface{})
		if first["name"] != "hithink.finance.market.price" {
			t.Errorf("工具未按名字排序：%v", first["name"])
		}
		if first["description"] != "行情大类" {
			t.Errorf("description = %v", first["description"])
		}
	})

	t.Run("depth 语义未变：段数超限的不返回", func(t *testing.T) {
		// depth=1 只放行相对 prefix 1 段的工具。
		got := decodeOutput(t, callTool(t, tool, `{"prefix":"hithink.finance.market","depth":1}`).Output)
		if got["count"].(float64) != 1 {
			t.Fatalf("depth=1 应只返回 hithink.finance.market.price，实际 %v", got["count"])
		}
		// 默认调用曾经返回 0 个工具（depth 回落成 1，放不下三段的真实工具名），
		// 而空清单会被模型读成"没有别的金融工具了"——那不是"暂时没数据"，
		// 是一个什么都没说却让模型以为自己知道的静默失效。
		// 这里原来断言"0 是既有行为"，现在反过来钉住修复后的性质：**默认必须列全**。
		// 真正被这条测试保护的是 defaultToolDepth，不是一个具体数字。
		def := decodeOutput(t, callTool(t, tool, `{}`).Output)
		if def["prefix"] != "hithink.finance" {
			t.Errorf("prefix 回落失败：%v", def["prefix"])
		}
		if int(def["depth"].(float64)) != defaultToolDepth {
			t.Errorf("depth 回落成 %v，应为 defaultToolDepth=%d", def["depth"], defaultToolDepth)
		}
		if def["count"].(float64) != 3 {
			t.Errorf("默认参数下 count = %v，期望 3（默认 depth 必须放得下三段工具名）。\n"+
				"返回 0 是本工具最危险的一种输出：没有错误、没有日志，模型只会以为没有别的工具。",
				def["count"])
		}
		if _, ok := def["hint"]; ok {
			t.Error("默认参数下列全了工具，不该再给『未找到』提示")
		}
		// 显式写 depth=defaultToolDepth 必须与省略等价。
		all := decodeOutput(t, callTool(t, tool,
			`{"depth":`+strconv.Itoa(defaultToolDepth)+`}`).Output)
		if all["count"].(float64) != 3 {
			t.Errorf("depth=%d 下 count = %v，期望 3（other.tool 不在 hithink.finance 下）",
				defaultToolDepth, all["count"])
		}
		// 负 depth 同样回落到默认值，而不是回落到 1 —— 回落到 1 就会重现那个空清单。
		neg := decodeOutput(t, callTool(t, tool, `{"depth":-3}`).Output)
		if int(neg["depth"].(float64)) != defaultToolDepth {
			t.Errorf("负 depth 回落成 %v，应为 defaultToolDepth=%d（回落到 1 会重现空清单）",
				neg["depth"], defaultToolDepth)
		}
	})

	t.Run("无命中时给出原来那句提示", func(t *testing.T) {
		got := decodeOutput(t, callTool(t, tool, `{"prefix":"nope.nope"}`).Output)
		hint, _ := got["hint"].(string)
		if !strings.Contains(hint, "未找到前缀为 'nope.nope' 的 tools") {
			t.Errorf("hint 文案变了：%q", hint)
		}
		if got["count"].(float64) != 0 {
			t.Errorf("count = %v，期望 0", got["count"])
		}
	})

	t.Run("无命中时给出原来那句提示", func(t *testing.T) {
		got := decodeOutput(t, callTool(t, tool, `{"prefix":"nope.nope"}`).Output)
		hint, _ := got["hint"].(string)
		if !strings.Contains(hint, "未找到前缀为 'nope.nope' 的 tools") {
			t.Errorf("hint 文案变了：%q", hint)
		}
		if got["count"].(float64) != 0 {
			t.Errorf("count = %v，期望 0", got["count"])
		}
	})

	t.Run("参数不是合法 JSON 时的报错不变", func(t *testing.T) {
		res := callTool(t, tool, `not-json`)
		if res.Success {
			t.Fatal("非法参数应返回 Success=false")
		}
		if !strings.Contains(res.Error, "参数解析失败") {
			t.Errorf("错误文案变了：%q", res.Error)
		}
	})

	t.Run("tools 模式不碰 DuckDB", func(t *testing.T) {
		// 指向一个不可达地址：tools 模式若仍然成功，说明它没有依赖 DB。
		tool := NewDiscoverToolWithConfig(reg, &Config{
			ServiceURL: "http://127.0.0.1:1", Timeout: time.Second,
		})
		if res := callTool(t, tool, `{}`); !res.Success {
			t.Fatalf("tools 模式不应依赖 DuckDB：%s", res.Error)
		}
	})

	t.Run("kind 取值非法时报错并列出合法值", func(t *testing.T) {
		res := callTool(t, tool, `{"kind":"indicator"}`)
		if res.Success {
			t.Fatal("非法 kind 应失败")
		}
		if !strings.Contains(res.Error, "columns") || !strings.Contains(res.Error, "tools") {
			t.Errorf("错误未列出合法取值：%q", res.Error)
		}
	})
}

// ---------- #1 列清单来自实时库 ----------

func TestDiscoverColumnsReadsLiveCatalogFromDuckDB(t *testing.T) {
	srv, got := fakeIndicatorDB(t, realCatalog, 0, 0)
	res := callTool(t, newColumnsTool(t, srv.URL), `{"kind":"columns"}`)
	if !res.Success {
		t.Fatalf("columns 模式失败：%s", res.Error)
	}

	// 请求本身必须是对 indicators 库的信息模式查询，且视图名走绑定参数 ——
	// 这是"列清单来自实时库"这一主张的全部证据。
	if len(*got) != 1 {
		t.Fatalf("期望 1 次查询，实际 %d", len(*got))
	}
	q := (*got)[0]
	if q.DB != indicatorDB {
		t.Errorf("查的库是 %q，期望 %q", q.DB, indicatorDB)
	}
	if !strings.Contains(q.SQL, "information_schema.columns") {
		t.Errorf("SQL 不是 information_schema 查询：%s", q.SQL)
	}
	if len(q.Params) != 1 || q.Params[0] != indicatorView {
		t.Errorf("视图名应作为绑定参数传入，实际 params=%v", q.Params)
	}

	out := decodeOutput(t, res.Output)
	if out["view"] != "indicators.v_indicators_daily" {
		t.Errorf("view = %v", out["view"])
	}
	// 列数必须等于数据库实际报的数，不是某个写死的数。
	if int(out["total_columns"].(float64)) != len(realCatalog) {
		t.Errorf("total_columns = %v，数据库实际 %d 列", out["total_columns"], len(realCatalog))
	}
}

func TestDiscoverColumnsDefaultIsGroupedNotAWallOfNames(t *testing.T) {
	srv, _ := fakeIndicatorDB(t, realCatalog, 0, 0)
	out := decodeOutput(t, callTool(t, newColumnsTool(t, srv.URL), `{"kind":"columns"}`).Output)

	if _, leaked := out["columns"]; leaked {
		t.Error("无过滤时不应返回 columns 全表，那才是上下文污染")
	}
	groups, ok := out["groups"].([]interface{})
	if !ok || len(groups) == 0 {
		t.Fatalf("缺少 groups 概览：%v", out)
	}
	seen := map[string]int{}
	for _, g := range groups {
		entry := g.(map[string]interface{})
		name := entry["group"].(string)
		seen[name] = int(entry["count"].(float64))
		sample := entry["sample"].([]interface{})
		if len(sample) > groupSampleSize {
			t.Errorf("%s 组样本 %d 个，超过上限 %d", name, len(sample), groupSampleSize)
		}
	}
	for _, g := range []string{"momentum", "overlap", "volatility", "candles", "meta"} {
		if _, ok := seen[g]; !ok {
			t.Errorf("概览缺少 %s 组：%v", g, seen)
		}
	}
	// meta 组必须如实包含 4 个非指标列，不能被塞进某个指标列族。
	if seen["meta"] != 4 {
		t.Errorf("meta 组 = %d 列，期望 4（thscode/date/backend/computed_at）", seen["meta"])
	}
}

// ---------- #2 列族过滤 ----------

func TestDiscoverColumnsGroupFilter(t *testing.T) {
	srv, _ := fakeIndicatorDB(t, realCatalog, 0, 0)
	tool := newColumnsTool(t, srv.URL)

	t.Run("group=momentum 只回 momentum_*", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","group":"momentum"}`).Output)
		names := columnNames(t, out)
		if len(names) != 4 {
			t.Fatalf("momentum 组 %d 列：%v", len(names), names)
		}
		for _, n := range names {
			if !strings.HasPrefix(n, "momentum_") {
				t.Errorf("非 momentum 列混入：%s", n)
			}
		}
		if int(out["matched"].(float64)) != 4 {
			t.Errorf("matched = %v", out["matched"])
		}
	})

	t.Run("group 与 match 叠加", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","group":"momentum","match":"TRIX"}`).Output)
		names := columnNames(t, out)
		if len(names) != 1 || names[0] != "momentum_trix_15" {
			t.Fatalf("大小写不敏感的子串过滤失效：%v", names)
		}
	})

	t.Run("列族拼错必须报错，不能返回空清单", func(t *testing.T) {
		res := callTool(t, tool, `{"kind":"columns","group":"volatilty"}`)
		if res.Success {
			t.Fatal("拼错列族必须报错：返回空列表会被读成『这一族不存在』")
		}
		if !strings.Contains(res.Error, "momentum") {
			t.Errorf("错误应列出合法列族：%q", res.Error)
		}
	})

	t.Run("过滤没命中时明说不是『列不存在』", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","match":"zzz_no_such_column"}`).Output)
		if int(out["matched"].(float64)) != 0 {
			t.Fatalf("matched = %v", out["matched"])
		}
		hint := out["hint"].(string)
		if !strings.Contains(hint, "不代表这些列不存在") {
			t.Errorf("hint 必须澄清这是过滤太窄：%q", hint)
		}
		if !strings.Contains(hint, fmt.Sprintf("%d 列", len(realCatalog))) {
			t.Errorf("hint 应报出真实列数 %d：%q", len(realCatalog), hint)
		}
	})
}

func columnNames(t *testing.T, out map[string]interface{}) []string {
	t.Helper()
	raw, ok := out["columns"].([]interface{})
	if !ok {
		t.Fatalf("输出里没有 columns：%v", out)
	}
	names := make([]string, 0, len(raw))
	for _, r := range raw {
		names = append(names, r.(map[string]interface{})["name"].(string))
	}
	return names
}

// ---------- #3 consumed 过滤 ----------

func TestDiscoverColumnsConsumedFilter(t *testing.T) {
	consumedSet := consumedIndicatorColumns()
	if len(consumedSet) == 0 {
		t.Fatal("嵌入源码里一个已消费列都没扫到 —— consumed 会全量谎报成 false")
	}

	// 已知有现成工具消费的列：indicator/trend_ma.go 与 indicator/momentum_kdj.go
	// 的 SQL 显式 SELECT 了它们。
	for _, col := range []string{"overlap_sma_5", "momentum_kdj_9_3_k"} {
		if !consumedSet[col] {
			t.Errorf("%s 被现成工具 SELECT 过，应判为 consumed", col)
		}
	}
	// 已知没有任何 Go 代码读取的列。
	for _, col := range []string{"volatility_kc_20_2_upper", "momentum_trix_15"} {
		if consumedSet[col] {
			t.Errorf("%s 没有任何工具消费，不应判为 consumed", col)
		}
	}

	srv, _ := fakeIndicatorDB(t, realCatalog, 0, 0)
	tool := newColumnsTool(t, srv.URL)

	t.Run("consumed=yes 只回有现成工具的列", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","consumed":"yes"}`).Output)
		names := columnNames(t, out)
		if len(names) == 0 {
			t.Fatal("consumed=yes 返回空")
		}
		if int(out["matched"].(float64)) != len(names) {
			t.Errorf("matched=%v 但实际返回 %d 列", out["matched"], len(names))
		}
		for _, r := range out["columns"].([]interface{}) {
			c := r.(map[string]interface{})
			if c["consumed"] != true {
				t.Errorf("%v 不该出现在 consumed=yes 的结果里", c["name"])
			}
		}
	})

	t.Run("consumed=no 只回没有任何现成工具的列", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","consumed":"no"}`).Output)
		names := columnNames(t, out)
		if len(names) == 0 {
			t.Fatal("consumed=no 返回空")
		}
		if int(out["matched"].(float64)) != len(names) {
			t.Errorf("matched=%v 但实际返回 %d 列", out["matched"], len(names))
		}
		kcFound := false
		for _, r := range out["columns"].([]interface{}) {
			c := r.(map[string]interface{})
			if c["consumed"] != false {
				t.Errorf("%v 有现成工具，不该出现在 consumed=no 的结果里", c["name"])
			}
			if c["name"] == "volatility_kc_20_2_upper" {
				kcFound = true
			}
		}
		if !kcFound {
			t.Errorf("死列 volatility_kc_20_2_upper 应出现在 consumed=no 里，实际返回：%v", names)
		}
		// yes 与 no 必须互补：同一份清单不能有一列既算有又算没有。
		yes := decodeOutput(t, callTool(t, tool, `{"kind":"columns","consumed":"yes"}`).Output)
		if got, want := int(out["matched"].(float64))+int(yes["matched"].(float64)), len(realCatalog); got != want {
			t.Errorf("consumed=yes(%d) + consumed=no(%d) = %d，应等于全部 %d 列",
				int(yes["matched"].(float64)), int(out["matched"].(float64)), got, want)
		}
	})

	t.Run("consumed 过滤与 group/match 可叠加", func(t *testing.T) {
		out := decodeOutput(t, callTool(t, tool, `{"kind":"columns","group":"overlap","consumed":"yes"}`).Output)
		names := columnNames(t, out)
		if len(names) != 2 {
			t.Fatalf("overlap + consumed=yes = %v，期望 [overlap_sma_5 overlap_sma_20]", names)
		}
	})

	t.Run("consumed 非法取值报错", func(t *testing.T) {
		res := callTool(t, tool, `{"kind":"columns","consumed":"true"}`)
		if res.Success {
			t.Fatal("非法 consumed 应报错")
		}
		if !strings.Contains(res.Error, "any") {
			t.Errorf("错误应列出合法取值：%q", res.Error)
		}
	})
}

// 抽取器本身的单元测试：用合成源码，不依赖其它 worker 正在改的文件。
func TestIndicatorColumnsInSource(t *testing.T) {
	cases := []struct {
		name string
		src  string
		want []string
	}{
		{
			name: "读 v_indicators_daily 的显式列算 consumed，WHERE 里的不算",
			src:  "q := `SELECT date, overlap_sma_5 AS ma5 FROM v_indicators_daily WHERE thscode = ?`",
			// date 命中 SQL 关键字表被剔掉：它是主键列不是指标列，不影响
			// "哪些指标被现成工具消费"这个判断（真实库里唯一与关键字重名的列就是 date）。
			want: []string{"overlap_sma_5"},
		},
		{
			name: "别名只算原列名",
			src:  "`SELECT momentum_rsi_14 AS rsi FROM v_indicators_daily`",
			want: []string{"momentum_rsi_14"},
		},
		{
			name: "读别的视图的列不算",
			src:  "`SELECT date, close FROM v_daily_qfq WHERE thscode = ?`",
			want: nil,
		},
		{
			name: "错位配对抓到的 Go 代码被排除",
			src:  "`json:\"x\"`\nif err := json.Unmarshal([]byte(\"SELECT a FROM b\"), &p); err != nil {\nreturn\n}",
			want: nil,
		},
		{
			name: "JOIN 另一张表也认 v_indicators_daily",
			src:  "`SELECT i.momentum_trix_15 AS trix FROM v_daily_qfq d JOIN v_indicators_daily i ON i.thscode = d.thscode`",
			want: []string{"momentum_trix_15"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := indicatorColumnsInSource(c.src)
			sort.Strings(got)
			want := append([]string{}, c.want...)
			sort.Strings(want)
			if strings.Join(got, ",") != strings.Join(want, ",") {
				t.Fatalf("got %v, want %v", got, want)
			}
		})
	}
}

// embed 必须真的覆盖到工具子包；否则 consumed 会静默全为 false。
func TestEmbeddedToolSourcesCoverSubpackages(t *testing.T) {
	for _, sub := range []string{"analysis", "indicator", "market", "pattern", "query", "special"} {
		entries, err := toolSourceFS.ReadDir(sub)
		if err != nil {
			t.Errorf("嵌入的源码里没有 %s/：%v", sub, err)
			continue
		}
		if len(entries) == 0 {
			t.Errorf("%s/ 嵌进来是空的", sub)
		}
	}
}

// ---------- #4 库不可达时的降级 ----------

func TestDiscoverColumnsDegradesHonestlyWhenDBUnavailable(t *testing.T) {
	cases := []struct {
		name string
		tool *DiscoverTool
	}{
		{
			name: "服务不可达",
			tool: NewDiscoverToolWithConfig(tools.NewToolRegistry(), &Config{
				ServiceURL: "http://127.0.0.1:1", Timeout: time.Second,
			}),
		},
	}
	srv, _ := fakeIndicatorDB(t, realCatalog, 0, http.StatusInternalServerError)
	cases = append(cases, struct {
		name string
		tool *DiscoverTool
	}{"服务返回错误", newColumnsTool(t, srv.URL)})

	// 服务起来后又挂掉：关掉再调，模拟运行中掉线。
	deadSrv, _ := fakeIndicatorDB(t, realCatalog, 0, 0)
	dead := newColumnsTool(t, deadSrv.URL)
	deadSrv.Close()
	cases = append(cases, struct {
		name string
		tool *DiscoverTool
	}{"运行中掉线", dead})

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			res := callTool(t, c.tool, `{"kind":"columns"}`)
			if res.Success {
				t.Fatal("库不可达时不得报成功")
			}
			if res.Output != "" {
				t.Fatalf("库不可达时不得输出任何清单（空清单会被读成『这些列不存在』）：%q", res.Output)
			}
			if res.Error == "" {
				t.Fatal("必须给出错误说明，而不是静默返回空")
			}
			// 错误必须可执行：点名视图、点名服务地址、给出排查项。
			for _, want := range []string{"v_indicators_daily", "python-service", "PYTHON_SERVICE_URL"} {
				if !strings.Contains(res.Error, want) {
					t.Errorf("错误缺少 %q：%s", want, res.Error)
				}
			}
			if !strings.Contains(res.Error, "不会") {
				t.Errorf("错误应说明为什么不返回空清单：%s", res.Error)
			}
		})
	}
}

// 库返回的行数比它自己声明的少 = 清单被截断。宁可报错也不给半份。
func TestDiscoverColumnsRejectsTruncatedCatalog(t *testing.T) {
	srv, _ := fakeIndicatorDB(t, realCatalog, len(realCatalog)*3, 0)
	res := callTool(t, newColumnsTool(t, srv.URL), `{"kind":"columns"}`)
	if res.Success {
		t.Fatal("清单被截断时不得报成功，否则模型会以为那就是全部列")
	}
	if !strings.Contains(res.Error, "截断") {
		t.Errorf("错误应说明截断：%s", res.Error)
	}
	if res.Output != "" {
		t.Errorf("截断时不得输出清单：%q", res.Output)
	}
}

// view 存在但 information_schema 查不到列：同样是"不能装作什么都没有"。
func TestDiscoverColumnsRejectsEmptyCatalog(t *testing.T) {
	srv, _ := fakeIndicatorDB(t, nil, 0, 0)
	res := callTool(t, newColumnsTool(t, srv.URL), `{"kind":"columns"}`)
	if res.Success {
		t.Fatal("0 列不得报成功")
	}
	if !strings.Contains(res.Error, "v_indicators_daily") {
		t.Errorf("错误应点名视图：%s", res.Error)
	}
}

// columns 模式不该在工具注册表为空时依赖它（Z 哥未启用 hithink 工具时也要能用）。
func TestDiscoverColumnsDoesNotNeedRegisteredTools(t *testing.T) {
	srv, _ := fakeIndicatorDB(t, realCatalog, 0, 0)
	if res := callTool(t, newColumnsTool(t, srv.URL), `{"kind":"columns"}`); !res.Success {
		t.Fatalf("空注册表下 columns 模式应可用：%s", res.Error)
	}
}

// ---------- 参数与描述的自我约束 ----------

func TestDiscoverParametersAdvertiseColumnsMode(t *testing.T) {
	schema := decodeOutput(t, string(NewDiscoverTool(tools.NewToolRegistry()).Parameters()))
	props := schema["properties"].(map[string]interface{})

	kind := props["kind"].(map[string]interface{})
	if kind["default"] != "tools" {
		t.Errorf("kind 默认值 = %v，期望 tools（省略 kind 必须等价于旧行为）", kind["default"])
	}
	enum := fmt.Sprint(kind["enum"])
	if !strings.Contains(enum, "columns") || !strings.Contains(enum, "tools") {
		t.Errorf("kind 枚举 = %s", enum)
	}
	consumed := props["consumed"].(map[string]interface{})
	if fmt.Sprint(consumed["default"]) != "any" {
		t.Errorf("consumed 默认值 = %v", consumed["default"])
	}
	if !strings.Contains(fmt.Sprint(consumed["enum"]), "yes") {
		t.Errorf("consumed 枚举应含 yes/no：%v", consumed["enum"])
	}
	// 旧参数必须仍在。
	for _, k := range []string{"prefix", "depth"} {
		if _, ok := props[k]; !ok {
			t.Errorf("参数 %s 消失了，会破坏既有调用", k)
		}
	}
	if len(schema["required"].([]interface{})) != 0 {
		t.Error("required 应保持为空：列清单不能被参数强制")
	}
}

func TestDiscoverDescriptionIsHonestAboutScope(t *testing.T) {
	desc := NewDiscoverTool(tools.NewToolRegistry()).Description()
	// 描述里不能写死列数——写死就会漂移，那正是本工具要消灭的东西。
	for _, n := range []string{"231", "54 ", "231 个"} {
		if strings.Contains(desc, n) {
			t.Errorf("描述里写死了 %q，列数会随库变化而漂移", n)
		}
	}
	// 边界必须写明：不回答"该用哪个 tool"（那是系统提示词里路由表的事）。
	if !strings.Contains(desc, "不回答") || !strings.Contains(desc, "路由表") {
		t.Errorf("描述应写明本工具不做语义路由：%s", desc)
	}
	// 也不能出现"我帮你挑一个"这种越界承诺。
	for _, claim := range []string{"帮你选择", "为你推荐", "自动匹配到最合适"} {
		if strings.Contains(desc, claim) {
			t.Errorf("描述越界：包含 %q——本工具只回答『有什么』", claim)
		}
	}
	// consumed 的判据必须写清楚，否则模型会把它当成"重要程度"标签。
	for _, want := range []string{"SELECT", "consumed"} {
		if !strings.Contains(desc, want) {
			t.Errorf("描述应说明 consumed 的判据（含 %q）：%s", want, desc)
		}
	}
	if !strings.Contains(desc, "query.sql") {
		t.Error("描述应把取数职责指向 hithink.finance.query.sql")
	}
}

// ---------- 真实库验收（需显式开启） ----------
//
//	WEKNORA_LIVE_DUCKDB_URL=http://localhost:50052 go test ./internal/agent/tools/hithink_finance/ -run Live
func TestDiscoverColumnsAgainstLiveDuckDB(t *testing.T) {
	url := os.Getenv("WEKNORA_LIVE_DUCKDB_URL")
	if url == "" {
		t.Skip("设置 WEKNORA_LIVE_DUCKDB_URL 后才跑真实库验收")
	}
	tool := NewDiscoverToolWithConfig(tools.NewToolRegistry(), &Config{ServiceURL: url, Timeout: 30 * time.Second})

	out := decodeOutput(t, callTool(t, tool, `{"kind":"columns"}`).Output)

	// 独立地从库里再数一遍，两边必须一致：这一列数不能来自任何内置副本。
	rows, err := QueryDuckDBParams(context.Background(), tool.config, indicatorDB, indicatorColumnsSQL, indicatorView)
	if err != nil {
		t.Fatalf("直连查询失败：%v", err)
	}
	if got, want := int(out["total_columns"].(float64)), len(rows); got != want {
		t.Fatalf("工具报 %d 列，库实际返回 %d 列", got, want)
	}
	if len(rows) < 200 {
		t.Fatalf("真实库只有 %d 列，与已知规模（200+）不符，验收前提不成立", len(rows))
	}
	consumed := int(out["consumed_total"].(float64))
	if consumed == 0 || consumed >= len(rows) {
		t.Fatalf("consumed=%d 不合理（共 %d 列）", consumed, len(rows))
	}

	// 端到端目标场景："有没有 TRIX 读数？"
	trix := decodeOutput(t, callTool(t, tool, `{"kind":"columns","match":"trix"}`).Output)
	if int(trix["matched"].(float64)) < 1 {
		t.Fatalf("库里存在 TRIX 列但 discover(match=trix) 没找到：%v", trix)
	}
	found := false
	for _, r := range trix["columns"].([]interface{}) {
		if r.(map[string]interface{})["name"] == "momentum_trix_15" {
			found = true
		}
	}
	if !found {
		t.Errorf("discover(match=trix) 没有返回 momentum_trix_15：%v", trix)
	}
	t.Logf("真实库 %d 列，其中 %d 列有现成工具消费", len(rows), consumed)
}

// TestDefaultCallMustNotReturnEmpty 是本文件最该存在的一个测试。
//
// discover 的默认调用（不传 prefix、不传 depth）过去返回 **0 个工具**：depth 默认 1，
// 而真实工具名全是三段，一个都匹配不上。模型于是拿到一份空清单，并把它读成
// "没有别的金融工具了"——一个什么都没说的系统，骗模型以为自己知道了。
//
// 空清单是这个工具最危险的一种输出：它不是错误，没有日志，不触发任何告警。所以这里
// 钉的不是"某个值等于某数"，而是一条性质——**默认调用必须列出本族工具**。
//
// 真实工具名三段是既有事实（market.price.snapshot / indicator.trend.ma /
// special.limit.limit_up_pool），所以这个守卫在有人把默认深度改小、或新增一个更深
// 的工具名时都会红。
func TestDefaultCallMustNotReturnEmpty(t *testing.T) {
	reg := tools.NewToolRegistry()
	// 按真实命名习惯注册：全族都是三段。
	for _, name := range []string{
		"hithink.finance.market.price.snapshot",
		"hithink.finance.market.price.historical",
		"hithink.finance.indicator.trend.ma",
		"hithink.finance.special.limit.limit_up_pool",
		"hithink.finance.financial.statement.income",
	} {
		reg.RegisterTool(fakeTool{name, "测试用"})
	}
	// 故意放一个不属于本族的，确认过滤仍然生效——本测试要证明的是"非空"，
	// 不是"全放行"。
	reg.RegisterTool(fakeTool{"other.tool", "别的系统"})

	tool := NewDiscoverToolWithConfig(reg, &Config{})
	got := decodeOutput(t, callTool(t, tool, `{}`).Output)

	tools2, _ := got["tools"].([]interface{})
	if len(tools2) == 0 {
		t.Fatalf("默认调用返回了 0 个工具。depth 的默认值(%d)覆盖不到三段的真实工具名。\n"+
			"空清单会被模型读成「没有别的工具」——这是静默失效，不是「暂时没数据」。",
			defaultToolDepth)
	}
	if len(tools2) != 5 {
		t.Errorf("默认调用返回 %d 个工具，期望 5 个本族工具（other.tool 必须被前缀过滤掉）", len(tools2))
	}
}

// TestOmittedDepthEqualsExplicitDefault 钉住"省略 depth"与"显式写 depth=默认值"
// 必须等价。两者曾经分道扬镳（省略→1、显式→调用者自选），这正是 bug 的形状。
func TestOmittedDepthEqualsExplicitDefault(t *testing.T) {
	reg := tools.NewToolRegistry()
	reg.RegisterTool(fakeTool{"hithink.finance.market.price.snapshot", "行情快照"})

	tool := NewDiscoverToolWithConfig(reg, &Config{})
	omitted := decodeOutput(t, callTool(t, tool, `{}`).Output)
	explicit := decodeOutput(t, callTool(t, tool,
		`{"depth":`+strconv.Itoa(defaultToolDepth)+`}`).Output)

	if len(omitted["tools"].([]interface{})) != len(explicit["tools"].([]interface{})) {
		t.Errorf("省略 depth 返回 %d 个，显式 depth=%d 返回 %d 个，两者应等价",
			len(omitted["tools"].([]interface{})), defaultToolDepth,
			len(explicit["tools"].([]interface{})))
	}
}
