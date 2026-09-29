package zettaranc

import (
	"context"
	"encoding/json"

	"github.com/Tencent/WeKnora/internal/types"
)

// BacktestTool runs strategy backtests using the Zettaranc system via python-service HTTP API.
type BacktestTool struct {
	client *HTTPClient
}

// NewBacktestTool creates a new backtest tool backed by python-service.
func NewBacktestTool(client *HTTPClient) *BacktestTool {
	return &BacktestTool{client: client}
}

func (t *BacktestTool) Name() string {
	return "zettaranc.backtest"
}

func (t *BacktestTool) Description() string {
	return `【未实现，调用会直接失败】Z哥战法的策略回测。

真实回测（逐日重放信号、持仓与撮合、绩效统计）尚未实现。本工具过去会返回
**选股结果**冒充回测结果，现已停止该行为。

替代方案：
- 要"从全市场挑出符合某战法的票" → zettaranc.screener
- 要"看某只票的趋势/量价/形态/支撑阻力" → zettaranc.analyze
- 要"取历史 OHLCV 自行核算收益" → hithink.finance.market.price.historical`
}

func (t *BacktestTool) Parameters() json.RawMessage {
	// 参数 schema 保留是为了让工具仍可被发现并给出明确报错。
	// 描述里已写明"调用会直接失败"，模型不该再构造参数调它。
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"strategy": map[string]interface{}{
				"type":        "string",
				"description": "策略名称。当前不支持，调用一律失败。",
			},
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码。当前不支持，调用一律失败。",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "回测天数。当前不支持，调用一律失败。",
			},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *BacktestTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	// 这个工具过去会**假装回测**：收了 thscode 和 days 两个参数却完全不读，
	// 实际调的是 t.client.Screen() —— 选股接口，然后把它返回的一堆无关股票
	// 当成"回测结果"吐给模型。用户问「回测 600519 近 250 天」，模型拿到的是
	// 一张全市场候选票的列表，只有 JSON 深处一句 note 写着"迁移中"。
	//
	// 这比没有这个工具更糟：没有的话模型会去用 screener，有了的话模型会
	// 拿一个与问题无关的结果去回答，而且看上去像是算出来的。
	//
	// 现在明确拒绝。实现真正的回测需要逐日重放信号 + 持仓与撮合规则，
	// 那是独立的一件事，不该由一个"返回选股结果"的函数假装。
	return &types.ToolResult{
		Success: false,
		Error: "zettaranc.backtest 尚未实现真实回测，已停止返回替代数据。\n" +
			"当前可用替代：\n" +
			"  · zettaranc.screener —— 全市场按策略选候选股（真实指标 + 价量）\n" +
			"  · zettaranc.analyze —— 单只标的的趋势/量价/形态/支撑阻力分析\n" +
			"  · hithink.finance.market.price.historical —— 取历史 OHLCV 自行核算收益",
	}, nil
}

// Ensure BacktestTool implements the Tool interface.
var _ types.Tool = (*BacktestTool)(nil)
