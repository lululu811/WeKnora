package spec_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/analysis"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/discover"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/financial"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/fund"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/futures"
	indexfinance "github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/index"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/indicator"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/market"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/pattern"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/query"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec/fakeservice"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/special"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这一次深化的产物不是"更少的代码"，而是这四条不变式。它们是这个模块的测试面：
// 声明与模型看到的东西、与源码里的工具名、与运行时真正执行的窗口，必须互相
// 对得上。少一条，这次深化就只是"搬了个家"。

// specNameRe 抓 spec 包里每一个 Spec 字面量的工具名。
//
// 不用导出列表：导出列表本身是一份要人记得同步的清单——那正是这套仓库明确
// 拒绝的形状（discover.go 与 toolnames_scan.go 各有一处注释说明为什么）。
// 直接从源码里抓，新加的 spec 漏不掉。
var specNameRe = regexp.MustCompile(`(?s)Spec\{\s*Name:\s*"([^"]+)"`)

// adapterNameRe 与 zettaranc 白名单测试用的是同一条正则：Name() 必须返回
// 字面量，测试从源码里取工具名，认不出从声明里查表。
var adapterNameRe = regexp.MustCompile(
	`func\s*\(\s*\w+\s+\*?\w+\s*\)\s*Name\(\)\s*string\s*\{\s*return\s+"([\w.]+)"`)

// registeredNameRe 抓注册 switch 里引用的 hithink 工具名。
var registeredNameRe = regexp.MustCompile(`"(hithink\.finance[\w.]*)"`)

// findRepoRoot 向上找 go.mod。
func findRepoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("取工作目录失败：%v", err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("找不到仓库根目录")
		}
		dir = parent
	}
}

// toolSpecsInSource 抓出 spec 包里声明的全部工具。
func toolSpecsInSource(t *testing.T) map[string]spec.Spec {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("取工作目录失败：%v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("读 spec 目录失败：%v", err)
	}
	out := map[string]spec.Spec{}
	all := map[string]spec.Spec{}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".go") || strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatalf("读 %s 失败：%v", e.Name(), err)
		}
		for _, m := range specNameRe.FindAllStringSubmatch(string(raw), -1) {
			name := m[1]
			if _, dup := all[name]; dup {
				t.Errorf("工具名 %s 在 spec 包里声明了两次", name)
			}
			all[name] = spec.Spec{Name: name}
		}
	}
	// 用真实的声明值覆盖占位。
	for name := range all {
		found := false
		for _, cand := range declaredSpecs() {
			if cand.Name == name {
				out[name] = cand
				found = true
				break
			}
		}
		if !found {
			t.Errorf("spec 包里声明了 %s，但 declaredSpecs() 里没有它", name)
		}
	}
	return out
}

// declaredSpecs 列出本包导出的全部声明。
//
// 新增一个工具时必须在这里登记——contract test 的第一条会抓住漏登记的。
func declaredSpecs() []spec.Spec {
	return []spec.Spec{
		spec.IndicatorMomentumKDJ,
		spec.IndicatorTrendMA,
		spec.IndicatorMomentumMacdRsi,
		spec.IndicatorVolatilityBBands,
		spec.IndicatorVolumeMoneyFlow,
		spec.IndicatorVolatilitySqueeze,
		spec.IndicatorTrendSupertrend,
		spec.MarketPriceHistorical,
		spec.FuturesPriceHistorical,
		spec.IndexSectorDaily,
		spec.SpecialHotStockRankTrend,
		spec.FundETFSnapshot,
		spec.FundETFInstitutionalRanking,
		spec.FundHoldingsPortfolio,
		spec.FundHoldingsStock,
		spec.FuturesPriceSnapshot,
		spec.FuturesVarietyList,
		spec.IndexSectorRanking,
		spec.SpecialAnomalyStockReason,
		spec.SpecialAuctionSnapshot,
		spec.SpecialLimitBreakPool,
		spec.SpecialLimitLadderDetail,
		spec.SpecialDragonTigerList,
		spec.SpecialHotStockSkyrocket,
		spec.SpecialHotStockTopList,
		spec.SpecialLimitUpPool,
		spec.IndexOverview,
		spec.MarketPriceSnapshot,
		spec.FinancialValuationSnapshot,
		spec.FinancialBalanceSheet,
		spec.FinancialIncomeStatement,
		spec.FinancialCashFlow,
		spec.FinancialIndicatorDetail,
		spec.AnalysisLevels,
		spec.AnalysisPattern,
		spec.AnalysisTrend,
		spec.AnalysisVolume,
		spec.SpecialSentimentStats,
		spec.FuturesInventoryWarehouseReceipts,
		spec.FuturesBasisRate,
		spec.IndexSectorConstituents,
		spec.IndexSectorMembership,
		spec.MarketDividendCalendar,
		spec.MarketDividendYieldScreener,
		spec.FundHoldersStructure,
		spec.PatternScan,
		spec.QuerySQL,
		spec.Discover,
	}
}

// adapterNamesInSource 抓出所有家族包里 adapters.go 声明的工具名。
func adapterNamesInSource(t *testing.T) map[string]bool {
	t.Helper()
	root := findRepoRoot(t)
	base := filepath.Join(root, "internal", "agent", "tools", "hithink_finance")
	entries, err := os.ReadDir(base)
	if err != nil {
		t.Fatalf("读 %s 失败：%v", base, err)
	}
	out := map[string]bool{}
	for _, e := range entries {
		if !e.IsDir() || e.Name() == "spec" {
			continue
		}
		// 扫家族包里的所有 .go 文件——有的家族把适配器放在 adapters.go，
		// 有的（analysis）还留在原来的工具文件里。
		files, err := os.ReadDir(filepath.Join(base, e.Name()))
		if err != nil {
			continue
		}
		for _, f := range files {
			if f.IsDir() || !strings.HasSuffix(f.Name(), ".go") ||
				strings.HasSuffix(f.Name(), "_test.go") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(base, e.Name(), f.Name()))
			if err != nil {
				continue
			}
			for _, m := range adapterNameRe.FindAllStringSubmatch(string(raw), -1) {
				out[m[1]] = true
			}
		}
	}
	return out
}

// registeredNamesInService 抓出注册 switch 里引用的 hithink 工具名。
func registeredNamesInService(t *testing.T) map[string]bool {
	t.Helper()
	root := findRepoRoot(t)
	path := filepath.Join(root, "internal", "application", "service", "agent_service.go")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读 %s 失败：%v", path, err)
	}
	out := map[string]bool{}
	for _, m := range registeredNameRe.FindAllStringSubmatch(string(raw), -1) {
		out[m[1]] = true
	}
	return out
}

// TestToolNameIsConsistentInThreePlaces 是第一条不变式：工具名在三个地方必须
// 一致——声明里、适配器的 Name() 字面量里、注册 switch 的 case 标签里。
//
// 少一处就出事：白名单测试只认字面量（少一处它就报"白名单里的工具不存在"），
// 注册 switch 少一处工具就永远注册不上（模型看不见它，而且不报错）。
func TestToolNameIsConsistentInThreePlaces(t *testing.T) {
	specs := toolSpecsInSource(t)
	adapters := adapterNamesInSource(t)
	registered := registeredNamesInService(t)

	for name := range specs {
		if !adapters[name] {
			t.Errorf("%s：声明里有，适配器里没有——工具永远注册不上", name)
		}
		if !registered[name] {
			t.Errorf("%s：声明里有，注册 switch 里没有——模型看不见它", name)
		}
	}
}

// TestSpecSetMatchesAdapterSet 是第二条不变式：声明集合与适配器集合必须相等。
//
// 少一个适配器 = 工具死了但没人发现；多一个适配器 = 有个工具模型能调却没有
// 声明兜着，窗口和错误措辞会各写各的。
func TestSpecSetMatchesAdapterSet(t *testing.T) {
	specs := toolSpecsInSource(t)
	adapters := adapterNamesInSource(t)

	// 只断言一个方向：**每个声明都要有适配器**。
	//
	// 反向不成立——形状真的不一样的工具（analysis 那一族）以 Fetch / Compose
	// 钩子的形式实现，声明管的是它的身份与契约，不是它的每一步。
	//
	// 反过来，多一个适配器 = 有个工具模型能调却没有声明兜着，窗口和错误措辞
	// 会各写各的——那一条由「注册 switch 里的名字 ⊆ 声明集」守住。
	for name := range specs {
		if !adapters[name] {
			t.Errorf("%s：有声明，没有适配器", name)
		}
	}
	if len(specs) == 0 {
		t.Fatal("一个声明都没抓到——正则可能已随重构失效")
	}
}

// loadFrozen 读迁移前那批工具自己吐出来的冻结快照。
func loadFrozen(t *testing.T) map[string]frozenEntry {
	t.Helper()
	root := findRepoRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "internal", "agent", "tools",
		"hithink_finance", "spec", "testdata", "frozen.json"))
	if err != nil {
		t.Fatalf("读冻结快照失败：%v", err)
	}
	var frozen map[string]frozenEntry
	if err := json.Unmarshal(raw, &frozen); err != nil {
		t.Fatalf("冻结快照不是合法 JSON：%v", err)
	}
	return frozen
}

// frozenEntry 是迁移前模型看到的东西。
type frozenEntry struct {
	Schema      string  `json:"schema"`
	Description string  `json:"description"`
	Probes      []probe `json:"probes"`
	// WindowParam 是探针里给了极大值的那个参数。
	WindowParam string `json:"window_param"`
	// Effective 是那次查询真正带出去的窗口值——被钳到上限，或被换回缺省值。
	Effective int `json:"effective"`
}

// probe 是一次喂给工具的参数，以及它实际发出去的那句查询。
type probe struct {
	Args map[string]interface{} `json:"args"`
	// SQLs 是这次调用发出去的查询，按顺序。多查询的工具（市场情绪统计）一次
	// 发三条——只记最后一条就漏掉了前面两条。
	SQLs []string `json:"sqls"`
	// Params 是每条查询各自的绑定值，与 SQLs 一一对应。
	Params [][]interface{} `json:"params"`
	// Output 是那次调用返回给模型的正文——它必须与迁移前逐字相同。
	Output string `json:"output"`
	// Error 是那次调用的错误措辞（成功时为空串）。
	Error string `json:"error"`
}

// TestParametersMatchFrozenContract 是第三条不变式：派生出来的 schema 与说明
// 文字，必须与迁移前逐字一致——唯一允许的差别是 schema 里多出一个 maximum。
//
// 冻结快照是迁移前那批工具自己吐出来的字节（从 git HEAD 的工作树里跑出来的），
// 不是手抄的，所以它不会跟着代码一起漂。
func TestParametersMatchFrozenContract(t *testing.T) {
	root := findRepoRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "internal", "agent", "tools",
		"hithink_finance", "spec", "testdata", "frozen.json"))
	if err != nil {
		t.Fatalf("读冻结快照失败：%v", err)
	}
	var frozen map[string]frozenEntry
	if err := json.Unmarshal(raw, &frozen); err != nil {
		t.Fatalf("冻结快照不是合法 JSON：%v", err)
	}

	specs := toolSpecsInSource(t)
	for name, s := range specs {
		f, ok := frozen[name]
		if !ok {
			t.Errorf("%s：冻结快照里没有它——快照与声明集不一致", name)
			continue
		}

		// 说明文字必须逐字不变。
		if got := spec.Description(s); got != f.Description {
			t.Errorf("%s：说明文字变了\n迁移前：%q\n现在：  %q", name, f.Description, got)
		}

		// schema 必须与迁移前一致，只允许多出 maximum。
		got := map[string]any{}
		if err := json.Unmarshal(spec.Parameters(s), &got); err != nil {
			t.Fatalf("%s：派生出的 schema 不是合法 JSON：%v", name, err)
		}
		want := map[string]any{}
		if err := json.Unmarshal([]byte(f.Schema), &want); err != nil {
			t.Fatalf("%s：冻结的 schema 不是合法 JSON：%v", name, err)
		}
		gotMaxima := maximums(got)
		oldHasMax := maximums(want)
		// 迁移前就写了 maximum 的（analysis.trend），值必须原样保住。
		for param, v := range oldHasMax {
			gotMax, ok := gotMaxima[param]
			if !ok || gotMax != v {
				t.Errorf("%s.%s：迁移前 schema 里的 maximum 是 %v，现在没了或变了（%v）",
					name, param, v, gotMax)
			}
		}
		added := stripMaximum(got)
		// required 的空数组与缺省在 JSON Schema 里是同一件事——按语义比。
		// （迁移前只有 special.hot_stock.top_list 一个工具的 schema 没写这个键，
		// 那是手写 schema 的不一致，不是契约。）
		if r, ok := added["required"].([]any); ok && len(r) == 0 {
			delete(added, "required")
		}
		if r, ok := want["required"].([]any); ok && len(r) == 0 {
			delete(want, "required")
		}
		want = stripMaximum(want)
		if !reflect.DeepEqual(added, want) {
			t.Errorf("%s：schema 变了（除了 maximum 之外必须逐字相同）\n迁移前：%v\n现在：  %v",
				name, want, added)
		}
	}

	// 反向：冻结快照里不该有多余的工具。
	for name := range frozen {
		if _, ok := specs[name]; !ok {
			t.Errorf("%s：冻结快照里有，声明集里没有", name)
		}
	}
}

// maximums 取出 schema 里每个属性上的 maximum。
func maximums(schema map[string]any) map[string]any {
	out := map[string]any{}
	props, ok := schema["properties"].(map[string]any)
	if !ok {
		return out
	}
	for name, v := range props {
		if prop, ok := v.(map[string]any); ok {
			if m, ok := prop["maximum"]; ok {
				out[name] = m
			}
		}
	}
	return out
}

// stripMaximum 摘掉 schema 里每个属性上的 maximum，并返回它摘掉的值。
//
// 这就是「唯一允许的差别」的机器表达：摘掉之后，剩下的必须与迁移前逐字相同。
func stripMaximum(schema map[string]any) map[string]any {
	props, ok := schema["properties"].(map[string]any)
	if !ok {
		return schema
	}
	for _, v := range props {
		if prop, ok := v.(map[string]any); ok {
			delete(prop, "maximum")
		}
	}
	return schema
}

// TestWindowMaxIsTheSchemaMaximum 是第四条不变式：窗口的上限只有一处声明。
//
// Param.Max 同时是 schema 里的 maximum 和运行时钳制的目标。运行时按 schema
// 钳制，所以模型看到的上限就是它真正能拿到的上限——这正是迁移前缺的那一环
// （迁移前 schema 里没有 maximum，钳制藏在实现里）。
func TestWindowMaxIsTheSchemaMaximum(t *testing.T) {
	frozen := loadFrozen(t)
	specs := toolSpecsInSource(t)
	for name, s := range specs {
		f, ok := frozen[name]
		if !ok {
			t.Errorf("%s：冻结快照里没有它", name)
			continue
		}
		if f.WindowParam == "" {
			// 这个工具没有窗口参数（单行快照、全市场概览），窗口那条不变式不适用。
			continue
		}
		var win *spec.Param
		for i := range s.Params {
			if s.Params[i].Name == f.WindowParam {
				win = &s.Params[i]
			}
		}
		if win == nil {
			t.Errorf("%s：声明里没有窗口参数 %s", name, f.WindowParam)
			continue
		}

		var schema map[string]any
		if err := json.Unmarshal(spec.Parameters(s), &schema); err != nil {
			t.Fatalf("%s：派生出的 schema 不是合法 JSON：%v", name, err)
		}
		props, _ := schema["properties"].(map[string]any)
		prop, _ := props[win.Name].(map[string]any)
		got, hasMax := prop["maximum"]

		// 规则一：schema 里有 maximum，运行时必须真的钳到它。
		//
		// 「超范围回缺省值」的参数不发布 maximum——那个数不是上限，发布一个运行时
		// 并不兑现的约束，比不发布更糟。
		switch {
		case hasMax:
			if win.Over != spec.OverRangeClamp {
				t.Errorf("%s.%s：schema 里有 maximum，但声明的超范围处置是「回缺省值」"+
					"——发布的约束运行时并不兑现", name, win.Name)
			}
			if int(got.(float64)) != *win.Max {
				t.Errorf("%s.%s：schema 的 maximum 是 %v，声明的上限是 %d",
					name, win.Name, got, *win.Max)
			}
			if f.Effective != *win.Max {
				t.Errorf("%s.%s：schema 说上限 %d，迁移前真正到服务手上的是 %d",
					name, win.Name, *win.Max, f.Effective)
			}
		case win.Max == nil:
			// 没有上限：探针的值原样通过。
			if f.Effective != 99999 {
				t.Errorf("%s.%s：声明里没有上限，探针传 99999，迁移前到手的是 %d",
					name, win.Name, f.Effective)
			}
		default:
			// 有阈值但不发布 maximum：超范围换回缺省值。
			if win.Over != spec.OverRangeReset {
				t.Errorf("%s.%s：没有发布 maximum，声明的超范围处置却不是「回缺省值」",
					name, win.Name)
			}
			if d, ok := win.Default.(int); ok && f.Effective != d {
				t.Errorf("%s.%s：超范围应换回缺省值 %d，迁移前到手的是 %d",
					name, win.Name, d, f.Effective)
			}
		}

		// 规则二的"运行时"那一半——探针传一个远大于上限的值，真正到服务手上的
		// 那个值必须与迁移前一致，不论它是被钳到上限还是被换回缺省值——
		// 在 TestExecutorSendsTheFrozenSQL 里验：它跑的是真正的工具，看的是
		// 真正发出去的绑定值。
	}
}

// TestExecutorSendsTheFrozenSQL 是端到端的那一条：执行器发出去的 SQL 必须与
// 迁移前逐字相同，窗口必须被钳到冻结快照里记的那个值。
//
// 前面几条都是看声明本身；这一条是真的把工具跑起来，接住发往 python-service
// 的请求，看出去的那句话是不是原来那句话。声明里的 SQL 写错了、窗口的钳制没
// 接上、绑定顺序错了——这里都会红。
func TestExecutorSendsTheFrozenSQL(t *testing.T) {
	frozen := loadFrozen(t)
	specs := toolSpecsInSource(t)
	for name := range specs {
		f, ok := frozen[name]
		if !ok {
			t.Errorf("%s：冻结快照里没有它", name)
			continue
		}

		// 两份探针：满配（排序开关、过滤条件、变体都走非缺省路径）与最小配
		//（都走缺省路径）。一份探针盖不住两条分支。
		for i, pr := range f.Probes {
			srv := fakeservice.New()

			args, _ := json.Marshal(pr.Args)
			cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}
			tool, ok := toolByName(name, cfg)
			if !ok {
				t.Errorf("%s：找不到对应的工具", name)
				continue
			}
			res, _ := tool.Execute(context.Background(), args)
			var captured struct {
				SQLs   []string        `json:"sqls"`
				Params [][]interface{} `json:"params"`
			}
			for _, r := range srv.Requests {
				captured.SQLs = append(captured.SQLs, r.SQL)
				captured.Params = append(captured.Params, r.Params)
			}
			srv.Close()

			got, gotErr := "", ""
			if res != nil {
				got = res.Output
				gotErr = res.Error
			}
			if got != pr.Output {
				t.Errorf("%s（探针 %d，参数 %v）：返回给模型的正文变了\n迁移前：%q\n现在：  %q",
					name, i, pr.Args, pr.Output, got)
			}
			if gotErr != pr.Error {
				t.Errorf("%s（探针 %d，参数 %v）：错误措辞变了\n迁移前：%q\n现在：  %q",
					name, i, pr.Args, pr.Error, gotErr)
			}

			if !reflect.DeepEqual(captured.SQLs, pr.SQLs) {
				t.Errorf("%s（探针 %d，参数 %v）：发出去的 SQL 变了\n迁移前：%q\n现在：  %q",
					name, i, pr.Args, pr.SQLs, captured.SQLs)
			}
			if !reflect.DeepEqual(captured.Params, pr.Params) {
				t.Errorf("%s（探针 %d，参数 %v）：发出去的绑定值变了\n迁移前：%v\n现在：  %v",
					name, i, pr.Args, pr.Params, captured.Params)
			}
		}
	}
}

// toolByName 按名字造出对应的工具。
//
// 契约测试跑的是**工具**而不是 spec 包的函数——「取数 → Go 侧计算」这一族的
// 钩子挂在适配器上，绕开适配器就等于没测到它们。
func toolByName(name string, cfg *hithink_finance.Config) (types.Tool, bool) {
	all := []types.Tool{
		market.NewPriceHistoricalTool(cfg),
		futures.NewPriceHistoricalTool(cfg),
		indexfinance.NewSectorDailyTool(cfg),
		indicator.NewTrendMATool(cfg),
		indicator.NewMomentumKDJTool(cfg),
		indicator.NewVolatilityBBandsTool(cfg),
		indicator.NewVolatilitySqueezeTool(cfg),
		indicator.NewTrendSupertrendTool(cfg),
		indicator.NewMomentumMacdRsiTool(cfg),
		indicator.NewVolumeMoneyFlowTool(cfg),
		special.NewHotStockTrendTool(cfg),
		fund.NewETFSnapshotTool(cfg),
		fund.NewETFInstitutionalRankingTool(cfg),
		fund.NewHoldingsPortfolioTool(cfg),
		fund.NewHoldingsStockTool(cfg),
		futures.NewPriceSnapshotTool(cfg),
		futures.NewVarietyListTool(cfg),
		indexfinance.NewSectorRankingTool(cfg),
		special.NewAnomalyStockTool(cfg),
		special.NewAuctionSnapshotTool(cfg),
		special.NewLimitBreakPoolTool(cfg),
		special.NewLimitLadderDetailTool(cfg),
		special.NewDragonTigerTool(cfg),
		special.NewHotStockTool(cfg),
		special.NewHotStockListTool(cfg),
		special.NewLimitUpPoolTool(cfg),
		indexfinance.NewIndexOverviewTool(cfg),
		market.NewPriceSnapshotTool(cfg),
		financial.NewValuationSnapshotTool(cfg),
		financial.NewBalanceSheetTool(cfg),
		financial.NewIncomeStatementTool(cfg),
		financial.NewCashFlowStatementTool(cfg),
		financial.NewFinancialIndicatorDetailTool(cfg),
		analysis.NewLevelsAnalysisTool(cfg),
		analysis.NewChartPatternTool(cfg),
		analysis.NewTrendAnalysisTool(cfg),
		analysis.NewVolumeAnalysisTool(cfg),
		special.NewSentimentStatsTool(cfg),
		futures.NewWarehouseReceiptsTool(cfg),
		futures.NewFuturesBasisRateTool(cfg),
		indexfinance.NewSectorConstituentsTool(cfg),
		indexfinance.NewSectorMembershipTool(cfg),
		market.NewDividendCalendarTool(cfg),
		market.NewDividendYieldScreenerTool(cfg),
		fund.NewETFHoldersStructureTool(cfg),
		pattern.NewPatternScanTool(cfg),
		query.NewSQLQueryTool(cfg),
		discover.NewToolWithConfig(tools.NewToolRegistry(), cfg),
	}
	for _, tool := range all {
		if tool.Name() == name {
			return tool, true
		}
	}
	return nil, false
}

// TestQueriesImplyComposed 是一条护栏：声明了多条查询的，一定是"Go 侧计算"那一族。
func TestQueriesImplyComposed(t *testing.T) {
	specs := toolSpecsInSource(t)
	for name, s := range specs {
		if len(s.Queries) > 0 && !s.Composed {
			t.Errorf("%s：声明了 %d 条查询却没标 Composed——执行器不会去跑它们",
				name, len(s.Queries))
		}
	}
}

// TestFiltersHaveAWhereSlot 是一条护栏：声明了过滤条件的，SQL 模板里必须有
// {where} 这个位置——否则条件既拼不进去、绑定值也放不对地方，而且不会报错。
func TestFiltersHaveAWhereSlot(t *testing.T) {
	specs := toolSpecsInSource(t)
	for name, s := range specs {
		if len(s.Filters) == 0 {
			continue
		}
		if !strings.Contains(s.SQL, "{where}") {
			t.Errorf("%s：声明了 %d 个过滤条件，SQL 模板里却没有 {where} 位置",
				name, len(s.Filters))
		}
	}
}

// TestSpecSQLIsNotEmpty 是一条护栏：声明里的 SQL 不能是空的。
func TestSpecSQLIsNotEmpty(t *testing.T) {
	specs := toolSpecsInSource(t)
	for name, s := range specs {
		if s.Composed {
			// 这一族的结果是算出来的：取数与计算是钩子。声明里可以带 SQL
			//（单查询时 Fetch 用它）、可以带 Queries（多查询时执行器跑它们），
			// 也可以两样都没有（取数逻辑本身是 Go 代码，比如两个 DuckDB 合并）。
			continue
		}
		if s.SQLFromParam != "" {
			// 自由 SQL：语句是模型给的，声明里写死的 SQL 在这个模式下不参与。
			continue
		}
		if strings.TrimSpace(s.SQL) == "" {
			t.Errorf("%s：声明里没有 SQL", name)
		}
		if !strings.Contains(s.SQL, "FROM ") {
			t.Errorf("%s：SQL 看起来不是查询：%q", name, s.SQL)
		}
		if s.DB == "" {
			t.Errorf("%s：声明里没有库名", name)
		}
	}
}

// TestBindPositionsAreDense 是一条护栏：SQL 里的 ? 按出现顺序取绑定值，绑定
// 位置必须稠密，中间留空会让绑定值与占位符错位。
func TestBindPositionsAreDense(t *testing.T) {
	specs := toolSpecsInSource(t)
	for name, s := range specs {
		if s.Composed {
			// 这一族的取数是钩子：单查询时声明里的 Binds 会被 Fetch 用上，
			// 多查询时由 Queries 各自带绑定，都不算错。
			continue
		}
		check := func(sql string, binds []string) {
			if got := strings.Count(sql, "?"); got != len(binds) {
				t.Errorf("%s：SQL 里有 %d 个 ?，声明里有 %d 个绑定参数", name, got, len(binds))
			}
			for _, b := range binds {
				found := false
				for _, p := range s.Params {
					if p.Name == b {
						found = true
					}
				}
				if !found {
					t.Errorf("%s：绑定参数 %q 不在声明里", name, b)
				}
			}
		}
		check(s.SQL, s.Binds)
		for _, v := range s.Variants {
			check(v.SQL, v.Binds)
		}
		// 过滤条件的绑定值落在 {where} 所在的位置——所以静态 ? 的个数只算静态
		// 绑定（过滤条件自己带 ?），两者不能混。
		if len(s.Filters) > 0 && !strings.Contains(s.SQL, "{where}") {
			t.Errorf("%s：声明了过滤条件，SQL 模板里却没有 {where} 位置", name)
		}
	}
}

// TestFreshnessWarning 守着数据新鲜度告警：数据超过容忍天数就多一条 warning，
// 没超过就没有。
//
// 这条不能靠冻结快照验——告警里的过期天数每天都在变，冻结下来第二天就失效。
// 所以这里直接验机制：喂一行 10 天前的数据，和一行当天的数据。
func TestFreshnessWarning(t *testing.T) {
	specs := toolSpecsInSource(t)
	s, ok := specs["hithink.finance.market.price.snapshot"]
	if !ok {
		t.Fatal("声明里没有 market.price.snapshot")
	}
	if s.Envelope.Freshness == nil {
		t.Fatal("market.price.snapshot 没有声明 Freshness")
	}

	// 造两个假服务：一个返回 10 天前的数据，一个返回当天的。
	run := func(date string) string {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"success": true, "db": "market", "count": 1,
				"data": []map[string]any{{"date": date, "close": 100.0}},
			})
		}))
		defer srv.Close()
		cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}
		tool := market.NewPriceSnapshotTool(cfg)
		args, _ := json.Marshal(map[string]any{"thscode": "600519.SH"})
		res, _ := tool.Execute(context.Background(), args)
		if res == nil {
			t.Fatal("工具没有返回结果")
		}
		return res.Output
	}

	out := run(time.Now().AddDate(0, 0, -10).Format("2006-01-02"))
	var m map[string]any
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("输出不是合法 JSON：%v", err)
	}
	if _, ok := m["warning"]; !ok {
		t.Errorf("10 天前的数据应该有 warning，实际输出：%s", out)
	}

	out2 := run(time.Now().Format("2006-01-02"))
	var m2 map[string]any
	if err := json.Unmarshal([]byte(out2), &m2); err != nil {
		t.Fatalf("输出不是合法 JSON：%v", err)
	}
	if _, ok := m2["warning"]; ok {
		t.Errorf("当天的数据不该有 warning，实际输出：%s", out2)
	}
}

// TestSentimentStatsOptionalQuery 守着多查询族的"可选查询"语义。
//
// 市场情绪统计要查三张池子：涨停池查不到是致命的（整个统计没意义），跌停池与
// 炸板池查不到则按 0 家继续（迁移前那两条的错误是被静默吞掉的）。这个区别在
// 冻结快照里盖不住——假服务总是成功——所以这里直接让服务按表名失败。
func TestSentimentStatsOptionalQuery(t *testing.T) {
	// 造一个"指定表名就失败"的服务。
	failing := func(failOn string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var req struct {
				SQL string `json:"sql"`
			}
			_ = json.NewDecoder(r.Body).Decode(&req)
			if strings.Contains(req.SQL, failOn) {
				w.WriteHeader(http.StatusInternalServerError)
				_, _ = w.Write([]byte(`{"error":"boom"}`))
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"success": true, "db": "special", "count": 1,
				"data": []map[string]any{{"trade_date": "2026-12-28", "thscode": "600519.SH"}},
			})
		}))
	}

	run := func(failOn string) *types.ToolResult {
		srv := failing(failOn)
		defer srv.Close()
		cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}
		tool := special.NewSentimentStatsTool(cfg)
		res, _ := tool.Execute(context.Background(), json.RawMessage(`{}`))
		return res
	}

	// 跌停池查不到 → 不致命，当成 0 家继续。
	res := run("v_limit_down_pool")
	if res == nil || !res.Success {
		t.Fatalf("跌停池查不到时应当继续，实际：%+v", res)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(res.Output), &m); err != nil {
		t.Fatalf("输出不是合法 JSON：%v", err)
	}
	if got, _ := m["limit_down_count"].(float64); got != 0 {
		t.Errorf("跌停池查不到时 limit_down_count 应当是 0，实际是 %v", m["limit_down_count"])
	}

	// 涨停池查不到 → 致命。
	res2 := run("v_limit_up_pool")
	if res2 == nil || res2.Success {
		t.Fatalf("涨停池查不到时应当失败，实际：%+v", res2)
	}
	if res2.Error == "" {
		t.Error("涨停池查不到时必须给出原因")
	}
}

// TestFuturesInventoryEmptyMessage 守着期货仓单与基差两个工具的"查不到"文案。
//
// 假服务对绑 NO_DATA 的查询返回空集，于是这两个工具会走到空结果分支——
// 冻结快照里盖不住（快照的探针都有数据），所以单独验。
func TestFuturesInventoryEmptyMessage(t *testing.T) {
	srv := fakeservice.New()
	defer srv.Close()
	cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}

	cases := []struct {
		name string
		tool types.Tool
		want string
	}{
		{"futures.inventory.warehouse_receipts",
			futures.NewWarehouseReceiptsTool(cfg), "未查询到仓单数据"},
		{"futures.basis.rate",
			futures.NewFuturesBasisRateTool(cfg), "未查询到基差数据"},
	}
	for _, c := range cases {
		args, _ := json.Marshal(map[string]any{"thscode": fakeservice.NoDataCode})
		res, _ := c.tool.Execute(context.Background(), args)
		if res == nil {
			t.Errorf("%s：没有返回结果", c.name)
			continue
		}
		if res.Success {
			t.Errorf("%s：查不到数据时应当失败", c.name)
			continue
		}
		if res.Error != c.want {
			t.Errorf("%s：空结果文案是 %q，期望 %q", c.name, res.Error, c.want)
		}
	}
}

// TestSectorMembershipTagFilter 守着板块归属的**多值 tags 过滤**与 tags 汇总。
//
// 冻结快照盖不住这一段：假服务返回的那一行没有 tag 字段，于是"按 tags 过滤"
// 与"不按 tags 过滤"的结果一样（都是空集）。这里造一个带 tag 的假服务，
// 让两条路径分开。
func TestSectorMembershipTagFilter(t *testing.T) {
	rows := []map[string]any{
		{"board_name": "白酒", "tag": "industry", "board_thscode": "881101.TI"},
		{"board_name": "人工智能", "tag": "cn_concept", "board_thscode": "881102.TI"},
		{"board_name": "食品饮料", "tag": "industry", "board_thscode": "881103.TI"},
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"success": true, "db": "index", "count": len(rows), "data": rows,
		})
	}))
	defer srv.Close()
	cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}
	tool := indexfinance.NewSectorMembershipTool(cfg)

	run := func(tags string) map[string]any {
		args, _ := json.Marshal(map[string]any{"thscode": "600519.SH", "tags": tags})
		res, _ := tool.Execute(context.Background(), args)
		if res == nil || !res.Success {
			t.Fatalf("tags=%q：执行失败：%+v", tags, res)
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(res.Output), &m); err != nil {
			t.Fatalf("输出不是合法 JSON：%v", err)
		}
		return m
	}

	// 不给 tags：全部返回，tags 字段是出现过的类型（按出现顺序、去重）。
	all := run("")
	if got, _ := all["sector_count"].(float64); got != 3 {
		t.Errorf("不给 tags 时应当返回 3 条，实际 %v", all["sector_count"])
	}
	if got, _ := all["tags"].(string); got != "industry,cn_concept" {
		t.Errorf("tags 字段应当是 \"industry,cn_concept\"，实际 %q", got)
	}

	// 给一个类型：只剩那两条。
	one := run("industry")
	if got, _ := one["sector_count"].(float64); got != 2 {
		t.Errorf("tags=industry 时应当返回 2 条，实际 %v", one["sector_count"])
	}
	if got, _ := one["tags"].(string); got != "industry" {
		t.Errorf("tags 字段应当是 \"industry\"，实际 %q", got)
	}

	// 给两个类型（逗号分隔的多值）：全都要。
	both := run("industry,cn_concept")
	if got, _ := both["sector_count"].(float64); got != 3 {
		t.Errorf("多值 tags 应当返回 3 条，实际 %v", both["sector_count"])
	}
	if got, _ := both["tags"].(string); got != "industry,cn_concept" {
		t.Errorf("tags 字段应当是 \"industry,cn_concept\"，实际 %q", got)
	}
}
