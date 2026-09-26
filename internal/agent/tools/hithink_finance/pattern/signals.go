package pattern

import (
	"fmt"
	"math"
)

// Signal represents a detected pattern.
type Signal struct {
	Category string  `json:"category"` // buy/sell/trend/volatility/volume/candle
	Name     string  `json:"name"`
	Signal   string  `json:"signal"` // bullish/bearish/neutral
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
	if latest.WillR < -80 {
		signals = append(signals, Signal{"buy", "Williams%%R超卖", "bullish", 0.65, latest.Date,
			fmt.Sprintf("Williams%%R=%.1f (<-80)", latest.WillR)})
	}
	if latest.WillR > -20 {
		signals = append(signals, Signal{"sell", "Williams%%R超买", "bearish", 0.65, latest.Date,
			fmt.Sprintf("Williams%%R=%.1f (>−20)", latest.WillR)})
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
	if len(rows) >= 2 && prev.STDir > 0 && latest.STDir < 0 {
		signals = append(signals, Signal{"sell", "Supertrend翻空", "bearish", 0.8, latest.Date,
			fmt.Sprintf("Supertrend 由多转空 (趋势值=%.2f)", latest.STVal)})
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
	if len(rows) >= 2 {
		// We don't have close price here, but we can check if indicators suggest breakout
		// This is a simplified version
		if latest.BBUpper > 0 && latest.BBUpper == latest.DCUpper {
			signals = append(signals, Signal{"volatility", "Donchian上轨突破", "bullish", 0.65, latest.Date,
				fmt.Sprintf("价格触及布林上轨(%.2f) = Donchian上轨", latest.BBUpper)})
		}
	}

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
		"verdict":      verdict,
		"buy_signals":  buyCount,
		"sell_signals": sellCount,
		"total":        len(signals),
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
