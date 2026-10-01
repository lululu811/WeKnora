// Package pattern provides a technical pattern scanner that reads
// pre-computed indicators from DuckDB and synthesises actionable signals.
package pattern

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type PatternScanTool struct {
	config *hithink_finance.Config
}

func NewPatternScanTool(config *hithink_finance.Config) *PatternScanTool {
	return &PatternScanTool{config: config}
}

func (t *PatternScanTool) Name() string { return "hithink.finance.pattern.scan" }

// Description 必须与 detectSignals 实际发出的信号一一对应。
//
// 描述里曾经写着「PSAR翻转」「VWAP突破」「量价齐升/量价背离」—— 这四个
// readout 在 signals.go 里从来就没有实现：psar / vwap 虽然被 query.go 查出来
// 映射进 row，但 detectSignals 一行都没读。模型按描述去调这个工具、拿不到
// 那个读数，只会被迫自己编。列一个不存在的读数，比不列更糟。
//
// 此后：PSAR 翻转已实现（psarState 判多空侧，并显式跳过 COALESCE 补出来的 0）；
// 量价背离已实现，改名「价量背离」（PVT 与价格 5 日走向相反，name 必须准确到
// 代码里那个字符串）；VWAP突破、量价齐升**仍然没有实现**，所以仍然不列。
//
// 上面这份清单不是靠人肉维护的：signals.go 里的 declaredSignalNames 是唯一真相，
// signals_contract_test.go 双向比对（打出来的 ⊆ 清单 ⊆ 本描述）。所以下面这份
// 文字里的信号名必须与代码逐字相同，包括 Williams%R 的那一个百分号。
//
// 加新信号时：先在 signals.go 实现并登记进 declaredSignalNames，再改这里的清单。
func (t *PatternScanTool) Description() string {
	return fmt.Sprintf(`扫描股票的技术形态信号。从 DuckDB indicators 库的 227 个预计算指标中，
综合检测 %d 种买卖形态，返回结构化信号列表。

下面列的是本工具**真的会返回**的信号名称（与 signals.go 逐条对应）：

- 动量：MACD金叉、MACD死叉、KDJ超卖金叉、KDJ超买死叉、J超卖回中、J超买回中、
  RSI6超卖、RSI6超买、RSI14超卖回升、RSI14超买回落、Stochastic超卖金叉、
  Stochastic超买死叉、CCI超卖、CCI超买、Williams%%R超卖、Williams%%R超买、
  MFI超卖、MFI超买、ER转高效、ER转低效
- 趋势：Supertrend翻转、Supertrend翻空、PSAR翻多、PSAR翻空、ADX多头趋势、
  ADX空头趋势、DI金叉、DI死叉、Aroon多头排列、Aroon空头排列、Vortex金叉、
  Vortex死叉、CHOP进入震荡、CHOP重回趋势、KC中轨多头带、KC中轨空头带、
  MACD动能衰减
- 波动：布林带收口、布林中轨收复、布林中轨跌破、Donchian上轨突破、
  Donchian下轨跌破、Keltner挤压、Keltner挤压向上突破、Keltner挤压向下突破、
  NATR进入高波动档位、NATR退出高波动档位、ATR扩张
- 量价：CMF资金流入、CMF资金流出、价量背离、缩量下跌吸筹、PVI强于NVI吸筹、
  NVI强于PVI派发、上穿VWAP、下穿VWAP
- 统计：Z-Score超卖、Z-Score超买、线性回归上升、线性回归下降
- 蜡烛图：Morning Star晨星、Evening Star暮星、Hammer锤子线、Shooting Star流星、
  Doji十字星、Engulfing吞没、Harami孕线、Piercing刺透、Dark Cloud乌云盖顶、
  Three White Soldiers三白兵、Three Black Crows三乌鸦（共 11 种）

少数信号的触发门槛（其余见 signals.go 注释）：
- J超卖回中/J超买回中：J 越出 0~100 的极值区后回到带内，J<0 看多、J>100 后回落看空。
- ER转高效/ER转低效：效率比跨过 1/3（趋势至少要完成全程三分之一才值得跟随）。
- CHOP进入震荡：CHOP 升破 61.8，趋势信号失真，宜减仓；CHOP重回趋势：跌破 38.2。
- Keltner挤压：布林带收进 Keltner 通道；Keltner挤压向上/向下突破：挤压后首次站上/跌破外轨。
- NATR进入高波动档位：NATR 升破 5%%，止损需放宽、仓位减半；退出后恢复正常。
- 价量背离：5 日价格涨而 PVT 降；缩量下跌吸筹：5 日价格跌而 PVT 升。
- PVI强于NVI吸筹/NVI强于PVI派发：放量日与缩量日的资金强弱发生翻转。

凡是用到价格、通道边界、累计量指标的信号，缺失值（query.go 的 COALESCE 补 0）
都会被显式跳过——缺数据不会伪造出信号，所以某些标的可能少于 %d 条。

**默认只返回"转折类"信号。** 另有 %d 条状态类信号在 20%%~52%% 的窗口上都成立
（线性回归升降、CMF 资金流出、Donchian下轨跌破、Williams%%R超卖、ADX多头趋势、
Aroon空头排列、CCI超卖），报的是**当前状态**而不是转折事件 —— 连着几天读到同一句
话，模型会学会无视它，真正的转折就被淹没。所以它们默认折叠，只在返回的
suppressed 字段里报出**名字和条数**（不静默丢弃：你看到"没有 CCI超卖"
时要知道那个条件其实是成立的）。

用户问"这只票趋势方向如何""资金是流出还是流入""是不是在破位"这类**要状态**
的问题时，用 include_noisy=true 重调；问"有什么转折信号"就用默认。

每个信号的 strength 是 0~1 的相对强度（规则基准分，**尚未**按数据完整度折扣）。

使用示例：
- 默认（只看转折）：thscode="600519.SH", days=10
- 连状态一起看：thscode="600519.SH", days=10, include_noisy=true`,
		len(declaredSignalNames), len(declaredSignalNames), len(noisySignalNames))
}

func (t *PatternScanTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "扫描天数（默认 10，用于检测交叉信号）",
				"default":     10,
			},
			"include_noisy": map[string]interface{}{
				"type": "boolean",
				"description": "是否同时返回高频状态类信号（默认 false）。" +
					"这些信号在 20%~52% 的 bar 上都成立，报的是当前状态而非转折事件，" +
					"默认返回会让真正的转折被淹没。仅在用户明确要看趋势方向/资金流向状态时置 true。",
				"default": false,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *PatternScanTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode      string `json:"thscode"`
		Days         int    `json:"days"`
		IncludeNoisy bool   `json:"include_noisy"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 10
	}
	if params.Days > 60 {
		params.Days = 60
	}

	rows, err := queryIndicatorRows(ctx, t.config, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}
	if len(rows) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未找到指标数据：%s", params.Thscode)}, nil
	}

	all := detectSignals(rows)
	signals, suppressed := all, []Signal(nil)
	if !params.IncludeNoisy {
		signals, suppressed = partitionSignals(all)
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(rows),
		"latest":  rows[0],
		"signals": signals,
		"summary": summarizeSignals(signals),
	}
	// 折叠掉的信号要**如实报数**而不是静默丢弃：不说的话，模型看到"没有
	// MACD动能衰减"会以为那个条件没成立，而它其实成立了。
	if len(suppressed) > 0 {
		names := make([]string, 0, len(suppressed))
		for _, s := range suppressed {
			names = append(names, s.Name)
		}
		output["suppressed"] = map[string]interface{}{
			"count": len(suppressed),
			"names": names,
			"note": "这些信号触发频率过高（>20% 的 bar），报的是状态而非转折，" +
				"默认不返回。需要它们时用 include_noisy=true 重调。",
		}
	}
	out, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(out)}, nil
}

var _ types.Tool = (*PatternScanTool)(nil)
