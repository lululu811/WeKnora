package query

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

type SQLQueryTool struct {
	config *hithink_finance.Config
}

func NewSQLQueryTool(config *hithink_finance.Config) *SQLQueryTool {
	return &SQLQueryTool{config: config}
}

func (t *SQLQueryTool) Name() string {
	return "hithink.finance.query.sql"
}

func (t *SQLQueryTool) Description() string {
	return `执行只读 SQL 查询。支持查询所有 DuckDB 数据库的 v_* 视图。

安全限制：只允许 SELECT 查询，默认 LIMIT 1000 行

【market】行情
- v_daily_qfq(thscode, date, open, high, low, close, volume, turnover, forward_factor, currency, interval)
- v_daily_hfq / v_daily 同结构（不复权 / 原始）
- v_symbol(thscode, name, market, sector) — 代码与名称对照，做 join 用这个

【indicators】技术指标（10GB，每天 19:00 刷新）
- v_indicators_daily(thscode, date, +200+ 指标列)，列族前缀：
  momentum_*（kdj_9_3_k/d/j、rsi_14、macd_12_26_9_macd…）
  overlap_*（ma、ema、boll…）  trend_*  volume_*  volatility_*
  cycles_*（横向/纵向周期）  statistic_*（beta、相关性）  candles_cdl_*（K线形态）
  ⚠ 该表没有 price 列；要价量必须与 market.v_daily_qfq 按 (thscode, date) join

【financials】基本面（全部以 thscode + period 为键，period 形如 2026Q2）
- v_income_statement(thscode, period, fiscal_year, fiscal_period, operating_income,
  operating_profit, profit_total, net_profit, parent_holder_net_profit, basic_eps, income_tax_expense)
- v_balance_sheet(thscode, period, assets_total, total_current_assets, total_debt,
  holder_equity_total, cash, accounts_receivable)
- v_cash_flow_statement(thscode, period, act_cash_flow_net, invest_cash_flow_net,
  financing_cash_flow_net, cash_equivalents_net_addition, pay_dividends_profits_interest_cash)
- v_financial_indicators(thscode, report, abilities_json) — 能力指标打包在 JSON 里
- v_valuation_latest(snapshot_date, thscode, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm)

【index】指数与行业/概念归属（做行业对比、板块轮动的核心）
- v_index_universe(thscode, name, tag) — tag 取值 cn_concept / industry / region / tszs
- v_index_constituents(index_thscode, thscode, ticker, name) — 某指数/板块的成分股
  ⚠ 只有当前成分快照，没有历史成分，做历史回测会有幸存者偏差
- v_index_daily(thscode, trade_date, open, high, low, close, volume, turnover) — 指数/板块行情

【special】特色资金与情绪数据（按 trade_date / snapshot_date + thscode）
- v_limit_up_pool / v_limit_down_pool(trade_date, thscode, name, last_price, price_change_ratio,
  first_limit_time, last_limit_time) — 涨停/跌停池
- v_limit_break_pool(trade_date, thscode, name, last_price, open_times, price_change_ratio)
  — 炸板池 = 冲高回落 = 派发信号
- v_auction_snapshot(snapshot_date, thscode, auction_price, auction_pct, auction_volume,
  auction_unmatched, float_market_cap) — 集合竞价，盘前信号
- v_dragon_tiger(trade_date, board_type, thscode, net_value, net_rate, buy_value,
  sell_value, org_net_value, hot_rank) — 龙虎榜
- v_hot_stock(capture_date, period, rank, thscode, heat, rank_change, rank_trend) — 热股榜
- v_anomaly_list(capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list)

【fund】基金与 ETF
- v_etf_daily(thscode, date, open, high, low, close, volume, turnover) — ETF 日线，可做 ETF 轮动
- v_etf_universe / v_etf_latest — ETF 列表与最新快照
- v_fund_nav(thscode, date, nav, accum_nav)、v_fund_profile(thscode, name, ...)、v_fund_holdings(基金重仓股)

【futures】期货
- v_futures_daily(thscode, trade_date, open, high, low, close, volume, open_interest)
- v_futures_varieties、v_futures_contracts、v_futures_latest、v_futures_intraday
- 品种多空持仓只有 raw_futures_positions_variety（无 v_ 视图）

使用示例：
- 查行情：sql="SELECT date, close, turnover FROM v_daily_qfq WHERE thscode='600519.SH' LIMIT 5", db="market"
- 查 KDJ：sql="SELECT date, momentum_kdj_9_3_k AS k FROM v_indicators_daily WHERE thscode='600519.SH' LIMIT 10", db="indicators"
- 这只票属于哪些行业/概念：sql="SELECT name, tag FROM v_index_universe WHERE thscode='600519.SH'", db="index"
- 同行业还有哪些票：sql="SELECT c.thscode, c.name FROM v_index_constituents c JOIN v_index_universe u ON c.index_thscode=u.thscode WHERE u.tag='industry' AND u.name='半导体'", db="index"
- 行业板块近20日涨幅：sql="SELECT trade_date, close FROM v_index_daily WHERE thscode=(SELECT thscode FROM v_index_universe WHERE tag='industry' AND name='半导体' LIMIT 1) ORDER BY trade_date DESC LIMIT 20", db="index"
- 利润率趋势：sql="SELECT period, operating_income, parent_holder_net_profit FROM v_income_statement WHERE thscode='600519.SH' ORDER BY period DESC LIMIT 8", db="financials"
- 现金流质量：sql="SELECT period, act_cash_flow_net, parent_holder_net_profit FROM v_cash_flow_statement WHERE thscode='600519.SH' ORDER BY period DESC LIMIT 8", db="financials"
- 最近炸板：sql="SELECT trade_date, thscode, name, open_times FROM v_limit_break_pool ORDER BY trade_date DESC LIMIT 20", db="special"`
}

func (t *SQLQueryTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"sql": map[string]interface{}{
				"type":        "string",
				"description": "SQL 查询语句（只读，只能 SELECT）",
			},
			"db": map[string]interface{}{
				"type":        "string",
				"description": "数据库名称",
				"enum":        hithink_finance.DBNames(),
			},
		},
		"required": []string{"sql", "db"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SQLQueryTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		SQL string `json:"sql"`
		DB  string `json:"db"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.SQL == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：sql 不能为空"}, nil
	}
	if params.DB == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：db 不能为空"}, nil
	}

	if err := hithink_finance.ValidateReadOnlySQL(params.SQL); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	params.SQL = hithink_finance.EnsureLimit(params.SQL, hithink_finance.DefaultLimit)

	results, err := hithink_finance.QueryDuckDB(ctx, t.config, params.DB, params.SQL)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	output := map[string]interface{}{
		"db":    params.DB,
		"count": len(results),
		"rows":  results,
	}

	outputJSON, _ := json.MarshalIndent(output, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SQLQueryTool)(nil)
