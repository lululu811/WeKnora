package market

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// DividendCalendarTool provides ex-dividend and bonus share events.
type DividendCalendarTool struct {
	config *hithink_finance.Config
}

// NewDividendCalendarTool creates a new dividend calendar tool.
func NewDividendCalendarTool(config *hithink_finance.Config) *DividendCalendarTool {
	return &DividendCalendarTool{config: config}
}

func (t *DividendCalendarTool) Name() string {
	return "hithink.finance.market.dividend.calendar"
}

func (t *DividendCalendarTool) Description() string {
	return `查询股票分红派息与除权除息日历（送转股、派发现金）。
支持查询单只个股历史分红派息记录，或全市场在特定时间区间的除权除息日历，支持按现金派息金额或送转股比例筛选。

返回字段：
- thscode: 股票代码
- name: 股票名称
- ex_date: 除权除息日（YYYY-MM-DD）
- dividend_per_share: 每股派息金额（税前元）
- per_share_bonus: 每股送转比例（如 0.49 表示每 10 股送转 4.9 股）
- allotment_ratio: 配股比例

使用示例：
- 查询贵州茅台历史分红：thscode="600519.SH"
- 查询 2026 年以来的高送转：min_bonus=0.3, start_date="2026-01-01"
- 查询近期全市场分红日历：start_date="2026-06-01", limit=30`
}

func (t *DividendCalendarTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "可选。股票代码，如 600519.SH、000001.SZ。指定时查询该股分红历史",
			},
			"start_date": map[string]interface{}{
				"type":        "string",
				"description": "可选。起始除息日（YYYY-MM-DD），默认当年年初",
			},
			"end_date": map[string]interface{}{
				"type":        "string",
				"description": "可选。截止除息日（YYYY-MM-DD）",
			},
			"min_dividend": map[string]interface{}{
				"type":        "number",
				"description": "可选。每股最低派现金额（元）",
				"default":     0,
			},
			"min_bonus": map[string]interface{}{
				"type":        "number",
				"description": "可选。每股最低送转股比例（如 0.3 表示每10股送转3股以上）",
				"default":     0,
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "可选。返回数量限制（默认 50，最大 200）",
				"default":     50,
			},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *DividendCalendarTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode     string  `json:"thscode"`
		StartDate   string  `json:"start_date"`
		EndDate     string  `json:"end_date"`
		MinDividend float64 `json:"min_dividend"`
		MinBonus    float64 `json:"min_bonus"`
		Limit       int     `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 {
		params.Limit = 50
	}
	if params.Limit > 200 {
		params.Limit = 200
	}

	var query string
	var bindArgs []interface{}

	if params.Thscode != "" {
		query = `
			SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio
			FROM raw_adjustment_events e
			LEFT JOIN v_symbol s ON e.thscode = s.thscode
			WHERE e.thscode = ?
			ORDER BY e.ex_date DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.Thscode, params.Limit)
	} else {
		if params.StartDate == "" {
			params.StartDate = fmt.Sprintf("%d-01-01", time.Now().Year())
		}
		if params.EndDate == "" {
			params.EndDate = "2099-12-31"
		}
		query = `
			SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio
			FROM raw_adjustment_events e
			LEFT JOIN v_symbol s ON e.thscode = s.thscode
			WHERE e.ex_date >= ? AND e.ex_date <= ? AND e.dividend_per_share >= ? AND e.per_share_bonus >= ?
			ORDER BY e.ex_date DESC
			LIMIT ?
		`
		bindArgs = append(bindArgs, params.StartDate, params.EndDate, params.MinDividend, params.MinBonus, params.Limit)
	}

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "market", query, bindArgs...)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.market.dividend.calendar")}, nil
	}

	if len(results) == 0 {
		return &types.ToolResult{Success: false, Error: "未查询到符合条件的分红除权记录"}, nil
	}

	output := map[string]interface{}{
		"count": len(results),
		"items": results,
	}
	if params.Thscode != "" {
		output["thscode"] = params.Thscode
	}

	data, err := json.Marshal(output)
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("结果序列化失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(data),
	}, nil
}

// DividendYieldScreenerTool screens high dividend yield stocks.
type DividendYieldScreenerTool struct {
	config *hithink_finance.Config
}

// NewDividendYieldScreenerTool creates a new dividend yield screener tool.
func NewDividendYieldScreenerTool(config *hithink_finance.Config) *DividendYieldScreenerTool {
	return &DividendYieldScreenerTool{config: config}
}

func (t *DividendYieldScreenerTool) Name() string {
	return "hithink.finance.market.dividend.yield_screener"
}

func (t *DividendYieldScreenerTool) Description() string {
	return `筛选高股息率红利标的（基于最新现金分红与最新收盘价计算真实股息率）。
适用于防御性红利策略、红利低波选股以及高现金回报优质企业挖掘。

返回字段：
- thscode: 股票代码
- name: 股票名称
- ex_date: 除权除息日
- dividend_per_share: 每股派现金额
- per_share_bonus: 每股送转比例
- close: 最新收盘价
- div_yield_pct: 预估股息率百分比（%）

使用示例：
- 筛选 2026 年股息率大于 5% 的高息标的：year="2026", min_yield_pct=5.0
- 筛选高息股 Top 30：min_yield_pct=4.0, limit=30`
}

func (t *DividendYieldScreenerTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"year": map[string]interface{}{
				"type":        "string",
				"description": "分红年度（YYYY），默认 2026",
				"default":     "2026",
			},
			"min_yield_pct": map[string]interface{}{
				"type":        "number",
				"description": "最低预估股息率（%，默认 3.0）",
				"default":     3.0,
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 50）",
				"default":     50,
			},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *DividendYieldScreenerTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Year        string  `json:"year"`
		MinYieldPct float64 `json:"min_yield_pct"`
		Limit       int     `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Year == "" {
		params.Year = "2026"
	}
	if params.MinYieldPct <= 0 {
		params.MinYieldPct = 3.0
	}
	if params.Limit <= 0 {
		params.Limit = 50
	}
	if params.Limit > 200 {
		params.Limit = 200
	}

	startDate := fmt.Sprintf("%s-01-01", params.Year)

	query := `
		WITH latest_p AS (
			SELECT thscode, close
			FROM v_daily_qfq
			WHERE date = (SELECT max(date) FROM v_daily_qfq)
		)
		SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, p.close,
		       ROUND(e.dividend_per_share / NULLIF(p.close, 0) * 100, 2) AS div_yield_pct
		FROM raw_adjustment_events e
		JOIN v_symbol s ON e.thscode = s.thscode
		LEFT JOIN latest_p p ON e.thscode = p.thscode
		WHERE e.ex_date >= ? AND e.dividend_per_share > 0
		ORDER BY div_yield_pct DESC
		LIMIT ?
	`

	results, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "market", query, startDate, params.Limit)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, "hithink.finance.market.dividend.yield_screener")}, nil
	}

	// Filter by min_yield_pct
	filtered := make([]map[string]interface{}, 0, len(results))
	for _, row := range results {
		if y, ok := row["div_yield_pct"].(float64); ok {
			if y >= params.MinYieldPct {
				filtered = append(filtered, row)
			}
		} else {
			filtered = append(filtered, row)
		}
	}

	if len(filtered) == 0 {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("未查询到 %s 年度股息率 >= %.2f%% 的股票", params.Year, params.MinYieldPct)}, nil
	}

	data, err := json.Marshal(map[string]interface{}{
		"year":          params.Year,
		"min_yield_pct": params.MinYieldPct,
		"count":         len(filtered),
		"items":         filtered,
	})
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("结果序列化失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(data),
	}, nil
}

// Ensure tools implement the Tool interface.
var (
	_ types.Tool = (*DividendCalendarTool)(nil)
	_ types.Tool = (*DividendYieldScreenerTool)(nil)
)
