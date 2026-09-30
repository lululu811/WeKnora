package pattern

import (
	"testing"
)

// 本文件逐条钉住**本次新增**的 18 个信号，每个都要回答三个问题：
//
//	1. 条件成立时它真的会响吗（不是写了但永远不触发）
//	2. 条件不成立时它闭嘴吗（阈值是门槛，不是建议）
//	3. **数据缺失时它闭嘴吗**——这是最要紧的一条
//
// 第 3 条单拎出来说：query.go 对每一列都写了 COALESCE(x, 0)，于是"当天没这一列的值"
// 和"这一列恰好等于 0"在下游长得一模一样。带阈值判据的列最容易翻车——一段缺数据的
// 历史照样会跨过阈值，凭空报出一条信号，而模型无从分辨那是行情还是缺列。这个包已经
// 在 PSAR 上出过这一次（psarState 就是那次事故留下的），现在每条新信号都按同一套办法
// 处理：判定依据任一为 0 就不发。
//
// 缺数据最阴险的形态不是"整列都是 0"（那个 hasData 一眼就挡住了），而是**只有一根**
// 缺：那正是 signal 看起来成立、实际只是数据补齐的瞬间。所以下面的 gap 用例一律是
// "把 fire 数据里的某一根清零"，不是"从零开始造数据"。

// zeroAt 返回把 rows[i] 上若干列清零后的副本，模拟"这一列当天没有值"。
func zeroAt(rows []row, i int, set func(r *row)) []row {
	out := append([]row(nil), rows...)
	r := out[i]
	set(&r)
	out[i] = r
	return out
}

// fired reports 名为 want 的信号是否在结果里。
func fired(signals []Signal, want string) bool {
	for _, s := range signals {
		if s.Name == want {
			return true
		}
	}
	return false
}

// gapCase 是一个"数据有缺口"的变体。
type gapCase struct {
	rows []row
	why  string
}

type signalCase struct {
	signal string
	fire   []row     // 必须响
	quiet  []row     // 合法读数但不该响
	gaps   []gapCase // 数据有缺口，绝不该响
}

func newSignalCases() []signalCase {
	// 挤压带：布林带收在 Keltner 通道里。
	squeezed := func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 20.5, 19.5, 21.0, 19.0 }
	notSqueezed := func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 21.0, 19.0, 20.0, 20.0 }

	return []signalCase{
		{
			// J < 0 是合法读数，所以这里不能像价格那样用 >0 判缺失，判据是 J 的
			// 两个输入 K/D（取值 0~100，0 只可能是空值）。
			signal: "J超卖回中",
			fire: []row{
				with(func(r *row) { r.K, r.D, r.J = 20, 28, 4 }),
				with(func(r *row) { r.K, r.D, r.J = 18, 30, -6 }),
			},
			quiet: []row{
				with(func(r *row) { r.K, r.D, r.J = 18, 30, -6 }),
				with(func(r *row) { r.K, r.D, r.J = 17, 30, -9 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.K, r.D, r.J = 20, 28, 4 }),
					with(func(r *row) { r.K, r.D, r.J = 18, 30, -6 }),
				}, 1, func(r *row) { r.K = 0 }), "前一日 K 缺失 → J 不可信"},
				{zeroAt([]row{
					with(func(r *row) { r.K, r.D, r.J = 20, 28, 4 }),
					with(func(r *row) { r.K, r.D, r.J = 18, 30, -6 }),
				}, 0, func(r *row) { r.D = 0 }), "最新一日 D 缺失 → J 不可信"},
			},
		},
		{
			signal: "J超买回中",
			fire: []row{
				with(func(r *row) { r.K, r.D, r.J = 50, 30, 90 }),
				with(func(r *row) { r.K, r.D, r.J = 90, 30, 210 }),
			},
			quiet: []row{
				with(func(r *row) { r.K, r.D, r.J = 90, 30, 210 }),
				with(func(r *row) { r.K, r.D, r.J = 88, 30, 204 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.K, r.D, r.J = 50, 30, 90 }),
					with(func(r *row) { r.K, r.D, r.J = 90, 30, 210 }),
				}, 1, func(r *row) { r.D = 0 }), "前一日 D 缺失 → J 不可信"},
			},
		},
		{
			signal: "ER转高效",
			fire: []row{
				with(func(r *row) { r.ER = 0.45 }),
				with(func(r *row) { r.ER = 0.20 }),
			},
			quiet: []row{
				with(func(r *row) { r.ER = 0.45 }),
				with(func(r *row) { r.ER = 0.50 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.ER = 0.45 }),
					with(func(r *row) { r.ER = 0.20 }),
				}, 1, func(r *row) { r.ER = 0 }), "前一日 ER 缺失：0 会被读成'效率最低'，凭空报反转"},
			},
		},
		{
			signal: "ER转低效",
			fire: []row{
				with(func(r *row) { r.ER = 0.20 }),
				with(func(r *row) { r.ER = 0.45 }),
			},
			quiet: []row{
				with(func(r *row) { r.ER = 0.20 }),
				with(func(r *row) { r.ER = 0.15 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.ER = 0.20 }),
					with(func(r *row) { r.ER = 0.45 }),
				}, 0, func(r *row) { r.ER = 0 }), "最新一日 ER 缺失"},
			},
		},
		{
			signal: "CHOP进入震荡",
			fire: []row{
				with(func(r *row) { r.Chop = 70 }),
				with(func(r *row) { r.Chop = 55 }),
			},
			quiet: []row{
				with(func(r *row) { r.Chop = 70 }),
				with(func(r *row) { r.Chop = 75 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.Chop = 70 }),
					with(func(r *row) { r.Chop = 55 }),
				}, 1, func(r *row) { r.Chop = 0 }), "前一日 CHOP 缺失：0 是'最强趋势'，会造出假升破"},
			},
		},
		{
			signal: "CHOP重回趋势",
			fire: []row{
				with(func(r *row) { r.Chop = 30 }),
				with(func(r *row) { r.Chop = 50 }),
			},
			quiet: []row{
				with(func(r *row) { r.Chop = 30 }),
				with(func(r *row) { r.Chop = 25 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.Chop = 30 }),
					with(func(r *row) { r.Chop = 50 }),
				}, 0, func(r *row) { r.Chop = 0 }), "最新一日 CHOP 缺失"},
			},
		},
		{
			signal: "布林中轨收复",
			fire: []row{
				with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
				with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
			},
			quiet: []row{
				with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
				with(func(r *row) { r.Close, r.BBMid = 20.5, 20.0 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
					with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
				}, 1, func(r *row) { r.BBMid = 0 }), "前一日中轨缺失"},
				{zeroAt([]row{
					with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
					with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
				}, 0, func(r *row) { r.Close = 0 }), "最新一日收盘价缺失（从 market 库合并，合不上就是 0）"},
			},
		},
		{
			signal: "布林中轨跌破",
			fire: []row{
				with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
				with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
			},
			quiet: []row{
				with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
				with(func(r *row) { r.Close, r.BBMid = 19.5, 20.0 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
					with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
				}, 1, func(r *row) { r.BBMid = 0 }), "前一日中轨缺失"},
			},
		},
		{
			// 轨值放在 rows[1]：dcl 含当天，拿当天收盘跟它比恒为假（见 signals.go）。
			signal: "Donchian下轨跌破",
			fire: []row{
				with(func(r *row) { r.Close = 18.5 }),
				with(func(r *row) { r.Close, r.DCLower = 19.5, 19.0 }),
			},
			// quiet：收盘 19.5 并没有跌破前一根的下轨 19.0，所以不该报。
			quiet: []row{
				with(func(r *row) { r.Close = 19.5 }),
				with(func(r *row) { r.Close, r.DCLower = 18.5, 19.0 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.Close = 18.5 }),
					with(func(r *row) { r.Close, r.DCLower = 19.5, 19.0 }),
				}, 1, func(r *row) { r.DCLower = 0 }), "前一日下轨缺失：0 会让任何正价都变成破位"},
			},
		},
		{
			// 压缩状态可能连着十几天，所以只报"开始"的那一根。quiet 用的正是
			// "前一日已经在压缩中"——同一状态第二天不再报第二次。
			signal: "Keltner挤压",
			fire:   []row{with(squeezed), with(notSqueezed)},
			quiet:  []row{with(squeezed), with(squeezed)},
			gaps: []gapCase{
				{zeroAt([]row{with(squeezed), with(notSqueezed)}, 0, func(r *row) { r.KCUpper = 0 }),
					"最新一日 KC 上轨缺失：与 0 比较会造出一条极窄的假通道"},
				{zeroAt([]row{with(squeezed), with(notSqueezed)}, 0, func(r *row) { r.BBUpper = 0 }),
					"最新一日布林上轨缺失"},
			},
		},
		{
			signal: "Keltner挤压向上突破",
			fire: []row{
				with(func(r *row) { squeezed(r); r.Close = 21.5 }),
				with(squeezed),
			},
			quiet: []row{
				with(func(r *row) { squeezed(r); r.Close = 21.5 }),
				with(notSqueezed),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { squeezed(r); r.Close = 21.5 }),
					with(squeezed),
				}, 1, func(r *row) { r.KCLower = 0 }), "前一日 KC 下轨缺失：压缩状态无法判定"},
				{zeroAt([]row{
					with(func(r *row) { squeezed(r); r.Close = 21.5 }),
					with(squeezed),
				}, 0, func(r *row) { r.KCUpper = 0 }), "最新一日 KC 上轨缺失"},
			},
		},
		{
			signal: "Keltner挤压向下突破",
			fire: []row{
				with(func(r *row) { squeezed(r); r.Close = 18.5 }),
				with(squeezed),
			},
			quiet: []row{
				with(func(r *row) { squeezed(r); r.Close = 18.5 }),
				with(notSqueezed),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { squeezed(r); r.Close = 18.5 }),
					with(squeezed),
				}, 1, func(r *row) { r.KCUpper = 0 }), "前一日 KC 上轨缺失：压缩状态无法判定"},
			},
		},
		{
			signal: "NATR进入高波动档位",
			fire:   []row{with(func(r *row) { r.NATR = 6.5 }), with(func(r *row) { r.NATR = 4.0 })},
			quiet:  []row{with(func(r *row) { r.NATR = 6.5 }), with(func(r *row) { r.NATR = 7.0 })},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.NATR = 6.5 }), with(func(r *row) { r.NATR = 4.0 }),
				}, 1, func(r *row) { r.NATR = 0 }), "前一日 NATR 缺失：0 是绝对静默，5% 门槛形同虚设"},
			},
		},
		{
			signal: "NATR退出高波动档位",
			fire:   []row{with(func(r *row) { r.NATR = 4.0 }), with(func(r *row) { r.NATR = 6.5 })},
			quiet:  []row{with(func(r *row) { r.NATR = 4.0 }), with(func(r *row) { r.NATR = 3.5 })},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.NATR = 4.0 }), with(func(r *row) { r.NATR = 6.5 }),
				}, 0, func(r *row) { r.NATR = 0 }), "最新一日 NATR 缺失"},
			},
		},
		{
			signal: "价量背离",
			fire: []row{
				with(func(r *row) { r.PVT, r.Close = 900, 11.0 }),
				neutralRow(), neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
			},
			// 价涨 PVT 也涨：量价同向，不是背离。
			quiet: []row{
				with(func(r *row) { r.PVT, r.Close = 1200, 11.0 }),
				neutralRow(), neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
			},
			gaps: []gapCase{
				// 最阴险的一种：只有窗口起点那一根缺值，PVT 变成 0，差值就成了
				// -1000 的巨额流出，价涨的标的立刻报出"量能不支持上涨"。
				{zeroAt([]row{
					with(func(r *row) { r.PVT, r.Close = 900, 11.0 }),
					neutralRow(), neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
				}, 5, func(r *row) { r.PVT = 0 }), "窗口起点 PVT 缺失 → 差值变成假巨额流出"},
				{zeroAt([]row{
					with(func(r *row) { r.PVT, r.Close = 900, 11.0 }),
					neutralRow(), neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
				}, 0, func(r *row) { r.Close = 0 }), "最新一日收盘价缺失"},
			},
		},
		{
			signal: "缩量下跌吸筹",
			fire: []row{
				with(func(r *row) { r.PVT, r.Close = 1200, 9.0 }),
				neutralRow(), neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
			},
			quiet: []row{
				with(func(r *row) { r.PVT, r.Close = 800, 9.0 }),
				neutralRow(), neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.PVT, r.Close = 1200, 9.0 }),
					neutralRow(), neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }),
				}, 5, func(r *row) { r.PVT = 0 }), "窗口起点 PVT 缺失 → 差值变成假巨额流入"},
			},
		},
		{
			signal: "PVI强于NVI吸筹",
			fire: []row{
				with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
				neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
			},
			// 两个窗口 PVI 都强于 NVI：常态里一直成立，逐根报等于什么都没说。
			quiet: []row{
				with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1250, 1050 }),
				neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
					neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				}, 5, func(r *row) { r.PVI = 0 }), "最新窗口起点 PVI 缺失"},
				{zeroAt([]row{
					with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
					neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				}, 6, func(r *row) { r.NVI = 0 }), "前一个窗口起点 NVI 缺失"},
			},
		},
		{
			signal: "NVI强于PVI派发",
			fire: []row{
				with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
				with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
				neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
			},
			quiet: []row{
				with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
				with(func(r *row) { r.PVI, r.NVI = 1050, 1250 }),
				neutralRow(), neutralRow(), neutralRow(),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
			},
			gaps: []gapCase{
				{zeroAt([]row{
					with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
					with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
					neutralRow(), neutralRow(), neutralRow(),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
					with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
				}, 5, func(r *row) { r.NVI = 0 }), "最新窗口起点 NVI 缺失"},
			},
		},
	}
}

func TestNewSignalsFire(t *testing.T) {
	for _, tc := range newSignalCases() {
		t.Run(tc.signal, func(t *testing.T) {
			if !fired(detectSignals(tc.fire), tc.signal) {
				t.Errorf("条件成立却没报 %q。实际：%v", tc.signal, emitted(tc.fire))
			}
		})
	}
}

func TestNewSignalsStayQuietBelowThreshold(t *testing.T) {
	for _, tc := range newSignalCases() {
		t.Run(tc.signal, func(t *testing.T) {
			if got := detectSignals(tc.quiet); fired(got, tc.signal) {
				t.Errorf("条件不成立却报了 %q：%v。阈值必须是门槛，不是建议。",
					tc.signal, emitted(tc.quiet))
			}
		})
	}
}

// TestNewSignalsEmitNothingOnMissingData 是本文件的主测试。
func TestNewSignalsEmitNothingOnMissingData(t *testing.T) {
	for _, tc := range newSignalCases() {
		t.Run(tc.signal, func(t *testing.T) {
			for i, g := range tc.gaps {
				if got := detectSignals(g.rows); fired(got, tc.signal) {
					t.Errorf("缺口 %d（%s）时报了 %q：%v。\n"+
						"COALESCE 补出来的 0 是数据缺失，不是读数；用它跨阈值等于用缺数据"+
						"伪造出一条看起来完全正常的信号。", i, g.why, tc.signal, emitted(g.rows))
				}
			}
		})
	}
}

// TestWilliamsRZeroIsNotOverbought 单独钉住一次既有 bug 的修复。
//
// 判据 `willr > -20` 会把 COALESCE 补出来的 0 当成超买：%R 的取值区间是 -100~0，
// 0 正好落在 -20 的上侧。于是凡是 momentum_willr_14 缺列的标的，工具每天都会报一条
// "Williams%R超买"，还附一个看起来像模像样的读数。这条测试的意义是：哪天有人为了
// "修好"它而把 < 0 的护栏删掉，这里立刻红。
func TestWilliamsRZeroIsNotOverbought(t *testing.T) {
	if got := detectSignals([]row{with(func(r *row) { r.WillR = 0 })}); fired(got, "Williams%R超买") {
		t.Errorf("willr 为 0（COALESCE 补出来的空值）不得报超买，实际：%v", emitted([]row{with(func(r *row) { r.WillR = 0 })}))
	}
	// 真实的超买读数（-10）必须照报，否则就是把这个 bug 修过头了。
	if got := detectSignals([]row{with(func(r *row) { r.WillR = -10 })}); !fired(got, "Williams%R超买") {
		t.Error("真实超买读数 -10 应当照常报出 Williams%R超买")
	}
	if got := detectSignals([]row{with(func(r *row) { r.WillR = -85 })}); !fired(got, "Williams%R超卖") {
		t.Error("真实超卖读数 -85 应当照常报出 Williams%R超卖")
	}
}

// TestNeutralRowIsSilent 用中性基准行做一次全量体检：任何一列都没给值时，
// 整份数据不应该产生任何一条新信号。上面所有 gap 用例都是从"能触发"退化的，
// 这条是从零开始——两个方向都堵住才叫缺数据不发信号。
func TestNeutralRowIsSilent(t *testing.T) {
	// 只有 RSI6 / MFI 需要给中性值：它们的判据是 <20 / >20，0 落在超买侧。
	rows := make([]row, 8)
	for i := range rows {
		rows[i] = neutralRow()
	}
	if got := emitted(rows); len(got) != 0 {
		t.Errorf("全中性 K 线不该产生任何信号，实际：%v", got)
	}
}

// TestHasDataRejectsNonPositive 直接钉住缺失判据本身。
func TestHasDataRejectsNonPositive(t *testing.T) {
	if !hasData(1, 2.5, 1e9) {
		t.Error("全为正应判为有值")
	}
	for _, bad := range []float64{0, -1, -1e-9} {
		if hasData(1, bad) {
			t.Errorf("含 %v 时应判为无值（0 是 COALESCE 补出来的空值）", bad)
		}
	}
	if hasData() {
		t.Error("没有任何列时应判为无值")
	}
}

// TestPviNviSpreadReportsGap 钉住 pviNviSpread 的 ok 语义：窗口任一端缺值就必须
// 报不可用，否则调用方会拿一个假差值去比大小。
func TestPviNviSpreadReportsGap(t *testing.T) {
	latest := with(func(r *row) { r.PVI, r.NVI = 1200, 1000 })
	base := with(func(r *row) { r.PVI, r.NVI = 1000, 1000 })
	e := pviNviSpread(latest, base)
	if !e.ok || e.dPVI != 200 || e.dNVI != 0 || e.spread != 200 {
		t.Errorf("正常窗口算错：%+v", e)
	}
	if e := pviNviSpread(zeroAt([]row{latest, base}, 0, func(r *row) { r.PVI = 0 })[0], base); e.ok {
		t.Errorf("最新端 PVI 缺失时必须 ok=false，实际：%+v", e)
	}
	if e := pviNviSpread(latest, zeroAt([]row{base}, 0, func(r *row) { r.NVI = 0 })[0]); e.ok {
		t.Errorf("基准端 NVI 缺失时必须 ok=false，实际：%+v", e)
	}
}
