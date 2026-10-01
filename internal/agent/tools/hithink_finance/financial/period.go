// Package financial 提供三张报表工具的 SQL 与说明文本。
package financial

// periodOrderBy 是三张报表共用的时间序，理由见下方注释。
//
// 三张表原本各自内联 `ORDER BY period DESC`，那是把「报表口径」当成了
// 「报告期」在排：period 列只取 annual / quarterly 两个值，financials 库里
// 17 万行 quarterly 全部同值，DuckDB 的排序退化成任意顺序。结果是
// `periods=4` 返回的是**任意** 4 期而不是最近 4 期——对单期查询（periods=1）
// 反而碰巧能出一个数，于是长期没被发现。依赖时间序列的下游（成长性同比、
// 趋势判断）拿到的就是随机样本。
//
// 报告期真正的载体是 fiscal_year + fiscal_period（Q1–Q4、FY），但它们分属
// 两列，跨列比较要写表达式；report_date_ms 也不能用——同步时它被污染成
// 同一批毫秒（实测 2025FY 与 2026Q2 都是 1786723200000）。period_end_ms 是
// 报告期末日期，单列、单调、且不受同步污染，作为排序基准最稳。
//
// 第二键 period ASC 把年报排在一季报之前：年报是经审计的完整报表，与同
// 期末的 Q4 数值相同（FY 与 Q4 的 period_end_ms 实测一致，均为年末日），
// 但口径更权威，同期取它。
const periodOrderBy = "ORDER BY period_end_ms DESC, period ASC"

// 三个报表的查询语句在此集中定义，工具的 Execute 直接引用。
//
// 提成常量的原因是可测：CheckSyncWindow 依赖 time.Now()，无法在不改动
// 公共 API 的前提下在测试里稳定放行，测试若只能靠断言内联字符串就锁不住
// 排序。SQL 作为纯数据后，period_test.go 可以直接断言它本身。
const (
	balanceSheetQuery = `
		SELECT period, fiscal_year, fiscal_period, period_end_ms, currency,
		       total_current_assets, non_current_nets_total, assets_total,
		       total_debt, holder_equity_total, cash, accounts_receivable
		FROM v_balance_sheet
		WHERE thscode = ?
		` + periodOrderBy + `
		LIMIT ?
	`

	incomeStatementQuery = `
		SELECT period, fiscal_year, fiscal_period, period_end_ms,
		       operating_income, operating_costs, operating_profit, profit_total,
		       net_profit, parent_holder_net_profit, basic_eps
		FROM v_income_statement
		WHERE thscode = ?
		` + periodOrderBy + `
		LIMIT ?
	`

	cashFlowQuery = `
		SELECT period, fiscal_year, fiscal_period, period_end_ms, currency,
		       act_cash_flow_net, invest_cash_flow_net, financing_cash_flow_net,
		       cash_equivalents_net_addition,
		       pay_dividends_profits_interest_cash, pay_fixed_assets_etc_cash
		FROM v_cash_flow_statement
		WHERE thscode = ?
		` + periodOrderBy + `
		LIMIT ?
	`
)

// 三个工具的 Description 都要向模型交代「怎么读报告期」——旧文案统一写成
// 「报告期字段是 period（形如 2026Q2）」，而 period 实际是 annual/quarterly，
// 模型据此推理必然出错。集中在这里，避免三份文案再次漂移。
const (
	periodFieldDoc = `报告期读法：period 是报表口径（annual / quarterly），不是期别；真正的期别看
fiscal_year + fiscal_period（如 2026 + Q2 = 2026 年二季报，FY = 年报），
期末日期见 period_end_ms。结果已按期末日期倒序，第一行是最新一期。`

	balanceSheetDesc = `获取资产负债表。返回 assets_total(总资产), total_current_assets(流动资产),
non_current_nets_total(非流动资产净额), total_debt(总债务), holder_equity_total(股东权益),
cash(货币资金), accounts_receivable(应收账款) 等。

` + periodFieldDoc + `
数据区间 2017Q3–2026Q2。使用示例：thscode="600519.SH", periods=4

配合 financial.indicator.detail 里的 assets_debt_ratio（资产负债率）、
current_ratio（流动比率）一起看，能判断偿债能力与财务健康度。

注意：年报（fiscal_period=FY）与四季报是同一报告期的两种口径，数值相同，
返回中若同时出现，以年报为准。`

	incomeStatementDesc = `获取股票的利润表数据。返回 operating_income(营业收入), operating_costs(营业成本),
operating_profit(营业利润), profit_total(利润总额), net_profit(净利润),
parent_holder_net_profit(归母净利润), basic_eps(基本每股收益) 等。

注意：字段名与旧版不同——营收是 operating_income（不是 revenue），
每股收益是 basic_eps（不是 eps）。

` + periodFieldDoc + `
使用示例：thscode="600519.SH", periods=4`

	cashFlowDesc = `获取现金流量表。返回
act_cash_flow_net(经营活动现金流净额), invest_cash_flow_net(投资活动现金流净额),
financing_cash_flow_net(筹资活动现金流净额), cash_equivalents_net_addition(现金净增加额),
pay_dividends_profits_interest_cash(分配股利利润偿付利息支付的现金),
pay_fixed_assets_etc_cash(购建固定资产等支付的现金) 等。

**分析要点**：把 act_cash_flow_net 和利润表的 net_profit 对比——
经营现金流长期远低于净利润，是利润质量存疑的经典信号（赚的是纸面钱）。
经营现金流为负而利润为正时，通常意味着应收账款在膨胀。

` + periodFieldDoc + `
使用示例：thscode="600519.SH", periods=4`
)
