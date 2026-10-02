package pattern

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// noisySignalNames 是**数据结论**（run_signal_audit.sh 跑真实 detectSignals
// 在真实 DuckDB 上量出来的触发频率），不是随手列的。这几个方向都必须守住：
//
//  1. 名单里的每条都必须是真实存在的信号 —— 否则折叠的是一个不存在的名字，
//     拼错一个字符就会让那条信号**既不默认返回、也永远拿不到**（include_noisy
//     只能放行已存在的信号），它就此静默消失。
//  2. 名单里的每条都必须在 declaredSignalNames 里（不能凭空造名）。
//
// 反向（某条信号其实已经 informative 了，名单却没跟上）无法在单测里判定 ——
// 那要重跑 classify_signals.py。所以这里只守名字，另由文档要求重跑。
func TestNoisySignalNamesAreRealSignals(t *testing.T) {
	declared := make(map[string]bool, len(declaredSignalNames))
	for _, n := range declaredSignalNames {
		declared[n] = true
	}
	seen := make(map[string]bool, len(noisySignalNames))
	for _, n := range noisySignalNames {
		if !declared[n] {
			t.Errorf("noisySignalNames 里有 %q，但它不在 declaredSignalNames 里 —— "+
				"折叠一个不存在的信号名，会让真正同名的信号既不默认返回、也拿不到", n)
		}
		if seen[n] {
			t.Errorf("noisySignalNames 里 %q 出现了两次", n)
		}
		seen[n] = true
	}
	if len(noisySignalNames) == 0 {
		t.Fatal("noisySignalNames 是空的，但 classify_signals.py 实测有 noisy 信号")
	}
}

// TestNoisySignalCountPinned 钉住条数。
//
// 这份名单 2026-10-01 改过两次：11 条 → 8 条（统计脚本判据与
// signals.go 对不上），8 条 → 8 条但换了成员（SQL 口径 vs 审计口径：
// MACD动能衰减 错折、Donchian下轨跌破 漏折）。改名单本身有正当理由，
// 但**静默**改就危险了 —— 折叠哪些信号直接决定模型看不到什么。
//
// 要改时：重跑 run_signal_audit.sh，同步本文件、noisySignalNames 的定义
// 与注释里的百分比，再更新这个数字。
const wantNoisySignalCount = 8

func TestNoisySignalCountPinned(t *testing.T) {
	if got := len(noisySignalNames); got != wantNoisySignalCount {
		t.Errorf("noisySignalNames 有 %d 条，期望 %d 条。\n"+
			"如果刚改了 signals.go 的判据或重跑了审计：请同步本文件、"+
			"noisySignalNames 的定义与注释里的百分比。\n"+
			"当前名单：%v", got, wantNoisySignalCount, noisySignalNames)
	}
}

// TestNoisyListMatchesTheAudit pins the list against the audit report that
// produced it.
//
// 这份名单的权威是 signal_frequency_audit.md（跑真实 detectSignals 量出），
// 不是 scripts/classify_signals.py 的 SQL —— 后者分母是"bar 数"而审计是
// "7-bar 窗口数"，同一条件下能差一倍。2026-10-01 先信了 SQL 那一版，
// 同时错了两条：MACD动能衰减 错折（SQL 20.43% vs 审计 10.25%）、
// Donchian下轨跌破 漏折（它需要 close 列，SQL 侧根本量不了）。
//
// 本测试读那份 markdown，把 verdict 为 `noisy` 的行与代码里的名单逐条
// 对比，并校验 signals.go 注释里写的百分比与报告一致。审计重跑后忘了
// 改代码（或反过来），这里会红。
func TestNoisyListMatchesTheAudit(t *testing.T) {
	path := filepath.Join(repoRootForTest(t),
		"internal/agent/tools/hithink_finance/pattern/signal_frequency_audit.md")
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		// 审计报告由本地生产库跑出来（见 run_signal_audit.sh），**不随仓库分发**：
		// 它本身就是数据。缺文件不是失败，是"这份数据不在这里"——本地有文件时照常断言。
		t.Skipf("审计报告不存在，跳过：%s\n"+
			"该文件由本地生产库生成、不随仓库分发；要跑这条断言请先跑 pattern/run_signal_audit.sh", path)
	}
	if err != nil {
		t.Fatalf("读审计报告失败: %v", err)
	}

	// 解析 `| # | 信号 | ... | <freq>% | ... | `verdict` |` 这一行。
	auditNoisy := map[string]float64{}
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(line, "| ") || strings.Contains(line, "|---") {
			continue
		}
		c := strings.Split(line, "|")
		if len(c) < 9 {
			continue
		}
		idx := strings.TrimSpace(c[1])
		if _, err := strconv.Atoi(idx); err != nil {
			continue // 表头或非数据行
		}
		name := strings.TrimSpace(c[2])
		freq, err := strconv.ParseFloat(strings.TrimSuffix(strings.TrimSpace(c[6]), "%"), 64)
		if err != nil {
			continue
		}
		verdict := strings.Trim(strings.TrimSpace(c[8]), "`")
		if verdict == "noisy" {
			auditNoisy[name] = freq
		}
	}
	if len(auditNoisy) == 0 {
		t.Fatal("审计报告里没有 verdict=noisy 的行 —— 报告格式变了？")
	}

	got := map[string]bool{}
	for _, n := range noisySignalNames {
		got[n] = true
	}
	for n := range auditNoisy {
		if !got[n] {
			t.Errorf("%q 在审计里是 noisy（%.2f%%），但不在 noisySignalNames 里 —— "+
				"该折叠的没折叠。审计重跑后请同步 signals.go。", n, auditNoisy[n])
		}
	}
	for _, n := range noisySignalNames {
		if _, ok := auditNoisy[n]; !ok {
			t.Errorf("%q 在 noisySignalNames 里，但审计判定它不是 noisy —— "+
				"该折叠的折错了对象。审计重跑后请同步 signals.go。", n)
		}
	}

	// 注释里的百分比必须与报告一致，否则读者会被误导去核对另一个数。
	for _, n := range noisySignalNames {
		want := auditNoisy[n]
		pat := regexp.MustCompile(`"` + regexp.QuoteMeta(n) + `",\s*//\s*([\d.]+)%`)
		m := pat.FindStringSubmatch(sourceOfSignalsGo(t))
		if m == nil {
			t.Errorf("signals.go 的 noisySignalNames 里 %q 缺少百分比注释", n)
			continue
		}
		got, _ := strconv.ParseFloat(m[1], 64)
		if diff := got - want; diff > 0.05 || diff < -0.05 {
			t.Errorf("%q 的注释写 %.2f%%，审计是 %.2f%%", n, got, want)
		}
	}
}

// repoRootForTest walks up to the repo root from this package's directory
// (internal/agent/tools/hithink_finance/pattern -> four levels up). It cannot
// reuse internal/indicators' loadRepoRegistry — that lives in another package.
func repoRootForTest(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("取工作目录失败: %v", err)
	}
	for range 8 {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("找不到仓库根目录（往上没有 go.mod）")
	return ""
}

func sourceOfSignalsGo(t *testing.T) string {
	t.Helper()
	p := filepath.Join(repoRootForTest(t),
		"internal/agent/tools/hithink_finance/pattern/signals.go")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("读 signals.go 失败: %v", err)
	}
	return string(b)
}

// TestPartitionSignalsSplitsCleanly checks the split itself: nothing may be lost
// or duplicated, and the suppressed side must be exactly the noisy subset.
//
// partitionSignals 是**分组**而非交错：keep 保持自己的相对顺序，suppressed 也
// 保持自己的。所以这里比的是分组后的集合，不是全局逐位相等。
func TestPartitionSignalsSplitsCleanly(t *testing.T) {
	in := []Signal{
		{Name: "MACD金叉", Signal: "bullish"},
		{Name: "线性回归上升", Signal: "bullish"},
		{Name: "KDJ超卖金叉", Signal: "bullish"},
		{Name: "CMF资金流出", Signal: "bearish"},
		{Name: "RSI6超买", Signal: "bearish"},
		// 修正判据后这三条**不再是** noisy，必须默认返回。fixture 放它们
		// 是为了守住这个方向 —— 名单回退成旧版时这里会红。
		{Name: "Aroon多头排列", Signal: "bullish"},
		{Name: "CMF资金流入", Signal: "bullish"},
		{Name: "Keltner挤压", Signal: "neutral"},
	}
	keep, suppressed := partitionSignals(in)

	if len(keep)+len(suppressed) != len(in) {
		t.Fatalf("分割丢信号：输入 %d，输出 %d+%d", len(in), len(keep), len(suppressed))
	}
	// 每一侧内部顺序必须与输入一致
	var keptIn, suppIn []string
	for _, s := range in {
		if isNoisySignal(s.Name) {
			suppIn = append(suppIn, s.Name)
		} else {
			keptIn = append(keptIn, s.Name)
		}
	}
	var gotKeep, gotSupp []string
	for _, s := range keep {
		gotKeep = append(gotKeep, s.Name)
	}
	for _, s := range suppressed {
		gotSupp = append(gotSupp, s.Name)
	}
	if !equalStrings(gotKeep, keptIn) {
		t.Errorf("默认返回的信号 = %v，期望 %v（顺序也应保持）", gotKeep, keptIn)
	}
	if !equalStrings(gotSupp, suppIn) {
		t.Errorf("被折叠的信号 = %v，期望 %v", gotSupp, suppIn)
	}
	for _, s := range keep {
		if isNoisySignal(s.Name) {
			t.Errorf("%q 是 noisy，却出现在默认返回里", s.Name)
		}
	}
	for _, s := range suppressed {
		if !isNoisySignal(s.Name) {
			t.Errorf("%q 不是 noisy，却被折叠了", s.Name)
		}
	}
	if len(suppressed) == 0 {
		t.Error("fixture 里明明有两条 noisy，suppressed 却是空的")
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// TestEmptyInputDoesNotPanic guards the case that actually happens in
// production: a stock with no clean signals at all. summarizeSignals on an
// empty slice is the risk, not the partition.
func TestEmptyInputDoesNotPanic(t *testing.T) {
	keep, suppressed := partitionSignals(nil)
	if len(keep) != 0 || len(suppressed) != 0 {
		t.Fatalf("空输入应返回两个空切片，得到 %d / %d", len(keep), len(suppressed))
	}
	s := summarizeSignals(nil)
	if s == nil {
		t.Fatal("summarizeSignals(nil) 返回 nil，调用方会 panic")
	}
}
