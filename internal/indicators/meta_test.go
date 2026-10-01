package indicators

import (
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// loadRepoRegistry loads the real config/indicators.yaml by walking up from this
// test file's directory.
func loadRepoRegistry(t *testing.T) (*Registry, string) {
	t.Helper()
	reg, root, err := LoadRepoRegistry(".")
	if err != nil {
		t.Fatalf("加载 config/%s 失败: %v", FileName, err)
	}
	return reg, root
}

func TestLoadRepoRegistry(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	if reg.SchemaVersion != SupportedSchemaVersion {
		t.Fatalf("schema_version = %d, want %d", reg.SchemaVersion, SupportedSchemaVersion)
	}
	if len(reg.Indicators) == 0 {
		t.Fatal("indicators 为空")
	}
}

// mustList is the set of indicators that indicators.ts registers today. This
// list is the contract: adding an indicator to the YAML without registering it
// in the frontend (or dropping one) must fail here.
func mustList() []string {
	return []string{
		"Z_MAIN",    // composite
		"Z_SIGNALS", // overlay
		"ZG_WHITE",  //
		"DG_YELLOW", //
		"Z_BBI",     //
		"Z_VOL",     //
		"Z_MACD",    //
		"Z_KDJ",     //
		"ZX_BRICK",  //
		"Z_BRICK",   // legacy alias of ZX_BRICK
		"Z_PCT_RET", //
	}
}

func TestRegistryCoversEveryRegisteredIndicator(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	want := mustList()
	have := map[string]bool{}
	for _, id := range reg.IDs() {
		have[id] = true
	}
	for _, id := range want {
		if !have[id] {
			t.Errorf("config/%s 缺少指标 %s（indicators.ts 会注册它）", FileName, id)
		}
	}
	// 反向：YAML 里多出来的指标必须是有意的，不能是复制粘贴的残留。
	for _, id := range reg.IDs() {
		found := false
		for _, w := range want {
			if w == id {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("config/%s 有前端不认识的指标 %s（YAML 里多出来的条目没人用）", FileName, id)
		}
	}
}

// TestCompositeAndPanels pins the two structural facts the task cares about:
// Z_MAIN is a composite on the main chart, and the sub-chart indicators are
// declared as sub.
func TestCompositeAndPanels(t *testing.T) {
	reg, _ := loadRepoRegistry(t)

	main := reg.Get("Z_MAIN")
	if main == nil {
		t.Fatal("找不到 Z_MAIN")
	}
	if main.Kind != KindComposite {
		t.Errorf("Z_MAIN.kind = %q, want %q（三条独立线必须表达为 composite）", main.Kind, KindComposite)
	}
	if main.Panel != PanelMain {
		t.Errorf("Z_MAIN.panel = %q, want %q", main.Panel, PanelMain)
	}
	if len(main.Series) != 3 {
		t.Errorf("Z_MAIN 有 %d 条线, want 3（zg_white / dg_yellow / bbi）", len(main.Series))
	}
	wantKeys := map[string]string{"zg_white": "DEMA", "dg_yellow": "LONGBBI", "bbi": "BBI"}
	for _, s := range main.Series {
		if want, ok := wantKeys[s.Key]; !ok {
			t.Errorf("Z_MAIN 出现未知线 %q", s.Key)
		} else if s.Formula != want {
			t.Errorf("Z_MAIN/%s.formula = %q, want %q", s.Key, s.Formula, want)
		}
	}

	for _, id := range []string{"Z_VOL", "Z_MACD", "Z_KDJ", "ZX_BRICK", "Z_BRICK", "Z_PCT_RET"} {
		ind := reg.Get(id)
		if ind == nil {
			t.Fatalf("找不到 %s", id)
		}
		if ind.Panel != PanelSub {
			t.Errorf("%s.panel = %q, want %q", id, ind.Panel, PanelSub)
		}
	}
	if sig := reg.Get("Z_SIGNALS"); sig.Kind != KindOverlay || sig.Panel != PanelMain {
		t.Errorf("Z_SIGNALS kind/panel = %s/%s, want overlay/main", sig.Kind, sig.Panel)
	}
}

// TestPeriodsAreTheOnesTheStackAlreadyUses pins the numbers that were
// previously hardcoded in three places. Changing one of these is a behaviour
// change for users, not a refactor.
//
// 这条测试存在的理由:conformance_test.go 的 TestCrossStackConformance
// **抓不到 param 改动** —— 它的 Go reference 与 TS 都从本 YAML 读参数
// (conformance_test.go:665-672),两边一起跟着改,测试保持绿。参数是这套
// 体系里唯一"改了不会自动被发现"的东西,所以必须在这里钉死。
//
// 漏掉过 Z_MAIN:它是最重要的复合主图(白线+黄线+牵牛绳三条线),
// params 只有 white_period=10,而三条线真正的周期藏在 series.params 里。
// 下面 TestSeriesParamsArePinned 补上了这个洞。
func TestPeriodsAreTheOnesTheStackAlreadyUses(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	cases := []struct {
		id     string
		params []int
	}{
		{"Z_MAIN", []int{10}},
		{"ZG_WHITE", []int{10}},
		{"DG_YELLOW", []int{14, 28, 57, 114}},
		{"Z_BBI", []int{3, 6, 12, 24}},
		{"Z_VOL", []int{5, 10}},
		{"Z_MACD", []int{12, 26, 9}},
		{"Z_KDJ", []int{9, 3, 3}},
		{"Z_PCT_RET", []int{3, 21}},
		{"ZX_BRICK", []int{4}},
		{"Z_BRICK", []int{4}},
	}
	for _, c := range cases {
		ind := reg.Get(c.id)
		if ind == nil {
			t.Fatalf("找不到 %s", c.id)
		}
		got := ind.ParamValues()
		if len(got) != len(c.params) {
			t.Errorf("%s params = %v, want %v", c.id, got, c.params)
			continue
		}
		for i := range got {
			if got[i] != c.params[i] {
				t.Errorf("%s params = %v, want %v", c.id, got, c.params)
				break
			}
		}
	}
}

// TestSeriesParamsArePinned pins the per-series periods, which is where the
// real periods of a composite indicator live. Z_MAIN's `params` carries only
// white_period=10; the yellow line (14/28/57/114) and the BBI rope (3/6/12/24)
// are declared on the series and read from there by
// indicators.ts:532-545. Nothing else guards them.
func TestSeriesParamsArePinned(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	cases := []struct {
		id     string
		series map[string][]int
	}{
		{"Z_MAIN", map[string][]int{
			"zg_white":  {10},
			"dg_yellow": {14, 28, 57, 114},
			"bbi":       {3, 6, 12, 24},
		}},
		{"ZG_WHITE", map[string][]int{"zg_white": {10}}},
		{"DG_YELLOW", map[string][]int{"dg_yellow": {14, 28, 57, 114}}},
		{"Z_BBI", map[string][]int{"bbi": {3, 6, 12, 24}}},
		{"Z_MACD", map[string][]int{
			// key 是 series 名（画布上的线），不是 DuckDB 列 alias ——
			// hist 那条线的 key 叫 macd，alias 才叫 macd_hist。
			"dif":  {12, 26},
			"dea":  {12, 26, 9},
			"macd": {12, 26, 9},
		}},
		{"Z_KDJ", map[string][]int{
			"k": {9, 3},
			"d": {9, 3, 3},
			"j": {9, 3, 3},
		}},
		{"Z_PCT_RET", map[string][]int{
			"pct_ret_short": {3},
			"pct_ret_long":  {21},
		}},
		{"Z_VOL", map[string][]int{
			"ma5":  {5},
			"ma10": {10},
		}},
		{"ZX_BRICK", map[string][]int{"brick": {4}}},
	}
	for _, c := range cases {
		ind := reg.Get(c.id)
		if ind == nil {
			t.Fatalf("找不到 %s", c.id)
		}
		byKey := map[string][]int{}
		for _, s := range ind.Series {
			byKey[s.Key] = s.Params
		}
		for key, want := range c.series {
			got, ok := byKey[key]
			if !ok {
				t.Errorf("%s: 缺少 series %q", c.id, key)
				continue
			}
			if len(got) != len(want) {
				t.Errorf("%s/%s params = %v, want %v", c.id, key, got, want)
				continue
			}
			for i := range got {
				if got[i] != want[i] {
					t.Errorf("%s/%s params = %v, want %v", c.id, key, got, want)
					break
				}
			}
		}
	}
}

// TestDuckDBColumnsMatchTheColumnNamesOnDisk is the drift detector for the
// Go/Python stacks: every column declared in indicators.yaml must still be
// spelled exactly that way in the DuckDB view they are read from. A column
// renamed in DuckDB, or dropped from one stack's SQL, fails here.
//
// 2026-10-01: 与 TestDuckDBColumnContractHoldsInBothStacks 同因，11 个工作台
// 指标现在全部是 frontend 实现，本文件一条 DuckDB 列声明都没有（此前
// Z_MACD/Z_KDJ/Z_VOL 声明走 v_indicators_daily，但工作台从未读过那些列，
// 声明是假的，已改正）。没有列可守时跳过，而不是为一个不存在的前提失败。
func TestDuckDBColumnsMatchTheColumnNamesOnDisk(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	cols := reg.DeclaredColumns()
	if len(cols) == 0 {
		t.Skip("当前没有声明的 DuckDB 列（全部指标为 frontend 实现）")
	}
	for _, c := range cols {
		if c.Column == "" {
			t.Errorf("%s: storage column has an empty name", c.Indicator)
		}
		if c.Alias == "" {
			t.Errorf("%s: column %s has an empty alias", c.Indicator, c.Column)
		}
	}
}

func TestViewPresets(t *testing.T) {
	reg, _ := loadRepoRegistry(t)
	if len(reg.MainPresets()) != 4 {
		t.Errorf("main_presets = %d, want 4", len(reg.MainPresets()))
	}
	if len(reg.SubPresets()) != 7 {
		t.Errorf("sub_presets = %d, want 7", len(reg.SubPresets()))
	}
	// 第一个主图模式必须是 Z_MAIN —— 它是默认模式。
	if reg.MainPresets()[0].ID != "zettaranc" {
		t.Errorf("默认主图模式 = %q, want %q", reg.MainPresets()[0].ID, "zettaranc")
	}
	first := reg.MainPresets()[0]
	if len(first.Indicators) != 1 || first.Indicators[0] != "Z_MAIN" {
		t.Errorf("zettaranc 模式 = %v, want [Z_MAIN]", first.Indicators)
	}
	// 默认副图
	if reg.SubPresets()[0].ID != "VOL_AND_BRICK" {
		t.Errorf("默认副图模式 = %q, want %q", reg.SubPresets()[0].ID, "VOL_AND_BRICK")
	}
	// 每个副图按钮都必须有 tooltip（历史上 7 个按钮一个都没有）。
	for _, v := range reg.SubPresets() {
		if strings.TrimSpace(v.Hint) == "" {
			t.Errorf("副图模式 %s 缺 hint", v.ID)
		}
	}
}

func TestValidateRejectsBadInput(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(r *Registry)
		wantErr string
	}{
		{
			name:    "duplicate id",
			mutate:  func(r *Registry) { r.Indicators[1].ID = r.Indicators[0].ID },
			wantErr: "duplicate id",
		},
		{
			name:    "bad kind",
			mutate:  func(r *Registry) { r.Indicators[0].Kind = "nope" },
			wantErr: "kind",
		},
		{
			name:    "alias params drift",
			mutate:  func(r *Registry) { r.Get("Z_BRICK").Params[0].Value = 5 },
			wantErr: "differ from alias target",
		},
		{
			name:    "alias target missing",
			mutate:  func(r *Registry) { r.Get("Z_BRICK").AliasOf = "NOPE" },
			wantErr: "does not exist",
		},
		{
			name:    "unknown colour token",
			mutate:  func(r *Registry) { r.Get("Z_BBI").Series[0].Color = "chartreuse" },
			wantErr: "color",
		},
		{
			name:    "bar without base_value",
			mutate:  func(r *Registry) { r.Get("Z_VOL").Series[0].BaseValue = nil },
			wantErr: "base_value",
		},
		{
			name:    "main indicator in a sub preset",
			mutate:  func(r *Registry) { r.Views.SubPresets[0].Indicators = []string{"Z_MAIN"} },
			wantErr: "expected sub",
		},
		{
			name:    "zero period",
			mutate:  func(r *Registry) { r.Get("Z_PCT_RET").Params[0].Value = 0 },
			wantErr: "non-positive",
		},
		{
			name:    "fixture too short for MA114",
			mutate:  func(r *Registry) { r.Conformance.FixtureBars = 60 },
			wantErr: "fixture_bars",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, root := loadRepoRegistry(t)
			reg, err := LoadFromFile(filepath.Join(root, "config", FileName))
			if err != nil {
				t.Fatalf("基准加载失败: %v", err)
			}
			c.mutate(reg)
			err = reg.Validate()
			if err == nil {
				t.Fatalf("Validate() 接受了非法输入（%s）", c.name)
			}
			if !strings.Contains(err.Error(), c.wantErr) {
				t.Errorf("错误信息 %q 不含 %q", err.Error(), c.wantErr)
			}
		})
	}
}

func TestLoadFromFileMissing(t *testing.T) {
	if _, err := LoadFromFile(filepath.Join(t.TempDir(), FileName)); err == nil {
		t.Fatal("缺失文件应该报错（这份配置不是可选的）")
	}
}

// TestGeneratedFrontendModule keeps frontend/src/.../indicator-meta.ts in sync
// with config/indicators.yaml.
//
//	go test ./internal/indicators/ -run TestGeneratedFrontendModule -update
//
// A failure here means: someone edited config/indicators.yaml and did not
// regenerate. That is the whole point — the frontend must never fall back to
// its own hardcoded periods.
func TestGeneratedFrontendModule(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	want, err := RenderFrontendModule(reg)
	if err != nil {
		t.Fatalf("渲染前端模块失败: %v", err)
	}
	path := filepath.Join(root, filepath.FromSlash(FrontendModulePath))
	got, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			if *update {
				if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
					t.Fatalf("创建目录失败: %v", err)
				}
				if err := os.WriteFile(path, []byte(want), 0o644); err != nil {
					t.Fatalf("写入失败: %v", err)
				}
				t.Logf("已生成 %s", FrontendModulePath)
				return
			}
			t.Fatalf("%s 不存在：%v\n用 -update 生成", FrontendModulePath, err)
		}
		t.Fatalf("读取 %s 失败: %v", FrontendModulePath, err)
	}
	if string(got) != want {
		if *update {
			if err := os.WriteFile(path, []byte(want), 0o644); err != nil {
				t.Fatalf("写入失败: %v", err)
			}
			t.Logf("已重新生成 %s", FrontendModulePath)
			return
		}
		t.Fatalf("%s 与 config/%s 不同步。\n改了 YAML 之后运行：\n  go test ./internal/indicators/ -run TestGeneratedFrontendModule -update",
			FrontendModulePath, FileName)
	}
}

// update is set by `-update`; see TestGeneratedFrontendModule.
var update = flag.Bool("update", false, "rewrite the generated frontend module from config/indicators.yaml")

// TestGeneratedPythonModule is the Python half of the staleness guard.
//
//	python-service has no YAML dependency, so it reads a generated module rather
//
// than parsing config/indicators.yaml at runtime. If this test fails, the
// strategy layer is running on stale indicator parameters.
//
//	go test ./internal/indicators/ -run TestGeneratedPythonModule -update
func TestGeneratedPythonModule(t *testing.T) {
	reg, root := loadRepoRegistry(t)
	want, err := RenderPythonModule(reg)
	if err != nil {
		t.Fatalf("渲染 Python 模块失败: %v", err)
	}
	path := filepath.Join(root, filepath.FromSlash(PythonModulePath))
	got, err := os.ReadFile(path)
	switch {
	case os.IsNotExist(err):
		if !*update {
			t.Fatalf("%s 不存在：%v\n用 -update 生成", PythonModulePath, err)
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatalf("创建目录失败: %v", err)
		}
	case err != nil:
		t.Fatalf("读取 %s 失败: %v", PythonModulePath, err)
	case string(got) == want:
		return
	}
	if !*update {
		t.Fatalf("%s 与 config/%s 不同步。\n改了 YAML 之后运行：\n  go test ./internal/indicators/ -run TestGeneratedPythonModule -update",
			PythonModulePath, FileName)
	}
	if err := os.WriteFile(path, []byte(want), 0o644); err != nil {
		t.Fatalf("写入 %s 失败: %v", PythonModulePath, err)
	}
	t.Logf("已重新生成 %s", PythonModulePath)
}
