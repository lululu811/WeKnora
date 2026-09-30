package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// ScreenerTool performs stock screening using the Zettaranc system via python-service HTTP API.
type ScreenerTool struct {
	client *HTTPClient
}

// NewScreenerTool creates a new screener tool backed by python-service.
func NewScreenerTool(client *HTTPClient) *ScreenerTool {
	return &ScreenerTool{client: client}
}

func (t *ScreenerTool) Name() string {
	return "zettaranc.screener"
}

func (t *ScreenerTool) Description() string {
	return `全市场集合式选股。一条 SQL 取回全市场指标 + 价量后在本地判定，
不走逐只扫描（逐只是 5,571 次往返、36~51 秒）。

14 个策略，按方向分三类。**先看用户想干什么再选策略**：

看涨（超卖共振、买点类）
  oversold_combo / B2 / SB1 / shaofu / limit_up / vol_breakout / donchian_break
  / hammer_reversal / vortex_bull

看跌（超买、死叉、空头排列）—— 用于**规避持仓**，比选新票更常用
  overbought_combo（oversold_combo 的镜像） / trend_down / anomaly
  / shooting_star_reversal

方向无关（波动本身无方向，涨途中也会出现）
  volatility_spike

名字对不上号时以本清单为准：oversold_combo 是**选股策略**（≥2 个超卖信号
共振）；K 线形态标注里的 B1（建仓波后第一次缩量回调、J<13）是另一回事，
走 annotate 接口，不在本工具的策略里。

形态信号只回答"图形像不像"，不回答"会不会暴雷"。可选筛选构成第二道关：

板块：sector="半导体" —— 限定在该板块成分股内
风险代理（出处 financials.v_balance_sheet，覆盖全部 A 股）：
  max_debt_ratio        资产负债率上限，本地 p50=0.40 p90=0.71
  min_current_ratio     流动比率下限，本地 p50=1.50 p10=0.60
  max_receivable_ratio  应收账款占总资产上限
开关：
  require_profit  最新期归母净利润为正（financials.v_income_statement）
  exclude_st      排除 ST/*ST

【重要】风险维度**只覆盖上面这些**。商誉、股权质押、大股东减持、审计意见、
监管处罚、业绩预告本地数据源里没有，所以**没有**对应参数。回答用户"有没有
暴雷风险"时必须说清这一点，不能因为资产负债率低就说它安全。

ST 是按 v_symbol.name 匹配识别的（库里没有 ST 标记列），全 A 204 只。

返回里的 scanned / scanned_from_universe / truncated / risk_filter /
warnings 都是可信度信息，汇报结论时应一并说明。`
}

func (t *ScreenerTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"strategy": map[string]interface{}{
				"type":        "string",
				"description": "选股策略",
				"enum":        ScreenerStrategies(),
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量（默认 20，最大 100）",
				"default":     20,
			},
			"sector": map[string]interface{}{
				"type":        "string",
				"description": "板块名片段，如「半导体」「白酒」。限定候选集在该板块成分股内。",
			},
			"max_debt_ratio": map[string]interface{}{
				"type":        "number",
				"description": "资产负债率上限，如 0.5 表示剔除负债超过 50% 的票。本地分布 p50=0.40 p90=0.71。出处 financials.v_balance_sheet",
			},
			"min_current_ratio": map[string]interface{}{
				"type":        "number",
				"description": "流动比率下限，如 1.0。本地分布 p50=1.50 p10=0.60。出处 financials.v_balance_sheet",
			},
			"max_receivable_ratio": map[string]interface{}{
				"type":        "number",
				"description": "应收账款占总资产上限。出处 financials.v_balance_sheet",
			},
			"require_profit": map[string]interface{}{
				"type":        "boolean",
				"description": "要求最新期归母净利润为正。出处 financials.v_income_statement",
			},
			"exclude_st": map[string]interface{}{
				"type":        "boolean",
				"description": "排除 ST/*ST。库里没有 ST 标记列，只能按 market.v_symbol.name 匹配，全 A 204 只",
			},
		},
		"required": []string{"strategy"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ScreenerTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Strategy           string   `json:"strategy"`
		Limit              int      `json:"limit"`
		Sector             string   `json:"sector"`
		MaxDebtRatio       *float64 `json:"max_debt_ratio"`
		MinCurrentRatio    *float64 `json:"min_current_ratio"`
		MaxReceivableRatio *float64 `json:"max_receivable_ratio"`
		RequireProfit      bool     `json:"require_profit"`
		ExcludeST          bool     `json:"exclude_st"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	if params.Strategy == "" {
		return &types.ToolResult{
			Success: false,
			Error:   "参数错误：strategy 不能为空",
		}, nil
	}

	if params.Limit <= 0 {
		params.Limit = 20
	}
	if params.Limit > 100 {
		params.Limit = 100
	}

	// 只把**用户真的给了**的筛选条件传下去。指针为 nil 就不设，
	// 服务端也就不会去查 financials —— 默认调用不多付一次跨库查询。
	var opts []ScreenOption
	if params.Sector != "" {
		opts = append(opts, WithSector(params.Sector))
	}
	if params.MaxDebtRatio != nil {
		opts = append(opts, WithMaxDebtRatio(*params.MaxDebtRatio))
	}
	if params.MinCurrentRatio != nil {
		opts = append(opts, WithMinCurrentRatio(*params.MinCurrentRatio))
	}
	if params.MaxReceivableRatio != nil {
		opts = append(opts, WithMaxReceivableRatio(*params.MaxReceivableRatio))
	}
	if params.RequireProfit {
		opts = append(opts, WithRequireProfit())
	}
	if params.ExcludeST {
		opts = append(opts, WithExcludeST())
	}

	// Call python-service HTTP API
	resp, err := t.client.Screen(ctx, params.Strategy, params.Limit, opts...)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("选股失败：%v。请检查：1) 策略名称是否正确；2) 数据是否已同步", err),
		}, nil
	}

	// Format output
	output := map[string]interface{}{
		"strategy": params.Strategy,
		"limit":    params.Limit,
		"data":     resp,
	}

	outputJSON, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(outputJSON),
	}, nil
}

// Ensure ScreenerTool implements the Tool interface.
var _ types.Tool = (*ScreenerTool)(nil)
