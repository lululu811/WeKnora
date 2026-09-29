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
func ScreenerStrategies() []string {
	return []string{
		"B1",               // 买点选股：超卖金叉组合
		"B2",               // MACD 金叉
		"SB1",              // B1 基础 + MACD 金叉
		"shaofu",           // 少妇战法
		"limit_up",         // 涨停/强势股（真实涨停池）
		"anomaly",          // 风险异常（direction=bearish）
		"volatility_spike", // 波动率异动（ATR扩张 / 布林带收口）
		"vol_breakout",     // 放量突破（涨幅>3% 且量比>1.5）
		"donchian_break",   // Donchian 上轨突破
	}
}
