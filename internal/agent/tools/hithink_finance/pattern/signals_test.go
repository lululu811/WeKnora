package pattern

import (
	"testing"
)

// 本文件固定 PSAR 翻转的行为。
//
// PSAR 的多空关系只取决于 PSAR 和收盘价谁大：PSAR 在价格下方是多头侧，在价格
// 上方是空头侧；相邻两根 K 线的这个关系互换，就是一次翻转。所以实现必须读一段
// 时间序列（rows[0] 最新、rows[1] 前一日），而不是拿单根 K 线去比——这也是上面
// Supertrend 翻转、DI 金叉的写法。
//
// 最关键的一条是 0：query.go 取的是 COALESCE(trend_psar, 0)，所以 0 表示"这根
// K 没有 PSAR"。如果把 0 当成真实值参与"PSAR 是否在价格下方"的比较，任何一段
// 缺 PSAR 的数据都会被判成多头侧，再跟旁边的真实 bar 一比就凭空捏出一次翻转。
// 模型会拿到一个纯粹由数据缺失造出来的信号——正是这份文件在消灭的那类静默错误。

// psarSignals 只挑出 PSAR 相关的信号。detectSignals 一次会吐出几十条别的信号
// （CCI、ADX、金叉……），断言整张列表会被无关噪声淹没。
func psarSignals(signals []Signal) []Signal {
	var out []Signal
	for _, s := range signals {
		if s.Name == "PSAR翻多" || s.Name == "PSAR翻空" {
			out = append(out, s)
		}
	}
	return out
}

func TestDetectSignalsPSARBullToBearFlip(t *testing.T) {
	// rows[0] 是最新一根。前一日 PSAR 在价格下方（多头侧），最新一根 PSAR 跳到
	// 价格上方（空头侧）——真实的由多转空。
	rows := []row{
		{Date: "2026-09-29", Close: 18.00, PSAR: 19.20},
		{Date: "2026-09-26", Close: 19.80, PSAR: 18.50},
	}

	got := psarSignals(detectSignals(rows))
	if len(got) != 1 {
		t.Fatalf("由多转空应恰好产生 1 条 PSAR 信号，实际 %d 条：%+v", len(got), got)
	}
	if got[0].Name != "PSAR翻空" {
		t.Errorf("方向反了：应由多转空，实际 %q", got[0].Name)
	}
	if got[0].Signal != "bearish" || got[0].Category != "sell" {
		t.Errorf("翻空应是 sell/bearish，实际 %s/%s", got[0].Category, got[0].Signal)
	}
	if got[0].Date != "2026-09-29" {
		t.Errorf("信号应挂在最新一根 K 上，实际 %q", got[0].Date)
	}
	// 强度跟 Supertrend 翻转保持一致，两者都是趋势跟踪系统的方向反转。
	if got[0].Strength != 0.8 {
		t.Errorf("强度应与 Supertrend 翻转一致为 0.8，实际 %v", got[0].Strength)
	}
}

func TestDetectSignalsPSARBearToBullFlip(t *testing.T) {
	// 反方向：前一日 PSAR 在价格上方（空头侧），最新一根掉到价格下方。
	rows := []row{
		{Date: "2026-09-29", Close: 21.40, PSAR: 20.10},
		{Date: "2026-09-26", Close: 19.50, PSAR: 20.80},
	}

	got := psarSignals(detectSignals(rows))
	if len(got) != 1 {
		t.Fatalf("由空转多应恰好产生 1 条 PSAR 信号，实际 %d 条：%+v", len(got), got)
	}
	if got[0].Name != "PSAR翻多" {
		t.Errorf("方向反了：应由空转多，实际 %q", got[0].Name)
	}
	if got[0].Signal != "bullish" || got[0].Category != "buy" {
		t.Errorf("翻多应是 buy/bullish，实际 %s/%s", got[0].Category, got[0].Signal)
	}
	if got[0].Date != "2026-09-29" {
		t.Errorf("信号应挂在最新一根 K 上，实际 %q", got[0].Date)
	}
	if got[0].Strength != 0.8 {
		t.Errorf("强度应与 Supertrend 翻转一致为 0.8，实际 %v", got[0].Strength)
	}
}

func TestDetectSignalsPSARZeroMeansNoDataNotBullish(t *testing.T) {
	// 收盘价为正、PSAR 全是 0 —— 也就是 trend_psar 整列缺失的标的。
	// 如果把 0 当成真实 PSAR：0 < 收盘价，两根 K 都会被判成多头侧，于是不翻转；
	// 但只要有一根的 PSAR 是真值（比如刚补齐数据），就会凭空多出一次"翻转"。
	// 两种都是错的：缺数据就是缺数据，一个信号都不该有。
	rows := []row{
		{Date: "2026-09-29", Close: 18.00, PSAR: 0},
		{Date: "2026-09-26", Close: 19.80, PSAR: 0},
	}

	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("PSAR 缺失（COALESCE 补 0）时不得发出任何信号，实际：%+v", got)
	}
}

// 只有一根 K 的 PSAR 缺失时同样不能翻转：一侧是真实值、一侧是 0，比较出来的
// "方向变化"是数据补齐的产物，不是行情反转。
func TestDetectSignalsPSARPartialZeroEmitsNothing(t *testing.T) {
	rows := []row{
		{Date: "2026-09-29", Close: 18.00, PSAR: 19.20}, // 真实值：空头侧
		{Date: "2026-09-26", Close: 19.80, PSAR: 0},     // 缺失
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("只有一根 K 的 PSAR 缺失时不得发出信号，实际：%+v", got)
	}

	rows = []row{
		{Date: "2026-09-29", Close: 18.00, PSAR: 0},     // 缺失
		{Date: "2026-09-26", Close: 19.80, PSAR: 18.50}, // 真实值：多头侧
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("只有一根 K 的 PSAR 缺失时不得发出信号，实际：%+v", got)
	}
}

// 收盘价也是从 market 库按 date 合并进来的，合并不上就是 0。零价同样不能参与
// 大小比较，否则就是用"没有价格"编出一根 K 线。
func TestDetectSignalsPSARZeroCloseEmitsNothing(t *testing.T) {
	rows := []row{
		{Date: "2026-09-29", Close: 0, PSAR: 19.20},
		{Date: "2026-09-26", Close: 19.80, PSAR: 18.50},
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("收盘价缺失时不得发出信号，实际：%+v", got)
	}
}

// 同一侧不叫翻转。连续两根都在多头侧（PSAR 都在价格下方）不该触发任何信号，
// 否则每根 K 线都会报一次"翻转"。
func TestDetectSignalsPSARSameSideIsNotAFlip(t *testing.T) {
	rows := []row{
		{Date: "2026-09-29", Close: 21.40, PSAR: 20.10},
		{Date: "2026-09-26", Close: 19.80, PSAR: 18.50},
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("多头侧未变，不该报翻转，实际：%+v", got)
	}

	rows = []row{
		{Date: "2026-09-29", Close: 18.00, PSAR: 19.20},
		{Date: "2026-09-26", Close: 19.80, PSAR: 20.80},
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("空头侧未变，不该报翻转，实际：%+v", got)
	}
}

// 翻转只比较最近两根 K 线，更早的方向变化不该在这个窗口里重复报出来。
func TestDetectSignalsPSAROnlyComparesAdjacentBars(t *testing.T) {
	// rows[0] 最新、rows[1] 前一日都在多头侧；rows[2] 曾经是空头侧。
	// 那次切换发生在 rows[2]→rows[1] 之间，不是最新这一根。
	rows := []row{
		{Date: "2026-09-29", Close: 21.40, PSAR: 20.10},
		{Date: "2026-09-26", Close: 19.80, PSAR: 18.50},
		{Date: "2026-09-25", Close: 17.00, PSAR: 18.90},
	}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("只应比较相邻两根 K，不该在更早的窗口报翻转，实际：%+v", got)
	}
}

// 只有一根 K 时无从比较，必须安静返回，不能 panic。
func TestDetectSignalsPSARSingleRowEmitsNothing(t *testing.T) {
	rows := []row{{Date: "2026-09-29", Close: 18.00, PSAR: 19.20}}
	if got := psarSignals(detectSignals(rows)); len(got) != 0 {
		t.Fatalf("单根 K 线无法判定翻转，实际：%+v", got)
	}
}

func TestPSARStateRejectsMissingData(t *testing.T) {
	cases := []struct {
		name        string
		r           row
		wantOK      bool
		wantBullish bool
	}{
		{"psar 在价格下方 = 多头侧", row{Close: 10, PSAR: 9}, true, true},
		{"psar 在价格上方 = 空头侧", row{Close: 10, PSAR: 11}, true, false},
		{"psar 恰好等于价格不算多头", row{Close: 10, PSAR: 10}, true, false},
		{"psar 为 0 = 无数据", row{Close: 10, PSAR: 0}, false, false},
		{"psar 为负 = 无数据", row{Close: 10, PSAR: -1}, false, false},
		{"收盘价为 0 = 无数据", row{Close: 0, PSAR: 9}, false, false},
		{"收盘价为负 = 无数据", row{Close: -1, PSAR: 9}, false, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			bullish, ok := psarState(tc.r)
			if ok != tc.wantOK {
				t.Errorf("ok = %v，期望 %v", ok, tc.wantOK)
			}
			if ok && bullish != tc.wantBullish {
				t.Errorf("bullish = %v，期望 %v", bullish, tc.wantBullish)
			}
		})
	}
}
