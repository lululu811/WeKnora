package pattern

import "testing"

// noisySignalNames 是**数据结论**（scripts/classify_signals.py 在真实 DuckDB 上
// 量出来的触发频率），不是随手列的。这两个方向都必须守住：
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

// wantNoisySignalCount 钉住条数。
//
// 这条名单 2026-10-01 改过一次：11 条 → 8 条，因为统计脚本的判据与
// signals.go 对不上（Aroon多头 / CMF资金流入 / Keltner挤压 掉出了名单）。
// 改名单本身是有正当理由的，但**静默**改就危险了 —— 折叠哪些信号直接决定
// 模型看不到什么。所以条数与内容都要有人点头。
//
// 要改时：重跑 scripts/classify_signals.py，同步本文件、signals.go 的
// noisySignalNames 与注释里的百分比，再更新这个数字。
const wantNoisySignalCount = 8

func TestNoisySignalCountPinned(t *testing.T) {
	if got := len(noisySignalNames); got != wantNoisySignalCount {
		t.Errorf("noisySignalNames 有 %d 条，期望 %d 条。\n"+
			"如果刚改了 signals.go 的判据或重跑了 classify_signals.py：请同步 "+
			"本文件、noisySignalNames 的定义与注释里的百分比。\n"+
			"当前名单：%v", got, wantNoisySignalCount, noisySignalNames)
	}
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
