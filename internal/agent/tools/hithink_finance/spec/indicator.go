package spec

// indicator 家族的声明：全部查 v_indicators_daily，形状是「一个代码 + 一个窗口 +
// 一行时间序列」。
//
// 这七个工具里有五个的窗口上限是 500、两个是 200，而迁移前它们各自的说明文字
// 有一半没提上限、schema 里一个都没有 maximum。现在上限写在 Param.Max 里，
// schema 的 maximum、运行时的钳制、说明文字里的「最大 N」三处都从它派生。
var (
	// IndicatorMomentumKDJ 是 KDJ 指标。
	IndicatorMomentumKDJ = Spec{
		Name: "hithink.finance.indicator.momentum.kdj",
		Description: `获取股票的 KDJ 指标。返回 date, k, d, j。

使用示例：thscode="600519.SH", days={days}`,
		DB:    "indicators",
		SQL:   `SELECT date, momentum_kdj_9_3_k AS k, momentum_kdj_9_3_d AS d, momentum_kdj_9_3_j AS j FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
		Binds: []string{"thscode", "days"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码",
				Required: true,
				EmptyMsg: "参数错误：thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "查询天数（默认 {default}）",
				Default: 30,
				Max:     intMax(500),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorTrendMA 是均线指标。
	IndicatorTrendMA = Spec{
		Name: "hithink.finance.indicator.trend.ma",
		Description: `获取股票的均线指标（MA5/MA10/MA20/MA60/MA120/MA250）。

使用示例：thscode="600519.SH", days={days}`,
		DB:    "indicators",
		SQL:   `SELECT date, overlap_sma_5 AS ma5, overlap_sma_10 AS ma10, overlap_sma_20 AS ma20, overlap_sma_60 AS ma60, overlap_sma_120 AS ma120, overlap_sma_250 AS ma250 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
		Binds: []string{"thscode", "days"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码",
				Required: true,
				EmptyMsg: "参数错误：thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "查询天数（默认 {default}）",
				Default: 30,
				Max:     intMax(500),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorMomentumMacdRsi 是 MACD 与多周期 RSI。
	IndicatorMomentumMacdRsi = Spec{
		Name: "hithink.finance.indicator.momentum.macd_rsi",
		Description: `获取股票 MACD（快线、信号线、柱状图）与多周期 RSI（RSI6、RSI12、RSI24）。

返回 date, macd, signal, hist, rsi_6, rsi_12, rsi_24。`,
		DB:    "indicators",
		SQL:   `SELECT date, momentum_macd_12_26_9_macd AS macd, momentum_macd_12_26_9_signal AS signal, momentum_macd_12_26_9_hist AS hist, momentum_rsi_6 AS rsi_6, momentum_rsi_12 AS rsi_12, momentum_rsi_24 AS rsi_24 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
		Binds: []string{"thscode", "days"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码（如 600519.SH）",
				Required: true,
				EmptyMsg: "参数错误：thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "查询天数（默认 {default}）",
				Default: 30,
				Max:     intMax(500),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorVolatilityBBands 是布林带与 ATR/NATR。
	IndicatorVolatilityBBands = Spec{
		Name: "hithink.finance.indicator.volatility.bbands",
		Description: `获取股票布林带（上轨、中轨、下轨）与真实波幅 ATR/NATR 指标。

返回 date, upper, middle, lower, atr, natr。`,
		DB:    "indicators",
		SQL:   `SELECT date, volatility_bbands_20_2_0_upper AS upper, volatility_bbands_20_2_0_middle AS middle, volatility_bbands_20_2_0_lower AS lower, volatility_atr_14 AS atr, volatility_natr_14 AS natr FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
		Binds: []string{"thscode", "days"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码（如 600519.SH）",
				Required: true,
				EmptyMsg: "参数错误：thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "查询天数（默认 {default}）",
				Default: 30,
				Max:     intMax(500),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorVolumeMoneyFlow 是主力资金指标。
	IndicatorVolumeMoneyFlow = Spec{
		Name: "hithink.finance.indicator.volume.money_flow",
		Description: `获取股票主力资金指标：OBV 能量潮、CMF 蔡金资金流向、MFI 资金流量指标、VWAP 成交量加权平均价。

返回 date, obv, cmf, mfi, vwap。`,
		DB:    "indicators",
		SQL:   `SELECT date, volume_obv AS obv, volume_cmf_20 AS cmf, volume_mfi_14 AS mfi, volume_vwap AS vwap FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
		Binds: []string{"thscode", "days"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码（如 600519.SH）",
				Required: true,
				EmptyMsg: "参数错误：thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "查询天数（默认 {default}）",
				Default: 30,
				Max:     intMax(500),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorVolatilitySqueeze 是 TTM Squeeze 波动挤压指标。
	//
	// 它的 SQL 把窗口值直接写进 LIMIT（不是绑定参数），所以 days 的 Bind 是 -1，
	// SQL 模板里的 {days} 由执行器替换成钳制后的值。
	IndicatorVolatilitySqueeze = Spec{
		Name: "hithink.finance.indicator.volatility.squeeze",
		Description: `获取股票经典 TTM Squeeze 波动挤压指标（布林线收缩于肯特纳通道内部的蓄势爆发状态）与动能柱。
返回 date, 布林上下轨, 肯特纳上下轨, MACD柱动能, 判定挤压状态 (is_squeezing)。`,
		DB:    "indicators",
		SQL:   `SELECT date, volatility_bbands_20_2_0_upper, volatility_bbands_20_2_0_lower, volatility_kc_20_2_upper, volatility_kc_20_2_lower, momentum_macd_12_26_9_hist FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT {days}`,
		Binds: []string{"thscode"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码（如 000001.SZ 或 600519.SH）",
				Required: true,
				Trim:     true,
				EmptyMsg: "thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "回看天数（默认 {default}，最大 {max}）",
				Default: 30,
				Max:     intMax(200),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}

	// IndicatorTrendSupertrend 是 Supertrend 顺势指标与贝塔系数。
	IndicatorTrendSupertrend = Spec{
		Name: "hithink.finance.indicator.trend.supertrend",
		Description: `获取股票超级趋势 (Supertrend 10,3) 顺势指标与贝塔系数 Beta。
返回 date, trend (趋势轨), direction (1=多头顺势牛, -1=空头防守熊), long (多头保护位), short (空头保护位), beta (20日Beta)。`,
		DB:    "indicators",
		SQL:   `SELECT date, trend_supertrend_10_3_0_trend, trend_supertrend_10_3_0_direction, trend_supertrend_10_3_0_long, trend_supertrend_10_3_0_short, statistics_beta_20 FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT {days}`,
		Binds: []string{"thscode"},
		Params: []Param{
			{
				Name:     "thscode",
				Type:     "string",
				Desc:     "同花顺股票代码（如 000001.SZ 或 600519.SH）",
				Required: true,
				Trim:     true,
				EmptyMsg: "thscode 不能为空",
			},
			{
				Name:    "days",
				Type:    "integer",
				Desc:    "回看天数（默认 {default}，最大 {max}）",
				Default: 30,
				Max:     intMax(200),
			},
		},
		Envelope: Envelope{
			Echo:     []string{"thscode"},
			CountKey: "days",
			Data:     true,
		},
	}
)
