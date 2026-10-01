package pattern

import (
	"fmt"
	"math"
)

// Signal represents a detected pattern.
type Signal struct {
	Category string  `json:"category"` // buy/sell/trend/volatility/volume/candle
	Name     string  `json:"name"`
	Signal   string  `json:"signal"`   // bullish/bearish/neutral
	Strength float64 `json:"strength"` // 0-1
	Date     string  `json:"date"`
	Desc     string  `json:"desc"`
}

// detectSignals scans rows and returns all detected signals.
// rows[0] = latest day, rows[1] = previous day, etc.
func detectSignals(rows []row) []Signal {
	var signals []Signal
	if len(rows) == 0 {
		return signals
	}

	latest := rows[0]
	var prev row
	if len(rows) > 1 {
		prev = rows[1]
	}

	// ── Momentum Buy Signals ──

	// MACD 金叉: DIF 从下方穿越 DEA
	if len(rows) >= 2 && prev.DIF <= prev.DEA && latest.DIF > latest.DEA {
		signals = append(signals, Signal{"buy", "MACD金叉", "bullish", 0.8, latest.Date,
			fmt.Sprintf("DIF(%.2f) 上穿 DEA(%.2f)", latest.DIF, latest.DEA)})
	}
	// MACD 零轴上金叉（更强）
	if len(rows) >= 2 && prev.DIF <= prev.DEA && latest.DIF > latest.DEA && latest.DIF > 0 {
		signals[len(signals)-1].Strength = 0.9
		signals[len(signals)-1].Desc += "（零轴上方，强势金叉）"
	}

	// MACD 死叉
	if len(rows) >= 2 && prev.DIF >= prev.DEA && latest.DIF < latest.DEA {
		signals = append(signals, Signal{"sell", "MACD死叉", "bearish", 0.8, latest.Date,
			fmt.Sprintf("DIF(%.2f) 下穿 DEA(%.2f)", latest.DIF, latest.DEA)})
	}

	// MACD 柱状线动能衰减
	//
	// 柱状线 macd_hist = (DIF - DEA) × 2，它跟 DIF/DEA 的关系是恒等的：柱为正
	// 当且仅当 DIF > DEA，也就是 MACD 金叉。所以"柱翻正/翻负"跟上面的金叉死叉
	// 是同一件事，发出来就是纯噪音。
	//
	// 柱真正多给的信息是**幅度**：同号的情况下，柱子越短说明这一段推力越接近耗尽。
	// 价格还在创新高、柱子却连着三根变短，就是最典型的动能背离前兆——而金叉死叉
	// 那一刻它还什么都没说。所以这里报的是幅度衰减，不报方向翻转。
	//
	// 需要连续三根同号递减（rows[0..3]，倒序），两天就判衰减太容易触发。
	// direction 给 neutral：它是对**当前这波**的降温提示，不是多空判断，也不该
	// 在 summarizeSignals 里被当成一票——跟 NATR 同样处理。
	//
	// 0 的处理：柱为 0 时既可能是真实的 DIF==DEA（本来就该报不了衰减），也可能是
	// COALESCE 补的空值，两种情况 abs 都是 0，递减条件都不成立，所以 hasData 在
	// 这里不是必需的；但仍然加上，免得哪天有人把阈值改成 >0。
	if len(rows) >= 4 {
		h0, h1, h2, h3 := latest.MACDHist, rows[1].MACDHist, rows[2].MACDHist, rows[3].MACDHist
		if hasData(h0, h1, h2, h3) && h0*h1 > 0 && h1*h2 > 0 && h2*h3 > 0 &&
			absVal(h0) < absVal(h1) && absVal(h1) < absVal(h2) && absVal(h2) < absVal(h3) {
			side := "多头"
			if h0 < 0 {
				side = "空头"
			}
			signals = append(signals, Signal{"trend", "MACD动能衰减", "neutral", 0.6, latest.Date,
				fmt.Sprintf("柱状线连续 3 根同向收窄（%+.2f→%+.2f→%+.2f→%+.2f），%s推力接近耗尽",
					h3, h2, h1, h0, side)})
		}
	}

	// KDJ 金叉（超卖区 K<20）
	if len(rows) >= 2 && prev.K <= prev.D && latest.K > latest.D && latest.K < 20 {
		signals = append(signals, Signal{"buy", "KDJ超卖金叉", "bullish", 0.85, latest.Date,
			fmt.Sprintf("K(%.1f) 上穿 D(%.1f)，超卖区", latest.K, latest.D)})
	}
	// KDJ 死叉（超买区 K>80）
	if len(rows) >= 2 && prev.K >= prev.D && latest.K < latest.D && latest.K > 80 {
		signals = append(signals, Signal{"sell", "KDJ超买死叉", "bearish", 0.85, latest.Date,
			fmt.Sprintf("K(%.1f) 下穿 D(%.1f)，超买区", latest.K, latest.D)})
	}

	// KDJ 的 J 线极值回中
	//
	// J = 3K - 2D，没有上下界（K/D 被夹在 0~100，J 可以到 -200~300），所以市场读
	// J 用的就是 0 和 100 两条线：J < 0 超卖耗尽，J > 100 超买耗尽。K/D 因为有界
	// 钝得厉害，同样的顶 J 先破 100、K/D 要晚几天才摸到 80——所以本工具判定超买
	// 超卖用 K/D（宁可晚一点也不能错），判定"这波该结束了"用 J。
	//
	// 取的是**回中**而不是"进入极值区"：进极值区只是还在加速，回中才是动能真的
	// 掉头了。跟上面 Supertrend / PSAR 只认多空翻转是同一个理由——报变化，不报状态。
	//
	// 0 的处理和价格列不同：J < 0 是合法读数，不能用 >0 判缺失。真正的判据是 J
	// 的两个输入——J 完全由 K、D 算出，而 K/D 的取值区间是 0~100，落在 0 上只可能
	// 是 COALESCE 补出来的空值。所以 K、D 都在，J 才可信；缺任一个，这一整组
	// (J/K/D) 都不可信，一根都不发。
	if len(rows) >= 2 && hasData(prev.K, prev.D, latest.K, latest.D) {
		if prev.J < 0 && latest.J >= 0 {
			signals = append(signals, Signal{"buy", "J超卖回中", "bullish", 0.8, latest.Date,
				fmt.Sprintf("J(%.1f) 由负值回到 0 上方，超卖耗尽、动能转正", latest.J)})
		}
		if prev.J > 100 && latest.J <= 100 {
			signals = append(signals, Signal{"sell", "J超买回中", "bearish", 0.8, latest.Date,
				fmt.Sprintf("J(%.1f) 从 >100 回落至 100 以内，超买耗尽", latest.J)})
		}
	}

	// Kaufman Efficiency Ratio 跨过 1/3
	//
	// ER = |10 日净变动| / 10 日逐日绝对变动之和，取值 0~1。分母是这 10 天来回走
	// 的总路程，分子是最后净走了多远，比值就是"有效行程占比"。ADX 只说趋势有多
	// 强，说不了这段趋势走得多干净：同样 +10%，直线走到和来回折返走到，ADX 可以
	// 几乎一样，但后者每次进场都会被扫损。这是 ER 唯一比 ADX 多给的信息。
	//
	// 阈值取 1/3：低于它意味着三分之二以上的行程是回撤，也就是市场上"1/3 原则"
	// 的通行说法——趋势至少要完成全程的三分之一才值得跟。只在**跨过**门槛的那一
	// 根上报：趋势质量天天在变，逐根报就是噪音。
	//
	// 0 的处理：ER = 0 要求 10 天收盘价一动不动（净变动 0、分母也 0 或非 0），那
	// 是没有行情而不是有效行情；COALESCE 补出来的空值同样正好是 0。>0 一次排掉
	// 两者。
	if len(rows) >= 2 && hasData(prev.ER, latest.ER) {
		if prev.ER < erTrendThreshold && latest.ER >= erTrendThreshold {
			signals = append(signals, Signal{"trend", "ER转高效", "bullish", 0.7, latest.Date,
				fmt.Sprintf("效率比 ER 由 %.2f 升到 %.2f（跨过 1/3），趋势质量转好", prev.ER, latest.ER)})
		}
		if prev.ER >= erTrendThreshold && latest.ER < erTrendThreshold {
			signals = append(signals, Signal{"trend", "ER转低效", "bearish", 0.7, latest.Date,
				fmt.Sprintf("效率比 ER 由 %.2f 跌到 %.2f（跌破 1/3），三成以上行程是折返", prev.ER, latest.ER)})
		}
	}

	// RSI 超卖反弹
	if latest.RSI6 < 20 && (len(rows) < 2 || rows[1].RSI6 <= latest.RSI6) {
		signals = append(signals, Signal{"buy", "RSI6超卖", "bullish", 0.7, latest.Date,
			fmt.Sprintf("RSI6=%.1f (<20 超卖区)", latest.RSI6)})
	}
	// RSI 超买回落
	if latest.RSI6 > 80 {
		signals = append(signals, Signal{"sell", "RSI6超买", "bearish", 0.7, latest.Date,
			fmt.Sprintf("RSI6=%.1f (>80 超买区)", latest.RSI6)})
	}

	// RSI14 超卖回升 / 超买回落
	//
	// RSI6 上面报的是**状态**（当前就在超买区），RSI14 这里报的是**变化**（离开
	// 超买区）。两者刻意不重复：RSI6 灵敏、天天都在极值区待着，状态报的边际信息
	// 很低；RSI14 慢、极少触极值，所以它一旦真的离开极值区，那一下才是有信息量的
	// 时点。跟 J 回中、CHOP 重回趋势是同一条准则——报变化，不报状态。
	//
	// 阈值取 70/30 而不是 RSI6 用的 80/20：6 日的 RSI 本来就比 14 日更容易跑到
	// 极端，同一个 80 用在 14 日上几乎永不触发，阈值必须跟周期匹配。
	//
	// 0 的处理：这里**不能**用 hasData。RSI 的取值区间是 0~100，0 是合法读数
	// （14 个周期里一天都没涨过），拿 0 当缺列哨兵会把这类真读数误杀。好在穿越
	// 条件自己就把 0 排除了：缺列时两根都是 0，而 0 >= 30 和 0 > 70 都不成立，
	// 两条信号都不会发。这就是"穿越"比"状态"更抗缺列的原因——它天然要求两侧
	// 各自取到不同的值。
	if len(rows) >= 2 {
		if prev.RSI14 < 30 && latest.RSI14 >= 30 {
			signals = append(signals, Signal{"buy", "RSI14超卖回升", "bullish", 0.7, latest.Date,
				fmt.Sprintf("RSI14 由 %.1f 回到 30 上方，超卖修复", latest.RSI14)})
		}
		if prev.RSI14 > 70 && latest.RSI14 <= 70 {
			signals = append(signals, Signal{"sell", "RSI14超买回落", "bearish", 0.7, latest.Date,
				fmt.Sprintf("RSI14 由 %.1f 回落至 70 以内，超买消化", latest.RSI14)})
		}
	}

	// Stochastic 金叉（超卖区）
	if len(rows) >= 2 && prev.StochK <= prev.StochD && latest.StochK > latest.StochD && latest.StochK < 20 {
		signals = append(signals, Signal{"buy", "Stochastic超卖金叉", "bullish", 0.75, latest.Date,
			fmt.Sprintf("K(%.1f) 上穿 D(%.1f)，超卖区", latest.StochK, latest.StochD)})
	}
	// Stochastic 死叉（超买区）
	if len(rows) >= 2 && prev.StochK >= prev.StochD && latest.StochK < latest.StochD && latest.StochK > 80 {
		signals = append(signals, Signal{"sell", "Stochastic超买死叉", "bearish", 0.75, latest.Date,
			fmt.Sprintf("K(%.1f) 下穿 D(%.1f)，超买区", latest.StochK, latest.StochD)})
	}

	// CCI 超卖
	if latest.CCI < -100 {
		strength := 0.6
		if latest.CCI < -200 {
			strength = 0.8
		}
		signals = append(signals, Signal{"buy", "CCI超卖", "bullish", strength, latest.Date,
			fmt.Sprintf("CCI=%.1f (<-100 超卖)", latest.CCI)})
	}
	// CCI 超买
	if latest.CCI > 100 {
		signals = append(signals, Signal{"sell", "CCI超买", "bearish", 0.6, latest.Date,
			fmt.Sprintf("CCI=%.1f (>100 超买)", latest.CCI)})
	}

	// Williams %R 超卖
	//
	// 名字里的 % 之前写成了 %%。那是给 fmt.Sprintf 用的转义写法，而 Name 是普通
	// 字符串字面量——Go 不会对字面量里的 %% 做任何处理，所以工具返回的信号名一直
	// 是 "Williams%%R超卖"，带两个百分号，跟 scan.go 描述里写的对不上。这正是下面
	// signals_contract_test.go 双向比对要抓的东西。Sprintf 里的 %% 是对的（那里确实
	// 是格式串），保持不变。
	//
	// willr 还带着一个同类的既有 bug，一并修掉：判据 `> -20` 把 COALESCE 补出来的
	// 0 也算成了超买——Williams %R 的取值区间是 -100~0，0 落在 >-20 那一侧，所以
	// 凡是 willr_14 缺列的标的，**每一天、每一根 K 线**都会凭空报一条
	// "Williams%R超买"，而且措辞还带个像模像样的读数 "=0.0"。这跟 PSAR 那次是
	// 同一个错误：拿缺数据造出一条看起来完全正常的信号。真实读数必然 < 0
	// （%R 的分子是"最高收盘 - 当前收盘"，只有收盘正好等于区间最高才等于 0，
	// 那要求当天就是最高点，现实中不会发生），所以 < 0 既能挡住空值又不误伤真值。
	if latest.WillR < 0 {
		if latest.WillR < -80 {
			signals = append(signals, Signal{"buy", "Williams%R超卖", "bullish", 0.65, latest.Date,
				fmt.Sprintf("Williams%%R=%.1f (<-80)", latest.WillR)})
		}
		if latest.WillR > -20 {
			signals = append(signals, Signal{"sell", "Williams%R超买", "bearish", 0.65, latest.Date,
				fmt.Sprintf("Williams%%R=%.1f (>−20)", latest.WillR)})
		}
	}

	// MFI 超卖/超买
	if latest.MFI < 20 {
		signals = append(signals, Signal{"buy", "MFI超卖", "bullish", 0.7, latest.Date,
			fmt.Sprintf("MFI=%.1f (<20 资金超卖)", latest.MFI)})
	}
	if latest.MFI > 80 {
		signals = append(signals, Signal{"sell", "MFI超买", "bearish", 0.7, latest.Date,
			fmt.Sprintf("MFI=%.1f (>80 资金超买)", latest.MFI)})
	}

	// ── Trend Signals ──

	// Supertrend 翻转
	if len(rows) >= 2 && prev.STDir < 0 && latest.STDir > 0 {
		signals = append(signals, Signal{"buy", "Supertrend翻转", "bullish", 0.8, latest.Date,
			fmt.Sprintf("Supertrend 由空转多 (趋势值=%.2f)", latest.STVal)})
	}
	// Supertrend 翻空
	if len(rows) >= 2 && prev.STDir > 0 && latest.STDir < 0 {
		signals = append(signals, Signal{"sell", "Supertrend翻空", "bearish", 0.8, latest.Date,
			fmt.Sprintf("Supertrend 由多转空 (趋势值=%.2f)", latest.STVal)})
	}

	// PSAR 翻转
	//
	// 抛物线转向系统和 Supertrend 是同一类东西：止损位贴着价格走，多空两侧
	// 切换的那一根 K 线就是"趋势方向反转"。所以判定方式、category、强度都跟
	// 上面的 Supertrend 翻转保持一致，只比较相邻两根 K 线的多空关系有没有互换。
	//
	// psarState 把 COALESCE 补出来的 0 当作"这根 K 没有 PSAR"而不是真实值：
	// 见 psarState 的注释。
	if len(rows) >= 2 {
		prevBull, prevOK := psarState(prev)
		latestBull, latestOK := psarState(latest)
		if prevOK && latestOK && prevBull != latestBull {
			if latestBull {
				signals = append(signals, Signal{"buy", "PSAR翻多", "bullish", 0.8, latest.Date,
					fmt.Sprintf("收盘价 %.2f 上破 PSAR %.2f（前日 PSAR %.2f 在价格上方），由空转多",
						latest.Close, latest.PSAR, prev.PSAR)})
			} else {
				signals = append(signals, Signal{"sell", "PSAR翻空", "bearish", 0.8, latest.Date,
					fmt.Sprintf("收盘价 %.2f 跌破 PSAR %.2f（前日 PSAR %.2f 在价格下方），由多转空",
						latest.Close, latest.PSAR, prev.PSAR)})
			}
		}
	}

	// ADX 强趋势 + DI 方向
	if latest.ADX > 25 {
		if latest.DIPlus > latest.DIMinus {
			signals = append(signals, Signal{"trend", "ADX多头趋势", "bullish",
				math.Min(0.9, 0.5+latest.ADX/100), latest.Date,
				fmt.Sprintf("ADX=%.1f (>25), DI+(%.1f) > DI-(%.1f)", latest.ADX, latest.DIPlus, latest.DIMinus)})
		} else {
			signals = append(signals, Signal{"trend", "ADX空头趋势", "bearish",
				math.Min(0.9, 0.5+latest.ADX/100), latest.Date,
				fmt.Sprintf("ADX=%.1f (>25), DI+(%.1f) < DI-(%.1f)", latest.ADX, latest.DIPlus, latest.DIMinus)})
		}
	}
	// DI 金叉
	if len(rows) >= 2 && prev.DIPlus <= prev.DIMinus && latest.DIPlus > latest.DIMinus {
		signals = append(signals, Signal{"trend", "DI金叉", "bullish", 0.75, latest.Date,
			fmt.Sprintf("DI+(%.1f) 上穿 DI-(%.1f)", latest.DIPlus, latest.DIMinus)})
	}
	// DI 死叉
	if len(rows) >= 2 && prev.DIPlus >= prev.DIMinus && latest.DIPlus < latest.DIMinus {
		signals = append(signals, Signal{"trend", "DI死叉", "bearish", 0.75, latest.Date,
			fmt.Sprintf("DI+(%.1f) 下穿 DI-(%.1f)", latest.DIPlus, latest.DIMinus)})
	}

	// Aroon 多头排列
	if latest.AroonUp > 70 && latest.AroonDown < 30 {
		signals = append(signals, Signal{"trend", "Aroon多头排列", "bullish", 0.7, latest.Date,
			fmt.Sprintf("AroonUp=%.0f (>70), AroonDown=%.0f (<30)", latest.AroonUp, latest.AroonDown)})
	}
	// Aroon 空头排列
	if latest.AroonDown > 70 && latest.AroonUp < 30 {
		signals = append(signals, Signal{"trend", "Aroon空头排列", "bearish", 0.7, latest.Date,
			fmt.Sprintf("AroonDown=%.0f (>70), AroonUp=%.0f (<30)", latest.AroonDown, latest.AroonUp)})
	}

	// Vortex 金叉/死叉
	if len(rows) >= 2 && prev.VIPlus <= prev.VIMinus && latest.VIPlus > latest.VIMinus {
		signals = append(signals, Signal{"trend", "Vortex金叉", "bullish", 0.7, latest.Date,
			fmt.Sprintf("VI+(%.2f) 上穿 VI-(%.2f)", latest.VIPlus, latest.VIMinus)})
	}
	if len(rows) >= 2 && prev.VIPlus >= prev.VIMinus && latest.VIPlus < latest.VIMinus {
		signals = append(signals, Signal{"trend", "Vortex死叉", "bearish", 0.7, latest.Date,
			fmt.Sprintf("VI+(%.2f) 下穿 VI-(%.2f)", latest.VIPlus, latest.VIMinus)})
	}

	// Choppiness Index 进出震荡区
	//
	// CHOP = 100 * log10(ΣTR / (区间最高 - 区间最低)) / log10(n)，固定 0~100，跟价格
	// 尺度无关，所以可以跨标的、跨价位比较。0 = 一路直走不回头，100 = 来回锯齿。
	// 它回答的正是波段持仓唯一真正要回答的问题——**该不该继续拿着**：一旦进入震荡
	// 区，上面那一堆趋势型信号（金叉、突破、ADX）全是噪音，正确的动作是把趋势仓
	// 减掉，而不是反过来加仓摊薄成本。这条判断今天在这个工具里是空白的。
	//
	// 阈值 38.2 / 61.8 是 Choppiness Index 通行的高低分位，不是随手挑的整数；
	// 取整到 38.2 跟 61.8 是因为它们是这组指标的标准读数位。同样只在**跨过**分位
	// 的那一根上报，震荡区里不逐根刷屏。
	//
	// 0 的处理：CHOP 趋近 0 要求 ΣTR 恰好等于"区间最高减区间最低"，也就是 14 天里
	// 每天同向单边、连一个跳空和一天回撤都没有——现实中不会发生；而 COALESCE 补出
	// 来的空值正好是 0。所以 >0 在这里同时排除了缺失和那个不可实现的边界。
	if len(rows) >= 2 && hasData(prev.Chop, latest.Chop) {
		if prev.Chop <= chopHighQuantile && latest.Chop > chopHighQuantile {
			signals = append(signals, Signal{"sell", "CHOP进入震荡", "bearish", 0.65, latest.Date,
				fmt.Sprintf("CHOP 由 %.1f 升到 %.1f（>%.1f），进入震荡区，趋势信号失真宜减仓",
					prev.Chop, latest.Chop, chopHighQuantile)})
		}
		if prev.Chop >= chopLowQuantile && latest.Chop < chopLowQuantile {
			signals = append(signals, Signal{"buy", "CHOP重回趋势", "bullish", 0.65, latest.Date,
				fmt.Sprintf("CHOP 由 %.1f 降到 %.1f（<%.1f），重回趋势区，趋势信号重新可用",
					prev.Chop, latest.Chop, chopLowQuantile)})
		}
	}

	// ── Volatility Signals ──

	// 布林带宽度变化（收口 = 变盘前兆）
	if len(rows) >= 5 && latest.BBWidth > 0 {
		avgWidth := 0.0
		for i := 0; i < 5 && i < len(rows); i++ {
			avgWidth += rows[i].BBWidth
		}
		avgWidth /= float64(min(5, len(rows)))
		if avgWidth > 0 && latest.BBWidth < avgWidth*0.5 {
			signals = append(signals, Signal{"volatility", "布林带收口", "neutral", 0.6, latest.Date,
				fmt.Sprintf("BB宽度=%.2f 低于5日均值%.2f的50%%，变盘前兆", latest.BBWidth, avgWidth)})
		}
	}

	// Donchian 突破
	//
	// **这条曾经是一个永不触发的死信号，而且一直广告在工具描述里。**
	//
	// 旧判定是 `latest.Close > latest.DCUpper`。而 `volatility_donchian_20_upper`
	// 是**含当天**的 20 日滚动最高价（实测 600519.SH 近 400 根 K 线里有 22 根
	// dc_upper 恰好等于当日 high，可证窗口含当天），于是恒有
	//
	//	dc_upper >= high >= close   ⟹   close > dc_upper 恒为假
	//
	// 拿 600519.SH 近 400 根真实 K 线数过：满足 `close > dc_upper` 的有 **0 根**。
	// 而下轨的镜像条件 `close < dc_lower` 同期触发了 64 根——同一份数据、同一个
	// 字段，方向不同结果差这么多，正是"含当天窗口拿去做突破判定"的典型症状。
	//
	// 正确写法是跟**上一根**的上轨比：`prev.DCUpper` 才是"今天之前那 20 天的
	// 最高价"，今天收盘站上去才是真的创了 20 日新高。下轨同理。
	//
	// 上一版注释里说的 `BBUpper == DCUpper`（两个独立算出来的量做浮点全等）也
	// 是死条件，注释说"一个月真实数据匹配 0 次"——那是在解释上一层的错误做法，
	// 不是这个条件的正当性论证。两个条件都不可能成立，所以 `anomaly` 选股策略
	// 一直匹配不上。
	if len(rows) >= 2 {
		prev := rows[1]
		if hasData(latest.Close, prev.Close, prev.DCUpper) && latest.Close > prev.DCUpper {
			signals = append(signals, Signal{"volatility", "Donchian上轨突破", "bullish", 0.65, latest.Date,
				fmt.Sprintf("收盘价 %.2f 站上前 20 日最高 %.2f，创新的 20 日高点",
					latest.Close, prev.DCUpper)})
		}
		// 下轨必须与上轨同一套口径：同样跟上一根比，否则又是一对口径不一致的孪生信号。
		if hasData(latest.Close, prev.Close, prev.DCLower) && latest.Close < prev.DCLower {
			signals = append(signals, Signal{"volatility", "Donchian下轨跌破", "bearish", 0.65, latest.Date,
				fmt.Sprintf("收盘价 %.2f 跌破前 20 日最低 %.2f，破的 20 日低点",
					latest.Close, prev.DCLower)})
		}
	}

	// 布林中轨（20 日均线）收复 / 跌破
	//
	// bb_mid 就是 20 日均线。工具里原来的 Donchian 上轨突破只覆盖"往上突破"这一
	// 侧，中轨这条最常用的均线基准反而没读——波段最常见的进场不是追突破，是**回踩
	// 20 日线企稳**，也就是价格从下方收回中轨。中轨是平的，用它做基准判断的是
	// "这次回撤有没有守住均线"，这跟上面 Keltner 中轨那条（均线本身在不在往上）
	// 是两回事，所以两条不重复。
	//
	// 只报穿越的那一根：贴着中轨震荡的时候每天都"在上方"，逐根报没有信息量。
	if len(rows) >= 2 && hasData(prev.Close, prev.BBMid, latest.Close, latest.BBMid) {
		if prev.Close <= prev.BBMid && latest.Close > latest.BBMid {
			signals = append(signals, Signal{"buy", "布林中轨收复", "bullish", 0.65, latest.Date,
				fmt.Sprintf("收盘价由 %.2f（20日线 %.2f 下方）收回至 %.2f 上方，回踩中轨企稳",
					prev.Close, prev.BBMid, latest.Close)})
		}
		if prev.Close >= prev.BBMid && latest.Close < latest.BBMid {
			signals = append(signals, Signal{"sell", "布林中轨跌破", "bearish", 0.65, latest.Date,
				fmt.Sprintf("收盘价由 %.2f（20日线 %.2f 上方）跌破至 %.2f 下方，中轨失守",
					prev.Close, prev.BBMid, latest.Close)})
		}
	}

	// Keltner 通道（EMA20 ± 2×ATR）与布林带的关系：Squeeze 与挤压突破
	//
	// 布林带是「均值 ± 2 倍标准差」，Keltner 是「EMA20 ± 2 倍 ATR」——两者的
	// 带宽驱动完全不同（波动率 vs 成交量相关），所以布林带收进 Keltner 通道里，
	// 意味着波动率已经压到比另一套口径还低的水平，也就是没有任何一种口径认为
	// 这里还有行情。这正是 Squeeze 指标的原始定义，也是"该布局了"而不是"该跑了"
	// 的那种前期。
	//
	// 只报**开始压缩**的那一根，以及**解压方向**的那一根：压缩可能连着十几天，
	// 逐根报"Keltner挤压"等于把一个状态说十几次。
	if len(rows) >= 2 && kcSqueezed(latest) && !kcSqueezed(prev) {
		signals = append(signals, Signal{"volatility", "Keltner挤压", "neutral", 0.65, latest.Date,
			fmt.Sprintf("布林带(%.2f~%.2f)收进Keltner通道(%.2f~%.2f)内，波动压缩、变盘前兆",
				latest.BBUpper, latest.BBLower, latest.KCUpper, latest.KCLower)})
	}
	// 挤压之后的第一根站上/跌破 Keltner 外轨 = 挤压方向被选出来了。前一日必须还在
	// 压缩中，否则就不是"解压"而只是普通的价格波动。
	if len(rows) >= 2 && kcSqueezed(prev) {
		if hasData(latest.Close, latest.KCUpper) && latest.Close > latest.KCUpper {
			signals = append(signals, Signal{"volatility", "Keltner挤压向上突破", "bullish", 0.75, latest.Date,
				fmt.Sprintf("前一日仍在Keltner挤压中，收盘价 %.2f 站上KC上轨 %.2f，挤压向上释放",
					latest.Close, latest.KCUpper)})
		}
		if hasData(latest.Close, latest.KCLower) && latest.Close < latest.KCLower {
			signals = append(signals, Signal{"volatility", "Keltner挤压向下突破", "bearish", 0.75, latest.Date,
				fmt.Sprintf("前一日仍在Keltner挤压中，收盘价 %.2f 跌破KC下轨 %.2f，挤压向下释放",
					latest.Close, latest.KCLower)})
		}
	}

	// Keltner 中轨（EMA20）趋势带
	//
	// kc_mid 是 EMA20，**带斜率**——这是它跟上面布林中轨（20 日均线，平的）唯一
	// 也足够的区别，也是为什么这两条不重复：价格收回平均线只能说明"这次回撤守住了
	// 均线"，而价格站上**同时在往上走**的 EMA20 才是"均线也在托着价格"。反过来，
	// 价格收回一条正在下行的均线，那只是撞上一条正在压下来的阻力，不是支撑——
	// 所以这里带方向过滤，布林中轨那条不带。
	if len(rows) >= 2 && hasData(prev.Close, prev.KCMid, latest.Close, latest.KCMid) {
		if prev.Close <= prev.KCMid && latest.Close > latest.KCMid && latest.KCMid > prev.KCMid {
			signals = append(signals, Signal{"trend", "KC中轨多头带", "bullish", 0.7, latest.Date,
				fmt.Sprintf("收盘价 %.2f 收上EMA20(%.2f)且该均线同步上行（前日 %.2f），均线与价格同向",
					latest.Close, latest.KCMid, prev.KCMid)})
		}
		if prev.Close >= prev.KCMid && latest.Close < latest.KCMid && latest.KCMid < prev.KCMid {
			signals = append(signals, Signal{"trend", "KC中轨空头带", "bearish", 0.7, latest.Date,
				fmt.Sprintf("收盘价 %.2f 跌破EMA20(%.2f)且该均线同步下行（前日 %.2f），均线与价格同向",
					latest.Close, latest.KCMid, prev.KCMid)})
		}
	}

	// NATR 进出高波动档位
	//
	// NATR = 100 × ATR / Close，把 ATR 这个绝对价差折算成百分比。ATR 本身没法跨
	// 标的用：¥20 的票 ATR=0.8 和 ¥200 的票 ATR=0.8 完全是两回事，前者一天波 4%、
	// 后者只波 0.4%。止损距离只有落成百分比才可能在不同价位之间搬运。
	//
	// 阈值 5：日均真实波幅超过 5%，一天正常的来回就能吃掉 5%，止损放在一个 NATR
	// 以内几乎必然被扫。跨上去的动作是**放宽止损 + 减半仓位**，不是看空，所以
	// 方向给 neutral，让它进输出但不左右 summarizeSignals 的多空计票。
	//
	// 0 的处理：NATR = 0 意味着 ATR = 0 或 Close = 0，两者都是"没有行情"，而
	// COALESCE 补出来的空值也是 0。>0 把这两种情况一起排掉。
	if len(rows) >= 2 && hasData(prev.NATR, latest.NATR) {
		if prev.NATR <= natrHighPct && latest.NATR > natrHighPct {
			signals = append(signals, Signal{"volatility", "NATR进入高波动档位", "neutral", 0.6, latest.Date,
				fmt.Sprintf("NATR 由 %.2f%% 升到 %.2f%%（>%.0f%%），止损需放宽到 %.1f%% 以上、仓位减半",
					prev.NATR, latest.NATR, natrHighPct, latest.NATR)})
		}
		if prev.NATR > natrHighPct && latest.NATR <= natrHighPct {
			signals = append(signals, Signal{"volatility", "NATR退出高波动档位", "neutral", 0.6, latest.Date,
				fmt.Sprintf("NATR 由 %.2f%% 回落到 %.2f%%（<=%.0f%%），可恢复常规止损与仓位",
					prev.NATR, latest.NATR, natrHighPct)})
		}
	}

	// Donchian 下轨跌破 原先在这里还有第二处判定（用 latest.DCLower），
	// 与上面 396 行那一处语义完全相同——上轨含当天、下轨不含当天这个上游不对称，
	// 让两种写法都"看起来对"，于是同一根 K 线报两遍。下轨判定已统一到上面的
	// prev.DCLower（与上轨对称，不依赖上游那个不对称），此处删除。
	// 同名信号一晚只能有一处发出点：两处同名 + 都能触发 = 模型收到重复信号，
	// 而任何按信号计数算出来的统计都会被这一倍放大。

	// ATR 扩张
	if len(rows) >= 5 {
		avgATR := 0.0
		for i := 1; i < 5 && i < len(rows); i++ {
			avgATR += rows[i].ATR
		}
		avgATR /= float64(min(4, len(rows)-1))
		if avgATR > 0 && latest.ATR > avgATR*1.5 {
			signals = append(signals, Signal{"volatility", "ATR扩张", "neutral", 0.55, latest.Date,
				fmt.Sprintf("ATR=%.2f 为5日均值%.2f的%.1f倍，波动加剧", latest.ATR, avgATR, latest.ATR/avgATR)})
		}
	}

	// ── Volume Signals ──

	// 收盘价上穿 / 下穿 VWAP
	//
	// VWAP 是当日成交量加权均价，机构建仓的平均成本线。价格在线上意味着今天进
	// 场的人整体是浮盈的，砸下去要先替他们扛亏损；在线下则反过来。
	//
	// 报的是**穿越**而不是"当前在线上还是线下"：位置是状态，天天在那儿，没有
	// 决策价值；穿越才是事件——从线下翻到线上是资金接手的那一刻，反过来是接手
	// 的人开始撤退。
	//
	// VWAP 是价格，0 只可能是 COALESCE 补的空值，直接走 hasData。收盘价同理
	// （query.go 的 close 是跨库按日期合并来的，对不上就是 0）。
	if len(rows) >= 2 && hasData(latest.VWAP, prev.VWAP, latest.Close, prev.Close) {
		if prev.Close < prev.VWAP && latest.Close > latest.VWAP {
			signals = append(signals, Signal{"buy", "上穿VWAP", "bullish", 0.65, latest.Date,
				fmt.Sprintf("收盘 %.2f 由 VWAP %.2f 下方上穿，当日建仓成本线被收复",
					latest.Close, latest.VWAP)})
		}
		if prev.Close > prev.VWAP && latest.Close < latest.VWAP {
			signals = append(signals, Signal{"sell", "下穿VWAP", "bearish", 0.65, latest.Date,
				fmt.Sprintf("收盘 %.2f 由 VWAP %.2f 上方下穿，当日建仓成本线失守",
					latest.Close, latest.VWAP)})
		}
	}

	// CMF 资金流向
	if latest.CMF > 0.1 {
		signals = append(signals, Signal{"volume", "CMF资金流入", "bullish",
			math.Min(0.8, 0.5+latest.CMF), latest.Date,
			fmt.Sprintf("CMF=%.2f (>0.1 资金净流入)", latest.CMF)})
	}
	if latest.CMF < -0.1 {
		signals = append(signals, Signal{"volume", "CMF资金流出", "bearish",
			math.Min(0.8, 0.5+math.Abs(latest.CMF)), latest.Date,
			fmt.Sprintf("CMF=%.2f (<-0.1 资金净流出)", latest.CMF)})
	}

	// PVT 量价背离
	//
	// PVT（Price Volume Trend）是 close×volume 的逐日累加：涨日加得多、跌日减得
	// 少，所以它衡量的不是"成交量大不大"，而是"钱站在哪一边"。它的绝对水平由
	// 起点决定、跨标的没有可比性，**只有窗口内的差值有意义**。
	//
	// 真正值钱的不是 PVT 本身涨跌，而是它跟价格**说的不是同一件事**：5 天里价格
	// 涨了、PVT 反而降了，说明推动这段上涨的钱总量在减少（涨是靠缩量扛上去的），
	// 这正是顶部区域最典型的形态。反过来价跌 PVT 涨是有人在跌里接。
	//
	// 0 的处理（这里最要紧）：PVT 是累加量，正常情况下恒为正，所以 >0 是个可靠
	// 的"有值"判据。危险的是**只有一根**缺值——缺的那根是 0，差值就成了
	// -一个巨大的负数，价涨的标的会凭空报出"量价背离"。这跟 PSAR 那次是同一类
	// 错误，所以窗口两端的 PVT 和收盘价都必须存在，缺一不发。
	if len(rows) >= 6 {
		base := rows[5]
		if hasData(latest.PVT, base.PVT, latest.Close, base.Close) {
			dPVT := latest.PVT - base.PVT
			dClose := latest.Close - base.Close
			if dClose > 0 && dPVT < 0 {
				signals = append(signals, Signal{"volume", "价量背离", "bearish", 0.7, latest.Date,
					fmt.Sprintf("近5日价格 +%.2f（%.2f→%.2f）但PVT %.0f 下降，本轮上涨缺乏量能支撑",
						dClose, base.Close, latest.Close, base.PVT)})
			}
			if dClose < 0 && dPVT > 0 {
				signals = append(signals, Signal{"volume", "缩量下跌吸筹", "bullish", 0.65, latest.Date,
					fmt.Sprintf("近5日价格 %.2f（%.2f→%.2f）但PVT 上升 %.0f，下跌有资金承接",
						dClose, base.Close, latest.Close, dPVT)})
			}
		}
	}

	// PVI / NVI 强弱：谁在积累
	//
	// PVI（Positive Volume Index）只在**放量日**累加，NVI 只在**缩量日**累加，所以
	// 两条线的斜率差回答的是"钱是在放量的时候进的，还是缩量的时候进的"。PVI
	// 相对走强 = 放量日有人买（顺势资金进场）；NVI 相对走强 = 缩量日才有人买
	// （没有量的支撑，虚得多）。和 OBV 的区别是 OBV 把所有日子一视同仁，这里只看
	// 放量/缩量两种日子的区别。
	//
	// 报的是两条线强弱的**翻转**，不是强弱本身：一个常态就是 PVI 领先的标的，
	// 天天报"PVI强于NVI"等于什么都没说。翻转要比较两次窗口，所以至少要 7 根
	// K 线——数据不够就安静不发，不做近似。
	if len(rows) >= 7 {
		latestGain := pviNviSpread(latest, rows[5])
		prevGain := pviNviSpread(rows[1], rows[6])
		if latestGain.ok && prevGain.ok {
			if prevGain.spread <= 0 && latestGain.spread > 0 {
				signals = append(signals, Signal{"volume", "PVI强于NVI吸筹", "bullish", 0.7, latest.Date,
					fmt.Sprintf("近5日 PVI +%.0f 超过 NVI +%.0f，放量日资金持续进场（前日差值 %.0f）",
						latestGain.dPVI, latestGain.dNVI, prevGain.spread)})
			}
			if prevGain.spread >= 0 && latestGain.spread < 0 {
				signals = append(signals, Signal{"volume", "NVI强于PVI派发", "bearish", 0.7, latest.Date,
					fmt.Sprintf("近5日 NVI +%.0f 超过 PVI +%.0f，买盘只出现在缩量日（前日差值 %.0f）",
						latestGain.dNVI, latestGain.dPVI, prevGain.spread)})
			}
		}
	}

	// ── Statistical Signals ──

	// Z-Score 极值
	if latest.ZScore < -2 {
		signals = append(signals, Signal{"buy", "Z-Score超卖", "bullish", 0.75, latest.Date,
			fmt.Sprintf("Z-Score=%.2f (<-2 统计超卖)", latest.ZScore)})
	}
	if latest.ZScore > 2 {
		signals = append(signals, Signal{"sell", "Z-Score超买", "bearish", 0.75, latest.Date,
			fmt.Sprintf("Z-Score=%.2f (>2 统计超买)", latest.ZScore)})
	}

	// 线性回归斜率
	if latest.LinSlope > 0 {
		signals = append(signals, Signal{"trend", "线性回归上升", "bullish",
			math.Min(0.7, math.Abs(latest.LinSlope)/10), latest.Date,
			fmt.Sprintf("14日线性回归斜率=%.4f (上升)", latest.LinSlope)})
	} else if latest.LinSlope < 0 {
		signals = append(signals, Signal{"trend", "线性回归下降", "bearish",
			math.Min(0.7, math.Abs(latest.LinSlope)/10), latest.Date,
			fmt.Sprintf("14日线性回归斜率=%.4f (下降)", latest.LinSlope)})
	}

	// ── Candlestick Patterns ──
	candlePatterns := []struct {
		val    float64
		name   string
		signal string
		desc   string
	}{
		{latest.CdlMorningStar, "Morning Star晨星", "bullish", "底部反转形态"},
		{latest.CdlEveningStar, "Evening Star暮星", "bearish", "顶部反转形态"},
		{latest.CdlHammer, "Hammer锤子线", "bullish", "下影线长，潜在底部"},
		{latest.CdlShootingStar, "Shooting Star流星", "bearish", "上影线长，潜在顶部"},
		{latest.CdlDoji, "Doji十字星", "neutral", "多空平衡，变盘信号"},
		{latest.CdlEngulfing, "Engulfing吞没", "bullish", "看涨吞没形态"},
		{latest.CdlHarami, "Harami孕线", "neutral", "趋势放缓信号"},
		{latest.CdlPiercing, "Piercing刺透", "bullish", "看涨刺透形态"},
		{latest.CdlDarkCloud, "Dark Cloud乌云盖顶", "bearish", "看跌乌云形态"},
		{latest.Cdl3WhiteSold, "Three White Soldiers三白兵", "bullish", "连续三阳，强势上涨"},
		{latest.Cdl3BlackCrows, "Three Black Crows三乌鸦", "bearish", "连续三阴，强势下跌"},
	}
	for _, cp := range candlePatterns {
		if cp.val != 0 {
			strength := 0.7
			if math.Abs(cp.val) > 100 {
				strength = 0.85
			}
			signals = append(signals, Signal{"candle", cp.name, cp.signal, strength, latest.Date, cp.desc})
		}
	}

	return signals
}

// psarState maps one bar to its Parabolic SAR side and reports whether that
// side is computable at all.
//
//	bullish — PSAR 在收盘价下方（多头侧）
//	ok      — 该 bar 真的有 PSAR 和收盘价可以比较
//
// 0 不是一个合法的 PSAR 值。query.go 里 psar 走的是 COALESCE(trend_psar, 0)，
// 也就是说 0 = "这一列当天没有值"，不是"PSAR 恰好等于 0"。而 PSAR 的多空判定
// 只看 PSAR 和价格谁大：把缺失当成 0 参与比较，等于宣称"PSAR 远在价格下方"，
// 任何一段缺 PSAR 的数据都会凭空被判成多头侧，再和相邻的真实 bar 一比就伪造出
// 一次翻转。这正是这份文件在消灭的那类静默错误——一个看起来像信号、其实只是
// 缺数据的结果。所以 PSAR 或收盘价任一 <= 0，ok 就是 false，不发信号。
//
// 收盘价同样要判 0：query.go 里的 close 是从 market 库按 date 合并进来的，
// 合并不上就是 0（零值），拿它和 PSAR 比大小毫无意义。
// 阈值常量。全部集中在这里，是因为它们都被当成"读数分位"而不是魔法数使用——
// 散落在各个 if 里的话，半年后没人知道 0.3 和 5 是怎么来的。
const (
	// ER 跨过 1/3 即视为趋势可跟随：低于此值意味着三成以上行程是折返。
	erTrendThreshold = 1.0 / 3.0
	// Choppiness Index 的标准高低分位。
	chopLowQuantile  = 38.2
	chopHighQuantile = 61.8
	// NATR 高波动档位：日均真实波幅超过 5%，一个 NATR 以内的止损必被扫。
	natrHighPct = 5.0
)

// absVal 是 math.Abs 的别名，让"只看幅度、不看方向"的那几处判断读起来更清楚：
// absVal(x) < absVal(y) 一眼就知道是在比大小，而不是在比正负。
func absVal(v float64) float64 { return math.Abs(v) }

// hasData 报告给定的这些列是不是**真的读到了值**，而不是 query.go 的
// COALESCE 补出来的 0。
//
// 和 psarState 是同一个理由，只是把它复用到所有新加的列上：COALESCE(x, 0) 让
// "当天没有这一列的值"和"这一列恰好等于 0"在下游长得一模一样。凡是靠 0 当哨兵
// 值的列——价格类（BB/KC/Donchian 中轨上下轨、收盘价）、以及任何取值恒大于 0 的
// 量（NATR、PVT/PVI/NVI）——都必须先过这一关，否则一段缺数据的历史会凭空编出
// 一次"跌破"或"背离"，而模型无从分辨那是行情还是缺列。
//
// 唯一不能用它判的是 J：J < 0 是合法读数（见 detectSignals 里 J 那段的注释），
// 那里改用它的输入 K/D 来判断。
//
// 一个列都不传时返回 false：这在当前代码里不可能发生（每个调用点都传了自己的列），
// 但真发生了的话，返回 true 等于宣告"这根 K 什么数据都有"，属于静默放行。返回
// false 让它变成一个绊线——最坏结果是少报一条信号，而不是凭空多报一条。
func hasData(vals ...float64) bool {
	if len(vals) == 0 {
		return false
	}
	for _, v := range vals {
		if v <= 0 {
			return false
		}
	}
	return true
}

// kcSqueezed 报告这一根 K 是否处于 Keltner 挤压：布林带完全落在 Keltner 通道之内。
//
// 四个价格列任一为 0（缺失）都返回 false——理由同 hasData：用 0 参与上下轨比较
// 等于凭空造出一条极窄的通道。
func kcSqueezed(r row) bool {
	if !hasData(r.BBUpper, r.BBLower, r.KCUpper, r.KCLower) {
		return false
	}
	return r.BBUpper < r.KCUpper && r.BBLower > r.KCLower
}

// pviNviEdge 是一次窗口里 PVI 与 NVI 各自的增量，以及两者的差值。
type pviNviEdge struct {
	dPVI   float64
	dNVI   float64
	spread float64 // dPVI - dNVI，正数表示放量日资金更强
	ok     bool
}

// pviNviSpread 计算 latest 相对 base 的 PVI / NVI 增量差。
//
// ok 为 false 表示窗口任一端缺值（见 hasData 的理由：累加量只要有一根是 0，差值
// 就会变成一个假的大额增量或减量，pviNvi 两条信号都会被骗）。调用方必须先看 ok。
func pviNviSpread(latest, base row) pviNviEdge {
	if !hasData(latest.PVI, latest.NVI, base.PVI, base.NVI) {
		return pviNviEdge{}
	}
	e := pviNviEdge{
		dPVI: latest.PVI - base.PVI,
		dNVI: latest.NVI - base.NVI,
		ok:   true,
	}
	e.spread = e.dPVI - e.dNVI
	return e
}

// declaredSignalNames 是 detectSignals **实际会发出**的全部信号名，一一对应，一
// 个不多一个不少。
//
// 这份清单是 signals.go 与 scan.go 之间的契约：scan.go 的 Description() 是逐字
// 交给 LLM 的，写错一个名字模型就会去调、拿不到、再自己编一个。历史上有四个信号
// （PSAR翻转 / VWAP突破 / 量价齐升 / 量价背离）是"描述里广告了、代码里没实现"。
//
// 单子靠三个方向由 signals_contract_test.go 把关，测试里没有正则：
//  1. 清单里每一条都必须被某组构造数据真正打出来（少写了 → 达不到 / 被删了 → 立刻红）
//  2. detectSignals 打出来的每一条都必须在清单里（新加了信号忘了登记 → 立刻红）
//  3. 清单里的每一条都必须出现在 Description() 的正文里
//
// 增删信号时改这里，同时改 scan.go 的 Description()。
var declaredSignalNames = []string{
	// Momentum
	"MACD金叉", "MACD死叉",
	"KDJ超卖金叉", "KDJ超买死叉",
	"J超卖回中", "J超买回中",
	"RSI6超卖", "RSI6超买",
	"RSI14超卖回升", "RSI14超买回落",
	"MACD动能衰减",
	"Stochastic超卖金叉", "Stochastic超买死叉",
	"CCI超卖", "CCI超买",
	"Williams%R超卖", "Williams%R超买",
	"MFI超卖", "MFI超买",
	"ER转高效", "ER转低效",
	// Trend
	"Supertrend翻转", "Supertrend翻空",
	"PSAR翻多", "PSAR翻空",
	"ADX多头趋势", "ADX空头趋势",
	"DI金叉", "DI死叉",
	"Aroon多头排列", "Aroon空头排列",
	"Vortex金叉", "Vortex死叉",
	"CHOP进入震荡", "CHOP重回趋势",
	"KC中轨多头带", "KC中轨空头带",
	// Volatility
	"布林带收口", "布林中轨收复", "布林中轨跌破",
	"Donchian上轨突破", "Donchian下轨跌破",
	"Keltner挤压", "Keltner挤压向上突破", "Keltner挤压向下突破",
	"NATR进入高波动档位", "NATR退出高波动档位",
	"ATR扩张",
	// Volume
	"CMF资金流入", "CMF资金流出",
	"上穿VWAP", "下穿VWAP",
	"价量背离", "缩量下跌吸筹",
	"PVI强于NVI吸筹", "NVI强于PVI派发",
	// Statistics
	"Z-Score超卖", "Z-Score超买",
	"线性回归上升", "线性回归下降",
	// Candlestick
	"Morning Star晨星", "Evening Star暮星", "Hammer锤子线", "Shooting Star流星",
	"Doji十字星", "Engulfing吞没", "Harami孕线", "Piercing刺透",
	"Dark Cloud乌云盖顶", "Three White Soldiers三白兵", "Three Black Crows三乌鸦",
}

// noisySignalNames 是"触发频率高到不再携带信息"的信号，默认**不**返回。
//
// 为什么要有这层：信号是按 bar 注入模型上下文的。一条在 20% 以上的 bar 上都成立
// 的信号，报的是**当前市场状态**（"趋势线斜率为正"）而不是**转折事件**；连续
// 若干天读到同一句话，模型会学会无视它 —— 真正的转折（1%~5% 那一档）就被淹没。
// 所以默认只给 informative，需要时用 include_noisy 显式索取。
//
// ⚠️ **名单的权威来源是 signal_frequency_audit.md**，由
// signal_frequency_audit_test.go 跑**真实的 detectSignals** 量出（300 只票跨步
// 抽样、279,360 个 7-bar 窗口、2022-08 起跨 2022 熊市与 2024-26 行情）。
//
// **不要用 scripts/classify_signals.py 决定这份名单。** 那个脚本是用 SQL 重写
// 判据的旁路，分母是"bar 数"而审计是"7-bar 窗口数"，同一条件下两者能差一倍
// （MACD动能衰减：SQL 20.43% vs 审计 10.25%，前者越过 20% 阈值、后者没越）。
// 2026-10-01 正是先信了 SQL 那一版，把 MACD动能衰减 错折了、又漏折了
// Donchian下轨跌破。SQL 脚本只能用来**核对判据有没有抄错**，不能定名单。
//
// 本名单 2026-10-01 改过两次，都是把 SQL 的结果换成审计的：
//
//	第一次（判据抄错）: 11 条 → 8 条。MACD动能衰减 原写成 hist>0、
//	  Aroon多头排列 原写成 up>down、CMF 门槛原写成 ±0.05、RSI6超卖 原写 <30、
//	  Keltner挤压 原写成状态量（真实是首次进入）。修正后 Aroon多头排列 /
//	  CMF资金流入 / Keltner挤压 掉出名单。
//	第二次（换权威）: 8 条 → 8 条，但**换了两条**。MACD动能衰减 掉出
//	  （审计 10.25%，informative）；Donchian下轨跌破 进来（审计 27.72%）。
//
// 名单是**数据结论**，不是价值判断：线性回归 47%/51% 报的是趋势方向，用户在
// "这只票现在什么趋势"的问题上可能真的想要它们。所以它们被**默认折叠**，不是
// 被删除 —— include_noisy=true 拿得到全量。
var noisySignalNames = []string{
	"线性回归下降",                    // 51.40%
	"线性回归上升",                    // 47.03%
	"CMF资金流出",                     // 31.35%
	"Donchian下轨跌破",                // 27.72%
	"Williams%R超卖",                  // 25.86%
	"ADX多头趋势",                     // 25.63%
	"Aroon空头排列",                   // 23.65%
	"CCI超卖",                         // 21.47%
}

// isNoisySignal 报告某个信号名是否在默认折叠名单里。
func isNoisySignal(name string) bool {
	for _, n := range noisySignalNames {
		if n == name {
			return true
		}
	}
	return false
}

// partitionSignals 把信号分成默认返回与按需返回两半。
//
// 返回的 suppressed 保持原顺序，便于模型知道"确实有这些信号，只是没给你"。
func partitionSignals(signals []Signal) (keep, suppressed []Signal) {
	for _, s := range signals {
		if isNoisySignal(s.Name) {
			suppressed = append(suppressed, s)
		} else {
			keep = append(keep, s)
		}
	}
	return keep, suppressed
}

func psarState(r row) (bullish bool, ok bool) {
	if r.PSAR <= 0 || r.Close <= 0 {
		return false, false
	}
	return r.Close > r.PSAR, true
}

func summarizeSignals(signals []Signal) map[string]interface{} {
	buyCount, sellCount := 0, 0
	buyStrength, sellStrength := 0.0, 0.0
	for _, s := range signals {
		if s.Signal == "bullish" {
			buyCount++
			buyStrength += s.Strength
		} else if s.Signal == "bearish" {
			sellCount++
			sellStrength += s.Strength
		}
	}

	verdict := "中性"
	if buyCount > sellCount && buyStrength > sellStrength {
		verdict = "偏多"
	} else if sellCount > buyCount && sellStrength > buyStrength {
		verdict = "偏空"
	}

	return map[string]interface{}{
		"verdict":       verdict,
		"buy_signals":   buyCount,
		"sell_signals":  sellCount,
		"total":         len(signals),
		"buy_strength":  round2(buyStrength),
		"sell_strength": round2(sellStrength),
	}
}

func round2(f float64) float64 {
	return math.Round(f*100) / 100
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
