package zettaranc

// ScreenerStrategies 返回选股器支持的策略名。
//
// 策略的真实清单在 python-service 的 STRATEGY_RULES 里（那份字典还带着每条
// 策略的 match_signals / direction / min_count，是唯一真相源）。这里曾经把
// 同一份列表手抄了一份，两边各改各的 —— 加了 volatility_spike 只在 Python
// 侧存在，模型按 enum 拼不出这个值。
//
// 这里的列表只用于**给 LLM 看的提示**：多列一个不存在的策略名，模型会拿到
// python-service 的 "策略不存在" 报错；少列一个真存在的，模型根本不会去试。
// 宁可多列。strategies_test.go 会把两边对不齐的项逐个报出来。
//
// 策略名不能叫 "B1"：形态标注层的 B1（建仓波回调买点，annotate 接口发的
// type="b1"）和选股策略是两个东西，同名会让模型把二者当成同一个信号。
func ScreenerStrategies() []string {
	return []string{
		"oversold_combo",   // 超卖共振：≥2 个超卖信号（≠ 形态标注里的 B1 建仓波）
		"B2",               // MACD 金叉
		"SB1",              // 超卖组合这一组再加 MACD 金叉（≥3 项）
		"shaofu",           // 少妇战法
		"limit_up",         // 涨停/强势股（真实涨停池）
		"anomaly",          // 风险异常（direction=bearish）
		"volatility_spike", // 波动率异动（ATR扩张 / 布林带收口）
		"vol_breakout",     // 放量突破（涨幅>3% 且量比>1.5）
		"donchian_break",   // Donchian 上轨突破
		// 以下四组覆盖此前"能算但无策略用"的 bearish 信号与单根形态
		"overbought_combo",       // 超买/见顶（oversold_combo 的镜像，direction=bearish）
		"trend_down",             // 趋势转空（死叉 + 空头排列）
		"vortex_bull",            // Vortex 多头交叉（VI+ 上穿 VI-）
		"hammer_reversal",        // 锤头线（底部反转）
		"shooting_star_reversal", // 流星线（顶部反转，direction=bearish）
		// 以下六条 Python 侧先落地、Go 侧一直没跟上（2026-10-04 补齐）。
		// 它们的 match_signals / min_count / min_bars 都在 STRATEGY_RULES 里，
		// 这里只补名字 —— 缺一个名字就等于"策略存在，但模型按 enum 拼不出来"，
		// 而 strategies_test.go 就是为了让这种漂移在 CI 里变红。
		"changan_combo",        // 长安三件套（全库十年仅 117 次命中，刻意保守）
		"double_gun_combo",     // 双枪放量（15 根窗口）
		"needle_under20_combo", // 单针下 20（长期强势趋势里的短期超跌）
		"shrink_pullback",      // 缩量回踩（70 根窗口；Python 侧已改逐片判定）
		"trend_right_combo",    // 转右：白线金叉黄线 / 收盘上穿黄线（direction=bullish）
		"trend_left_combo",     // 转左：白线死叉黄线 / 收盘下穿黄线（direction=bearish）
	}
}
