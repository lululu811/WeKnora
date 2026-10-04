package pattern

import (
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// 本文件是 signals.go ↔ scan.go 之间的契约闸门。
//
// 背景：Description() 的正文是**逐字**交给 LLM 的，所以它上面写一条、代码里没
// 实现，等于告诉模型"这里有个读数你调一下就有"——模型拿不到，只能自己编一个。
// 这个包历史上就是这么坏过：描述里广告过 PSAR翻转 / VWAP突破 / 量价齐升 /
// 量价背离 四个 readout，前两个是列都查出来了但 detectSignals 一行都没读。
//
// 人肉对清单必然对不上，所以这里改成机器对：
//
//	1. 覆盖   —— declaredSignalNames 里每一条都必须能被某组构造数据真正打出来
//	2. 完整性 —— detectSignals 打出来的每一条都必须在清单里
//	3. 广告   —— 清单里每一条都必须出现在 Description() 正文里
//	4. 数量   —— 清单条数被钉死，删/改名会立刻红
//
// 四条都不依赖正则：比对的是代码里那个字符串本身。

// neutralRow 是一根不触发任何信号的基准 K 线。
//
// 只有 RSI6 和 MFI 必须显式给成中性值：这两条的判据是 <20 / >20，而 0 落在超买
// 侧，基准行会凭空报两条超卖。其余列保持零值 = "缺数据"，正好也顺带验证了缺数据
// 不发信号这件事本身。
func neutralRow() row {
	return row{Date: "2026-09-19", RSI6: 50, MFI: 50}
}

// with 造一根基准 K 线并覆盖若干字段。
func with(f func(r *row)) row {
	r := neutralRow()
	f(&r)
	return r
}

// emitted 返回 detectSignals 打出来的信号名（去重后升序），用来做集合比对。
func emitted(rows []row) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range detectSignals(rows) {
		if !seen[s.Name] {
			seen[s.Name] = true
			out = append(out, s.Name)
		}
	}
	sort.Strings(out)
	return out
}

// fixture 是"想触发某个信号"的一组 K 线。
type fixture struct {
	want string
	rows []row
}

// coverageFixtures 为 declaredSignalNames 里的**每一条**信号各提供一组能把它打
// 出来的 K 线。少一条覆盖，测试 2（完整性）不会发现，但测试 1（覆盖）会发现——
// 清单里躺着一条永远打不出来的信号，那和"描述里广告了却没实现"是同一种病。

func coverageFixtures() []fixture {
	var fs []fixture
	add := func(want string, rows ...row) {
		fs = append(fs, fixture{want: want, rows: rows})
	}

	// ── Momentum ──
	add("MACD金叉", with(func(r *row) { r.DIF, r.DEA = 2, 1 }), with(func(r *row) { r.DIF, r.DEA = 1, 1.5 }))
	add("MACD死叉", with(func(r *row) { r.DIF, r.DEA = 1, 2 }), with(func(r *row) { r.DIF, r.DEA = 2, 1.5 }))
	add("KDJ超卖金叉", with(func(r *row) { r.K, r.D, r.J = 15, 10, 20 }), with(func(r *row) { r.K, r.D, r.J = 12, 13, 10 }))
	add("KDJ超买死叉", with(func(r *row) { r.K, r.D, r.J = 85, 90, 70 }), with(func(r *row) { r.K, r.D, r.J = 88, 85, 95 }))
	// J = 3K - 2D，下面两组 J 值都按这个等式算，不是随手填的。
	add("J超卖回中", with(func(r *row) { r.K, r.D, r.J = 20, 28, 4 }), with(func(r *row) { r.K, r.D, r.J = 18, 30, -6 }))
	add("J超买回中", with(func(r *row) { r.K, r.D, r.J = 50, 30, 90 }), with(func(r *row) { r.K, r.D, r.J = 90, 30, 210 }))
	add("RSI6超卖", with(func(r *row) { r.RSI6 = 15 }), with(func(r *row) { r.RSI6 = 12 }))
	add("RSI6超买", with(func(r *row) { r.RSI6 = 85 }))
	// RSI14 报的是**穿越**，所以 rows[0]（最新）必须已经回到区间内。
	add("RSI14超卖回升",
		with(func(r *row) { r.RSI14 = 32 }),
		with(func(r *row) { r.RSI14 = 25 }))
	add("RSI14超买回落",
		with(func(r *row) { r.RSI14 = 68 }),
		with(func(r *row) { r.RSI14 = 75 }))
	add("Stochastic超卖金叉", with(func(r *row) { r.StochK, r.StochD = 15, 10 }), with(func(r *row) { r.StochK, r.StochD = 12, 13 }))
	add("Stochastic超买死叉", with(func(r *row) { r.StochK, r.StochD = 85, 90 }), with(func(r *row) { r.StochK, r.StochD = 88, 85 }))
	add("CCI超卖", with(func(r *row) { r.CCI = -150 }))
	add("CCI超买", with(func(r *row) { r.CCI = 150 }))
	add("Williams%R超卖", with(func(r *row) { r.WillR = -85 }))
	add("Williams%R超买", with(func(r *row) { r.WillR = -10 }))
	add("MFI超卖", with(func(r *row) { r.MFI = 15 }))
	add("MFI超买", with(func(r *row) { r.MFI = 85 }))
	add("ER转高效", with(func(r *row) { r.ER = 0.45 }), with(func(r *row) { r.ER = 0.20 }))
	add("ER转低效", with(func(r *row) { r.ER = 0.20 }), with(func(r *row) { r.ER = 0.45 }))
	// MACD 动能衰减需要**四根**同号递减的柱（rows 倒序，rows[0] 最新=最窄那根）。
	add("MACD动能衰减",
		with(func(r *row) { r.MACDHist = 1.0 }),
		with(func(r *row) { r.MACDHist = 2.0 }),
		with(func(r *row) { r.MACDHist = 3.0 }),
		with(func(r *row) { r.MACDHist = 4.0 }))

	// ── Trend ──
	add("Supertrend翻转", with(func(r *row) { r.STDir, r.STVal = 1, 10.5 }), with(func(r *row) { r.STDir = -1 }))
	add("Supertrend翻空", with(func(r *row) { r.STDir, r.STVal = -1, 9.5 }), with(func(r *row) { r.STDir = 1 }))
	add("PSAR翻多", with(func(r *row) { r.Close, r.PSAR = 21.4, 20.1 }), with(func(r *row) { r.Close, r.PSAR = 19.5, 20.8 }))
	add("PSAR翻空", with(func(r *row) { r.Close, r.PSAR = 18.0, 19.2 }), with(func(r *row) { r.Close, r.PSAR = 19.8, 18.5 }))
	add("ADX多头趋势", with(func(r *row) { r.ADX, r.DIPlus, r.DIMinus = 30, 28, 12 }))
	add("ADX空头趋势", with(func(r *row) { r.ADX, r.DIPlus, r.DIMinus = 30, 10, 25 }))
	add("DI金叉", with(func(r *row) { r.DIPlus, r.DIMinus = 28, 12 }), with(func(r *row) { r.DIPlus, r.DIMinus = 12, 20 }))
	add("DI死叉", with(func(r *row) { r.DIPlus, r.DIMinus = 10, 25 }), with(func(r *row) { r.DIPlus, r.DIMinus = 20, 12 }))
	add("Aroon多头排列", with(func(r *row) { r.AroonUp, r.AroonDown = 80, 20 }))
	add("Aroon空头排列", with(func(r *row) { r.AroonUp, r.AroonDown = 20, 80 }))
	add("Vortex金叉", with(func(r *row) { r.VIPlus, r.VIMinus = 1.2, 0.8 }), with(func(r *row) { r.VIPlus, r.VIMinus = 0.7, 0.9 }))
	add("Vortex死叉", with(func(r *row) { r.VIPlus, r.VIMinus = 0.8, 1.2 }), with(func(r *row) { r.VIPlus, r.VIMinus = 0.9, 0.7 }))
	// zettaranc 双线。Close 必须给 —— 信号描述里带收盘价，且判定要过 hasData。
	add("白线金叉黄线",
		with(func(r *row) { r.Close, r.ZtrWhite, r.ZtrYellow = 20.5, 20.3, 20.0 }),
		with(func(r *row) { r.Close, r.ZtrWhite, r.ZtrYellow = 19.6, 19.5, 20.0 }))
	add("白线死叉黄线",
		with(func(r *row) { r.Close, r.ZtrWhite, r.ZtrYellow = 19.5, 19.8, 20.0 }),
		with(func(r *row) { r.Close, r.ZtrWhite, r.ZtrYellow = 20.4, 20.2, 20.0 }))
	// 黄线穿越要两根：rows[0] 已经在黄线上方、rows[1] 还在下方。
	add("收盘上穿黄线",
		with(func(r *row) { r.Close, r.ZtrYellow = 20.5, 20.0 }),
		with(func(r *row) { r.Close, r.ZtrYellow = 19.5, 20.0 }))
	add("收盘下穿黄线",
		with(func(r *row) { r.Close, r.ZtrYellow = 19.5, 20.0 }),
		with(func(r *row) { r.Close, r.ZtrYellow = 20.5, 20.0 }))
	add("CHOP进入震荡", with(func(r *row) { r.Chop = 70 }), with(func(r *row) { r.Chop = 55 }))
	add("CHOP重回趋势", with(func(r *row) { r.Chop = 30 }), with(func(r *row) { r.Chop = 50 }))
	add("KC中轨多头带",
		with(func(r *row) { r.Close, r.KCMid = 20.2, 20.0 }),
		with(func(r *row) { r.Close, r.KCMid = 19.6, 19.8 }))
	add("KC中轨空头带",
		with(func(r *row) { r.Close, r.KCMid = 19.6, 20.0 }),
		with(func(r *row) { r.Close, r.KCMid = 21.0, 20.5 }))

	// ── Volatility ──
	// 布林带收口：最新宽度 2，前 4 天都是 10，5 日均值 8.4，2 < 8.4/2。
	add("布林带收口",
		with(func(r *row) { r.BBWidth = 2 }),
		with(func(r *row) { r.BBWidth = 10 }), with(func(r *row) { r.BBWidth = 10 }),
		with(func(r *row) { r.BBWidth = 10 }), with(func(r *row) { r.BBWidth = 10 }))
	add("布林中轨收复",
		with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }),
		with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }))
	add("布林中轨跌破",
		with(func(r *row) { r.Close, r.BBMid = 19.8, 20.0 }),
		with(func(r *row) { r.Close, r.BBMid = 20.2, 20.0 }))
	// Donchian 必须跟**上一根**的轨比：dc_upper 是含当天的 20 日最高价，拿它跟
	// 当天收盘比恒为假（详见 signals.go 的说明）。所以轨值放在 rows[1] 上。
	add("Donchian上轨突破",
		with(func(r *row) { r.Close = 21.5 }),
		with(func(r *row) { r.Close, r.DCUpper = 20.5, 21.0 }))
	add("Donchian下轨跌破",
		with(func(r *row) { r.Close = 18.5 }),
		with(func(r *row) { r.Close, r.DCLower = 19.5, 19.0 }))
	// 挤压：布林带完全落在 Keltner 通道内。前一日给一组收得更开的带子表示"没在挤压"。
	add("Keltner挤压",
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 20.5, 19.5, 21.0, 19.0 }),
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 21.0, 19.0, 20.0, 20.0 }))
	add("Keltner挤压向上突破",
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower, r.Close = 20.5, 19.5, 21.0, 19.0, 21.5 }),
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 20.5, 19.5, 21.0, 19.0 }))
	add("Keltner挤压向下突破",
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower, r.Close = 20.5, 19.5, 21.0, 19.0, 18.5 }),
		with(func(r *row) { r.BBUpper, r.BBLower, r.KCUpper, r.KCLower = 20.5, 19.5, 21.0, 19.0 }))
	add("NATR进入高波动档位", with(func(r *row) { r.NATR = 6.5 }), with(func(r *row) { r.NATR = 4.0 }))
	add("NATR退出高波动档位", with(func(r *row) { r.NATR = 4.0 }), with(func(r *row) { r.NATR = 6.5 }))
	add("ATR扩张",
		with(func(r *row) { r.ATR = 5 }),
		with(func(r *row) { r.ATR = 2 }), with(func(r *row) { r.ATR = 2 }),
		with(func(r *row) { r.ATR = 2 }), with(func(r *row) { r.ATR = 2 }))

	// ── Volume ──
	add("CMF资金流入", with(func(r *row) { r.CMF = 0.3 }))
	// VWAP 穿越：rows[0] 是最新那一根，必须已经站到线的另一侧。
	add("上穿VWAP",
		with(func(r *row) { r.Close, r.VWAP = 20.5, 20.0 }),
		with(func(r *row) { r.Close, r.VWAP = 19.5, 20.0 }))
	add("下穿VWAP",
		with(func(r *row) { r.Close, r.VWAP = 19.5, 20.0 }),
		with(func(r *row) { r.Close, r.VWAP = 20.5, 20.0 }))
	add("CMF资金流出", with(func(r *row) { r.CMF = -0.3 }))
	// 价升但 PVT 降：5 日窗口（rows[0] 与 rows[5]）。
	add("价量背离",
		with(func(r *row) { r.PVT, r.Close = 900, 11.0 }),
		neutralRow(), neutralRow(), neutralRow(), neutralRow(),
		with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }))
	add("缩量下跌吸筹",
		with(func(r *row) { r.PVT, r.Close = 1200, 9.0 }),
		neutralRow(), neutralRow(), neutralRow(), neutralRow(),
		with(func(r *row) { r.PVT, r.Close = 1000, 10.0 }))
	// PVI 相对 NVI 由弱转强：最新窗口 dPVI=200/dNVI=0，前一窗口反过来。
	add("PVI强于NVI吸筹",
		with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
		with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
		neutralRow(), neutralRow(), neutralRow(),
		with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
		with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }))
	add("NVI强于PVI派发",
		with(func(r *row) { r.PVI, r.NVI = 1000, 1200 }),
		with(func(r *row) { r.PVI, r.NVI = 1200, 1000 }),
		neutralRow(), neutralRow(), neutralRow(),
		with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }),
		with(func(r *row) { r.PVI, r.NVI = 1000, 1000 }))

	// 单针下20：3日百分位 15 (<=20) + 21日百分位 70 (>=60)
	add("单针下20", with(func(r *row) { r.ZtrRSLRank15, r.ZtrRSLRank105 = 15, 70 }))

	// ── Statistics ──
	add("Z-Score超卖", with(func(r *row) { r.ZScore = -2.5 }))
	add("Z-Score超买", with(func(r *row) { r.ZScore = 2.5 }))
	add("线性回归上升", with(func(r *row) { r.LinSlope = 1.5 }))
	add("线性回归下降", with(func(r *row) { r.LinSlope = -1.5 }))

	// ── Candlestick ──
	for _, c := range []struct {
		name string
		set  func(r *row)
	}{
		{"Morning Star晨星", func(r *row) { r.CdlMorningStar = 1 }},
		{"Evening Star暮星", func(r *row) { r.CdlEveningStar = 1 }},
		{"Hammer锤子线", func(r *row) { r.CdlHammer = 1 }},
		{"Shooting Star流星", func(r *row) { r.CdlShootingStar = 1 }},
		{"Doji十字星", func(r *row) { r.CdlDoji = 1 }},
		{"Engulfing吞没", func(r *row) { r.CdlEngulfing = 1 }},
		{"Harami孕线", func(r *row) { r.CdlHarami = 1 }},
		{"Piercing刺透", func(r *row) { r.CdlPiercing = 1 }},
		{"Dark Cloud乌云盖顶", func(r *row) { r.CdlDarkCloud = 1 }},
		{"Three White Soldiers三白兵", func(r *row) { r.Cdl3WhiteSold = 1 }},
		{"Three Black Crows三乌鸦", func(r *row) { r.Cdl3BlackCrows = 1 }},
	} {
		add(c.name, with(c.set))
	}

	return fs
}

// TestDeclaredSignalCountPinned 把信号总数钉死。
//
// 这是"信号集不能悄悄缩水"的那道闸：任何一条信号被删掉或改名，declaredSignalNames
// 就会和这里的数字对不上，测试会明确报出"改了什么"，而不是让人自己去猜少了哪条。
//
// 66 = 46（原有）+ 20（本次新增，见 signals.go 的 declaredSignalNames 分组）。
//
// 唯一一次改名也记在这里：Williams 的信号原来叫 "Williams%%R超卖" / "Williams%%R超买"
// ——%% 只在 fmt.Sprintf 的格式串里才是转义，而 Name 是普通字符串字面量，所以工具
// 一直返回带两个百分号的名字，scan.go 的描述里写的却是一个。已改为 "Williams%R超卖"
// / "Williams%R超买"。名字没变、数量没变，只是恢复了与描述一致。
// 66 → 71：补上三个此前"查出来却没人读"的字段对应的信号。
//
//	RSI14超卖回升 / RSI14超买回落 —— 用 14 日周期报**穿越**而不是状态；
//	MACD动能衰减               —— 柱状线连续 3 根同向收窄；
//	上穿VWAP / 下穿VWAP        —— 收盘价穿越当日建仓成本线。
//
// 三组都补了正向 fixture（各自能被打出来）和一个全 0 负向闸门。
//
// 71 → 75：接入 zettaranc 自研双线（BBI 牵牛绳一并取数备用），补四条**转折**信号。
//
//	白线金叉黄线 / 白线死叉黄线 —— 快线 DEMA10 穿越慢线 LONGBBI(14/28/57/114)
//	收盘上穿黄线 / 收盘下穿黄线 —— 收盘价穿越慢线，即"左侧转右侧"的那一步
//
// 刻意**不**发两类状态信号：其一 "close > 黄线"，400 只 × 400 根实测 48.13% 的
// bar 成立；其二 "黄线斜率向上"，实测 **45.92%**（频率审计判 noisy），因为 114 日
// 均线的日间差值微乎其微、符号在零附近随机翻转——**慢线的斜率方向是状态不是事件**。
// 这与本文件 Choppiness 段落写下的原则一致。
//
// 四条都配了正向 fixture 和 COALESCE 补 0 的负向闸门 —— ztr_* 三列有约 0.4%
// 的 NULL 会补成 0，0 比任何一条线都低，不挡就是给缺列的 K 线凭空发金叉。
//
// 75 → 76：接入 a-stock 的 p9 单针下 20（RSL 改名前后的列名对齐）。实测 8.09%，informative。
//
// 同一批里原本还实现了 p6 长安三件套，**已移出 signals.go**，理由与它的 verdict 无关
// —— 它确实会触发：**全库 5,572 只 × 十年 10,349,853 根 bar 上，七条条件同时成立
// 恰好 117 次**（0.0011%，约每 20 个交易日全市场一次）。1/88,000 的 bar 率不是
// "每根 K 报一次状态"该待的地方，而 run_signal_audit.sh 的 300 只定距样本只覆盖
// 全库的 2.7%，期望 3.2 次，因此给出 `dead`（λ=3.2 时 P(0)≈4%，完全合理）。
// 它与 shrink_pullback（70 根窗口）、double_gun（15 根窗口，超出审计 7 根窗口）
// 同属形态层，在 python-service/zettaranc/volume.py 实现。
//
// 顺带记下一个真实的坑：第一版守卫写成 hasData(..., latest.J)，而 hasData 要求每个
// 值 > 0、主判据却是 J < -13，信号因此**永远打不出来**。那个 `dead` 是我的 bug。
// 结论：`dead` 判定必须先确认信号在结构上可能触发，再谈阈值松紧。
const wantDeclaredSignalCount = 76

func TestDeclaredSignalCountPinned(t *testing.T) {
	if got := len(declaredSignalNames); got != wantDeclaredSignalCount {
		t.Errorf("信号清单条数 = %d，期望 %d。\n"+
			"如果你刚删了或改名了某条信号：请同步改 declaredSignalNames、scan.go 的 Description()，"+
			"以及本文件里的这个数字与信号集未变的说明——这正是本测试存在的意义，"+
			"不能让信号集在没人注意到的情况下缩水。\n当前清单：%v",
			got, wantDeclaredSignalCount, declaredSignalNames)
	}
}

func TestDeclaredSignalNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, n := range declaredSignalNames {
		if seen[n] {
			t.Errorf("信号名 %q 在 declaredSignalNames 里出现两次", n)
		}
		seen[n] = true
	}
}

// TestEveryDeclaredSignalIsEmitted 覆盖闸门：清单里每一条都要能被真实打出来。
//
// 清单里躺着一条代码永远打不出来的信号 = 描述里广告了却没实现。
func TestEveryDeclaredSignalIsEmitted(t *testing.T) {
	reachable := map[string]bool{}
	for _, f := range coverageFixtures() {
		got := emitted(f.rows)
		if !contains(got, f.want) {
			t.Errorf("信号 %q 声明在清单里，但这组 K 线没打出来。实际打出：%v\n"+
				"要么判定条件被改坏了，要么这条信号已经被删了——两种都必须在这里露出来。",
				f.want, got)
		}
		reachable[f.want] = true
	}
	for _, n := range declaredSignalNames {
		if !reachable[n] {
			t.Errorf("信号 %q 在 declaredSignalNames 里，却没有任何一组 K 线能触发它"+
				"（要么是死信号，要么是覆盖数据漏了）", n)
		}
	}
}

// TestFiredSignalsAreAllDeclared 完整性闸门：新加信号忘了登记就会在这里红。
func TestFiredSignalsAreAllDeclared(t *testing.T) {
	declared := map[string]bool{}
	for _, n := range declaredSignalNames {
		declared[n] = true
	}
	for _, f := range coverageFixtures() {
		for _, n := range emitted(f.rows) {
			if !declared[n] {
				t.Errorf("detectSignals 打出了未登记的信号 %q。"+
					"请把它加进 declaredSignalNames，并在 scan.go 的 Description() 里如实广告"+
					"——描述没写而代码有，模型就会以为没有而错过它。", n)
			}
		}
	}
}

// TestDescriptionAdvertisesExactlyTheDeclaredSet 广告闸门：描述与代码逐条对齐。
//
// 不断言"描述里没有多余的信号"——那需要拿描述去解析正文，脆弱且易碎。真正会出事
// 的是反向：代码里有而描述没写（模型会错过它），这一条能抓住。
//
// 已知的一处天然漏网：Keltner挤压 是 Keltner挤压向上突破 / Keltner挤压向下突破 的
// 子串，所以后两条在正文里出现就顺带满足了它的检查。这不影响任何一条信号的
// 真假判定，只是这条的"必须出现"比看上去弱一点。
func TestDescriptionAdvertisesExactlyTheDeclaredSet(t *testing.T) {
	desc := NewPatternScanTool(nil).Description()
	for _, n := range declaredSignalNames {
		if !strings.Contains(desc, n) {
			t.Errorf("信号 %q 已实现并登记在 declaredSignalNames，但没有出现在 scan.go 的 "+
				"Description() 里。Description 是逐字交给 LLM 的：代码里有、描述里没有，"+
				"模型就会以为这个读数不存在而跳过它。", n)
		}
	}
	// 描述里的总数用 fmt 拼自清单长度，所以它永远等于真实条数；这里钉一句，防止
	// 有人把 %d 换成一个写死的数字。
	if !strings.Contains(desc, fmt.Sprintf("综合检测 %d 种买卖形态", len(declaredSignalNames))) {
		t.Errorf("Description() 里的信号总数应当由 len(declaredSignalNames) 拼出"+
			"（\"综合检测 %d 种买卖形态\"），实际正文：%.120s", len(declaredSignalNames), desc)
	}
}

// TestStillNotImplementedSignalsAreNotAdvertised 是上一条的反向提醒，写死当前状态。
//
// VWAP 和 OBV 被 query.go 查出来映射进 row，但 detectSignals 至今没读；描述里也就
// 不能出现它们。哪天真的实现了，把这里的注释和期望改掉即可。
func TestStillNotImplementedSignalsAreNotAdvertised(t *testing.T) {
	desc := NewPatternScanTool(nil).Description()
	for _, absent := range []string{"VWAP突破", "量价齐升"} {
		if strings.Contains(desc, absent) {
			t.Errorf("描述里出现了 %q，但 signals.go 并没有实现这个读数（vwap / obv 查出来了却没读）。"+
				"要么去实现它，要么从描述里删掉——广告一个拿不到的读数，模型只会自己编。", absent)
		}
	}
}

// TestZeroValuedColumnsNeverFire 负向闸门：COALESCE 补出来的 0 不是行情。
//
// 覆盖闸门（TestEveryDeclaredSignalIsEmitted）只证明"该发的能发"，证明不了
// "不该发的没发"。后者才是今晚栽跟头的那一类：Williams%R 拿 0 当读数，于是每张
// 缺该列的票每天都报一条超买（见 signals.go 里 WillR < 0 那个守卫的注释）。
//
// 这里对每个新信号灌一份全 0 的 K 线，要求一条都不许发。
func TestZeroValuedColumnsNeverFire(t *testing.T) {
	zeroed := []row{
		with(func(r *row) { r.MACDHist, r.RSI14, r.VWAP, r.Close = 0, 0, 0, 0 }),
		with(func(r *row) { r.MACDHist, r.RSI14, r.VWAP, r.Close = 0, 0, 0, 0 }),
		with(func(r *row) { r.MACDHist, r.RSI14, r.VWAP, r.Close = 0, 0, 0, 0 }),
		with(func(r *row) { r.MACDHist, r.RSI14, r.VWAP, r.Close = 0, 0, 0, 0 }),
	}
	for _, s := range detectSignals(zeroed) {
		switch s.Name {
		case "MACD动能衰减", "RSI14超卖回升", "RSI14超买回落", "上穿VWAP", "下穿VWAP":
			t.Errorf("全 0 的 K 线（= query.go 的 COALESCE 补出来的缺列）打出了 %q（%s）。"+
				"0 是数据缺口不是行情：拿它做判断等于凭空造信号。", s.Name, s.Desc)
		}
	}
}

// TestNoSignalEmittedTwiceOnOneBar 防的是一整类问题，不是某一个信号。
//
// `Donchian下轨跌破` 曾经被两个地方同时发出：一处用 prev.DCLower（与上轨对称），
// 一处用 latest.DCLower。上轨含当天而下轨不含当天这个上游不对称，让两种写法都
// "看起来对"，于是同一根 K 线报两遍。后果不是难看——**任何按信号计数算出来的
// 统计都会被放大一倍**，而信号频率正是用来判断哪个信号是噪音的依据。
//
// 同名信号在一次 detectSignals 里出现两次，一定是 bug：要么是重复实现，要么是
// 两条本该同名的信号被写成了同一个名字。两种都该在这里露出来。
func TestNoSignalEmittedTwiceOnOneBar(t *testing.T) {
	// 覆盖矩阵刻意挑几组能让多个分支同时成立的 K 线：上下轨同时破、
	// 布林中轨收复 + Keltner 向上突破 + 极值类同柱齐发等等。
	fixtures := [][]row{
		{
			with(func(r *row) { r.Close = 21.5 }),
			with(func(r *row) { r.Close, r.DCUpper, r.DCLower = 20.5, 21.0, 19.0 }),
		},
		{
			with(func(r *row) { r.Close = 18.5 }),
			with(func(r *row) { r.Close, r.DCUpper, r.DCLower = 19.5, 19.0, 20.0 }),
		},
		{
			with(func(r *row) {
				r.Close, r.BBUpper, r.BBLower = 21.5, 21.0, 19.0
				r.KCUpper, r.KCLower, r.BBMid = 22.0, 18.5, 20.0
			}),
			with(func(r *row) {
				r.Close, r.BBUpper, r.BBLower = 19.5, 20.5, 19.5
				r.KCUpper, r.KCLower, r.BBMid = 21.0, 19.0, 20.0
			}),
		},
		{
			with(func(r *row) {
				r.Close, r.DIF, r.DEA, r.StochK, r.StochD = 20.5, 1.2, 1.0, 15, 18
				r.K, r.D, r.MACDHist = 15, 20, -0.5
			}),
			with(func(r *row) {
				r.Close, r.DIF, r.DEA, r.StochK, r.StochD = 19.0, 0.8, 1.0, 12, 13
				r.K, r.D, r.MACDHist = 20, 15, -1.0
			}),
		},
	}
	for i, rows := range fixtures {
		counts := map[string]int{}
		for _, s := range detectSignals(rows) {
			counts[s.Name]++
		}
		for name, n := range counts {
			if n > 1 {
				t.Errorf("第 %d 组 K 线上信号 %q 被发出了 %d 次。\n"+
					"同一根 K 线的同名信号只能有一条——重复实现会让所有按信号计数的统计翻倍。",
					i+1, name, n)
			}
		}
	}
}

// TestDonchianUpperCannotBeBeatenBySameBar 是本文件最该存在的一个测试。
//
// dc_upper 是**含当天**的 20 日最高价，所以数据本身满足
// `DCUpper >= high >= Close`。旧判定 `latest.Close > latest.DCUpper` 因此恒为假——
// 一条广告在工具描述里、却永远打不出来的信号。
//
// 这条不变式以前没有任何测试守：coverage fixture 把 DCUpper 编成任意数（21.0），
// 不满足数据自己的约束，于是覆盖闸门是绿的、信号实际是死的。**fixture 绕开了
// 不变式，绿灯就毫无意义**——这正是 fixture 式测试的能力边界。
func TestDonchianUpperCannotBeBeatenBySameBar(t *testing.T) {
	// 取一批真实数据里成立的不变式：上轨 >= 收盘价。旧写法在这个前提下不可满足。
	// row 里没有 High —— 信号引擎只拿到收盘价。上轨 >= 收盘价这条不变式已经足够
	// 证伪旧写法，不必引入引擎并不持有的字段。
	rows := []row{
		with(func(r *row) { r.Close, r.DCUpper = 1235.58, 1338.86 }),
		with(func(r *row) { r.Close, r.DCUpper = 1243.88, 1338.86 }),
		with(func(r *row) { r.Close, r.DCUpper = 1237.00, 1338.86 }),
	}
	for _, s := range detectSignals(rows) {
		if s.Name == "Donchian上轨突破" {
			t.Errorf("在 DCUpper >= Close 恒成立的数据上打出了 %q（%s）。"+
				"若这条触发，说明比较用错了同一根的轨——必须比 prev.DCUpper。", s.Name, s.Desc)
		}
	}
	// 反向确认：改比 prev.DCUpper 之后，条件是**可满足**的。
	fires := []row{
		with(func(r *row) { r.Close = 1400.0 }),
		with(func(r *row) { r.Close, r.DCUpper = 1300.0, 1338.86 }),
	}
	if !contains(emitted(fires), "Donchian上轨突破") {
		t.Errorf("收盘 1400 高于前一根的上轨 1338.86，仍未打出 Donchian上轨突破——判定写坏了")
	}
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

// rowGuardRe 抓 `len(rows) >= N` 里的 N。带空白容忍，因为写法不统一。
var rowGuardRe = regexp.MustCompile(`len\(rows\)\s*>=\s*(\d+)`)

// deepestRowGuard 返回 signals.go 里最深的一处 `len(rows) >= N`。
//
// 先剥掉行注释再扫：注释里写"需要 70 根"这类说明不该把窗口算进去，
// 那是解释文字不是守卫 —— 与 query_contract_test.go 对 COALESCE 列名的
// 同一处理（那条测试最初就是被注释里的字面量 `COALESCE(col, 0)` 绊倒的）。
func deepestRowGuard(t *testing.T) int {
	t.Helper()
	src, err := os.ReadFile("signals.go")
	if err != nil {
		t.Fatalf("读 signals.go 失败：%v", err)
	}
	text := stripLineComments(string(src))
	deepest := 0
	for _, m := range rowGuardRe.FindAllStringSubmatch(text, -1) {
		if n, err := strconv.Atoi(m[1]); err == nil && n > deepest {
			deepest = n
		}
	}
	if deepest == 0 {
		t.Fatal("signals.go 里一处 `len(rows) >= N` 都没有 —— 正则失效或代码大改，" +
			"这个测试会静默放行，必须先确认它还能扫到东西")
	}
	return deepest
}

// TestAuditWindowCoversDeepestGuard 守住 auditWinSize 与代码同步。
//
// 失配的后果是**静默**的：审计喂 N 根窗口，而某条信号要 N+ 根，于是它每根
// bar 都因历史不足而不触发，verdict 报 `dead`。看到这个结果的人会去查"是不是
// 阈值太严"，而真因是审计没喂够 —— 方向完全错。
func TestAuditWindowCoversDeepestGuard(t *testing.T) {
	deepest := deepestRowGuard(t)
	if deepest > auditWinSize {
		t.Errorf("signals.go 里最深需要 %d 根，auditWinSize 只有 %d —— "+
			"需要更多历史的信号在审计里会永远不触发，verdict 报 dead。\n"+
			"请把 auditWinSize 调到 %d **并重跑全量审计**（分母会从 Σ(N_i-6) "+
			"变成 Σ(N_i-%d)，评估到的 bar 数大幅减少，别拿旧结论沿用）。",
			deepest, auditWinSize, deepest, deepest-1)
	}
	if auditWinSize > deepest {
		t.Logf("auditWinSize=%d 比代码里最深的守卫 %d 还大：多喂的历史没人用，"+
			"却让分母从 Σ(N_i-%d) 缩到 Σ(N_i-%d)，白白少评估一批 bar",
			auditWinSize, deepest, deepest-1, auditWinSize-1)
	}
}

// auditWinSize 定义在本文件（无 build tag）而不是审计测试文件里：
// 下面那条守卫测试要在普通 CI 里跑，必须拿得到这个数；同包的带 tag 文件
// 编译时也能看到它，所以审计本身照常用。
const (
	// auditWinSize 是 detectSignals 里任何分支要求的最深 `len(rows) >= N`。
	// 当前 signals.go 最深到 7。
	//
	// 这个数字由 TestAuditWindowCoversDeepestGuard 守着 —— 有人加了一条需要
	// 更多历史的信号而忘了改它，那条信号会在**每根** bar 上因为历史不足而不触发，
	// 审计报 `dead`，看起来像"阈值太严"，实际是审计自己没喂够历史。
	//
	// ⚠️ **不要为了让某个信号"有希望过审"而调大它。** 窗口 N 的分母是
	// `Σ max(0, N_i - (N-1))`：N=7 时评估 88% 的 bar，N=70 时只剩 6.6%，
	// 样本少一个数量级，77 条真实信号里会凭空冒出一批 `dead`。
	// 窗口只该在**真的有信号需要更深历史**时跟着长，而且必须同时重跑全量。
	//
	// 需要更长历史的形态类策略（缩量回踩 70 根 / 双枪 15 根 / 长安 3 根）
	// 不在本引擎里 —— 它们在 python-service 的形态层，由选股池按 `min_bars`
	// 单独取窗口，频率用全库实测（10,349,853 根 bar）而不是这个逐 bar 审计来量。
	auditWinSize = 7
)
