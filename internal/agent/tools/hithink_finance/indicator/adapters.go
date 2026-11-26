package indicator

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// 这一族七个工具的形状完全一样：一个代码、一个窗口、一张表、一句说明。
// 声明在 spec 包里，六步实现在 spec.Execute 里，这里只剩一个 Name()。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。

// MomentumKDJTool 返回 KDJ 指标。
type MomentumKDJTool struct{ spec.Tool }

// NewMomentumKDJTool 创建 KDJ 工具。
func NewMomentumKDJTool(config *hithink_finance.Config) *MomentumKDJTool {
	return &MomentumKDJTool{spec.Tool{Config: config, Spec: spec.IndicatorMomentumKDJ}}
}

// Name 返回工具名。
func (t *MomentumKDJTool) Name() string { return "hithink.finance.indicator.momentum.kdj" }

// TrendMATool 返回均线指标。
type TrendMATool struct{ spec.Tool }

// NewTrendMATool 创建均线工具。
func NewTrendMATool(config *hithink_finance.Config) *TrendMATool {
	return &TrendMATool{spec.Tool{Config: config, Spec: spec.IndicatorTrendMA}}
}

// Name 返回工具名。
func (t *TrendMATool) Name() string { return "hithink.finance.indicator.trend.ma" }

// MomentumMacdRsiTool 返回 MACD 与多周期 RSI。
type MomentumMacdRsiTool struct{ spec.Tool }

// NewMomentumMacdRsiTool 创建 MACD/RSI 工具。
func NewMomentumMacdRsiTool(config *hithink_finance.Config) *MomentumMacdRsiTool {
	return &MomentumMacdRsiTool{spec.Tool{Config: config, Spec: spec.IndicatorMomentumMacdRsi}}
}

// Name 返回工具名。
func (t *MomentumMacdRsiTool) Name() string { return "hithink.finance.indicator.momentum.macd_rsi" }

// VolatilityBBandsTool 返回布林带与 ATR/NATR。
type VolatilityBBandsTool struct{ spec.Tool }

// NewVolatilityBBandsTool 创建布林带工具。
func NewVolatilityBBandsTool(config *hithink_finance.Config) *VolatilityBBandsTool {
	return &VolatilityBBandsTool{spec.Tool{Config: config, Spec: spec.IndicatorVolatilityBBands}}
}

// Name 返回工具名。
func (t *VolatilityBBandsTool) Name() string { return "hithink.finance.indicator.volatility.bbands" }

// VolatilitySqueezeTool 返回 TTM Squeeze 波动挤压指标。
type VolatilitySqueezeTool struct{ spec.Tool }

// NewVolatilitySqueezeTool 创建波动挤压工具。
func NewVolatilitySqueezeTool(config *hithink_finance.Config) *VolatilitySqueezeTool {
	return &VolatilitySqueezeTool{spec.Tool{Config: config, Spec: spec.IndicatorVolatilitySqueeze}}
}

// Name 返回工具名。
func (t *VolatilitySqueezeTool) Name() string { return "hithink.finance.indicator.volatility.squeeze" }

// TrendSupertrendTool 返回 Supertrend 顺势指标与贝塔系数。
type TrendSupertrendTool struct{ spec.Tool }

// NewTrendSupertrendTool 创建 Supertrend 工具。
func NewTrendSupertrendTool(config *hithink_finance.Config) *TrendSupertrendTool {
	return &TrendSupertrendTool{spec.Tool{Config: config, Spec: spec.IndicatorTrendSupertrend}}
}

// Name 返回工具名。
func (t *TrendSupertrendTool) Name() string { return "hithink.finance.indicator.trend.supertrend" }

// VolumeMoneyFlowTool 返回主力资金指标。
type VolumeMoneyFlowTool struct{ spec.Tool }

// NewVolumeMoneyFlowTool 创建资金流工具。
func NewVolumeMoneyFlowTool(config *hithink_finance.Config) *VolumeMoneyFlowTool {
	return &VolumeMoneyFlowTool{spec.Tool{Config: config, Spec: spec.IndicatorVolumeMoneyFlow}}
}

// Name 返回工具名。
func (t *VolumeMoneyFlowTool) Name() string { return "hithink.finance.indicator.volume.money_flow" }

// Ensure tools implement types.Tool
var (
	_ types.Tool = (*MomentumKDJTool)(nil)
	_ types.Tool = (*TrendMATool)(nil)
	_ types.Tool = (*MomentumMacdRsiTool)(nil)
	_ types.Tool = (*VolatilityBBandsTool)(nil)
	_ types.Tool = (*VolatilitySqueezeTool)(nil)
	_ types.Tool = (*TrendSupertrendTool)(nil)
	_ types.Tool = (*VolumeMoneyFlowTool)(nil)
)
