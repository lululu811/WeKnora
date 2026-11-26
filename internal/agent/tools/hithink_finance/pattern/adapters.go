package pattern

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// PatternScanTool 扫技术形态信号。
//
// 声明在 spec 包里（名字、说明、参数、窗口），取数与识别留在这一边——
// 识别要跨两个 DuckDB 按日期合并，表达不成数据。
type PatternScanTool struct{ spec.Tool }

// NewPatternScanTool 创建形态扫描工具。
func NewPatternScanTool(config *hithink_finance.Config) *PatternScanTool {
	t := &PatternScanTool{}
	t.Tool = spec.Tool{
		Config:  config,
		Spec:    spec.PatternScan,
		Fetch:   fetchScanRows,
		Compose: composeScan,
	}
	return t
}

// Name 返回工具名。
func (t *PatternScanTool) Name() string { return "hithink.finance.pattern.scan" }

// Ensure tool implements types.Tool
var _ types.Tool = (*PatternScanTool)(nil)

// fetchScanRows 是取数：查指标库与行情库，再按日期在 Go 里合并。
//
// 参数解析与窗口钳制在 spec 那边（Fetch 收到的是解析好的 values），这里只负责
// 把值取出来用。
func fetchScanRows(
	ctx context.Context,
	cfg *hithink_finance.Config,
	values map[string]any,
) (any, error) {
	thscode, _ := values["thscode"].(string)
	days, _ := values["days"].(int)
	return queryIndicatorRows(ctx, cfg, thscode, days)
}

// composeScan 是这个工具自己的计算：识别信号、折叠状态类信号、组装输出。
func composeScan(rows any, values map[string]any) (*types.ToolResult, error) {
	rs, _ := rows.([]row)
	thscode, _ := values["thscode"].(string)
	includeNoisy, _ := values["include_noisy"].(bool)

	if len(rs) == 0 {
		return &types.ToolResult{Success: false,
			Error: fmt.Sprintf("未找到指标数据：%s", thscode)}, nil
	}

	all := detectSignals(rs)
	signals, suppressed := all, []Signal(nil)
	if !includeNoisy {
		signals, suppressed = partitionSignals(all)
	}

	output := map[string]interface{}{
		"thscode": thscode,
		"days":    len(rs),
		"latest":  rs[0],
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
