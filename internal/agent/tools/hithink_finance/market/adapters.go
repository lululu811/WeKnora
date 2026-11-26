package market

import (
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// PriceHistoricalTool 返回前复权历史日线。声明在 spec 包里，这里只转发。
//
// Name() 必须返回字面量：zettaranc 白名单测试用正则从源码里取工具名，认不出从
// 声明里查表。spec 包的契约测试保证这份字面量与声明里的 Name 一致。
type PriceHistoricalTool struct{ spec.Tool }

// NewPriceHistoricalTool 创建历史日线工具。
func NewPriceHistoricalTool(config *hithink_finance.Config) *PriceHistoricalTool {
	return &PriceHistoricalTool{spec.Tool{Config: config, Spec: spec.MarketPriceHistorical}}
}

// Name 返回工具名。
func (t *PriceHistoricalTool) Name() string { return "hithink.finance.market.price.historical" }

// Ensure tool implements types.Tool
var _ types.Tool = (*PriceHistoricalTool)(nil)

// PriceSnapshotTool 返回单只股票的最新行情快照。
type PriceSnapshotTool struct{ spec.Tool }

// NewPriceSnapshotTool 创建行情快照工具。
func NewPriceSnapshotTool(config *hithink_finance.Config) *PriceSnapshotTool {
	return &PriceSnapshotTool{spec.Tool{Config: config, Spec: spec.MarketPriceSnapshot}}
}

// Name 返回工具名。
func (t *PriceSnapshotTool) Name() string { return "hithink.finance.market.price.snapshot" }

// Ensure tool implements types.Tool
var _ types.Tool = (*PriceSnapshotTool)(nil)

// DividendCalendarTool 返回分红派息与除权除息日历。声明在 spec 包里，这里只转发。
type DividendCalendarTool struct{ spec.Tool }

// NewDividendCalendarTool 创建分红日历工具。
func NewDividendCalendarTool(config *hithink_finance.Config) *DividendCalendarTool {
	return &DividendCalendarTool{spec.Tool{Config: config, Spec: spec.MarketDividendCalendar}}
}

// Name 返回工具名。
func (t *DividendCalendarTool) Name() string { return "hithink.finance.market.dividend.calendar" }

// Ensure tool implements types.Tool
var _ types.Tool = (*DividendCalendarTool)(nil)

// DividendYieldScreenerTool 筛高股息率标的。
//
// 查询在声明里；按股息率过滤是 Go 侧的活（股息率是结果行里才算出来的），
// 于是它以 Compose 钩子的形式留在这里。
type DividendYieldScreenerTool struct{ spec.Tool }

// NewDividendYieldScreenerTool 创建股息率筛选工具。
func NewDividendYieldScreenerTool(config *hithink_finance.Config) *DividendYieldScreenerTool {
	t := &DividendYieldScreenerTool{}
	t.Tool = spec.Tool{
		Config:  config,
		Spec:    spec.MarketDividendYieldScreener,
		Compose: composeDividendYield,
	}
	return t
}

// Name 返回工具名。
func (t *DividendYieldScreenerTool) Name() string {
	return "hithink.finance.market.dividend.yield_screener"
}

// Ensure tool implements types.Tool
var _ types.Tool = (*DividendYieldScreenerTool)(nil)

// composeDividendYield 按预估股息率过滤，再组装输出。
//
// 没有 div_yield_pct 的行原样留下：那是数据缺了，不是股息率不达标。
func composeDividendYield(rows any, values map[string]any) (*types.ToolResult, error) {
	sets, _ := rows.(map[string][]map[string]any)
	items := sets["items"]
	year, _ := values["year"].(string)
	minYield, _ := values["min_yield_pct"].(float64)

	filtered := make([]map[string]interface{}, 0, len(items))
	for _, row := range items {
		if y, ok := row["div_yield_pct"].(float64); ok {
			if y >= minYield {
				filtered = append(filtered, row)
			}
		} else {
			filtered = append(filtered, row)
		}
	}
	if len(filtered) == 0 {
		return &types.ToolResult{Success: false,
			Error: fmt.Sprintf("未查询到 %s 年度股息率 >= %.2f%% 的股票", year, minYield)}, nil
	}

	data, err := json.Marshal(map[string]interface{}{
		"year":          year,
		"min_yield_pct": minYield,
		"count":         len(filtered),
		"items":         filtered,
	})
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("结果序列化失败：%v", err)}, nil
	}
	return &types.ToolResult{Success: true, Output: string(data)}, nil
}
