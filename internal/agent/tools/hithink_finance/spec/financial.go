package spec

// financial 家族的声明：三张报表、财务指标明细与估值快照。
//
// 三张报表共用一个时间序常量（FinancialPeriodOrderBy）与一段报告期读法说明
// （FinancialPeriodFieldDoc）——它们曾经各自内联，于是三份文案与三段排序
// 各自漂移过一次。集中在这里，避免再漂。
//
// ⚠️ 财务指标明细（financial.indicator.detail）**不可**换成
// FinancialPeriodOrderBy：v_financial_indicators_detail 视图只有 thscode /
// report / 25 个指标列，period、period_end_ms、fiscal_year、fiscal_period
// 四列全部不存在，套用会直接 binder 报错；那张表的 report 取值实测统一为
// 'YYYY-N'，字典序与时间序一致，ORDER BY report DESC 是正确的。
// docs/tech-debt 里把它记为缺陷、要求"统一排序来源"的那条记载是错的。

// FinancialPeriodOrderBy 是三张报表共用的时间序。
//
// period 列只取 annual / quarterly（报表口径），不是期别；真实期别在
// fiscal_year + fiscal_period，但它们分属两列，跨列比较要写表达式；
// report_date_ms 同步时被污染成同一批毫秒。period_end_ms 是报告期末日期，
// 单列、单调、不受同步污染，作为排序基准最稳。
//
// 第二键 period ASC 把年报排在一季报之前：年报是经审计的完整报表，与同期末的
// Q4 数值相同，但口径更权威。
const FinancialPeriodOrderBy = "ORDER BY period_end_ms DESC, period ASC"

// FinancialPeriodFieldDoc 是三张报表共用的报告期读法说明。
const FinancialPeriodFieldDoc = `报告期读法：period 是报表口径（annual / quarterly），不是期别；真正的期别看
fiscal_year + fiscal_period（如 2026 + Q2 = 2026 年二季报，FY = 年报），
期末日期见 period_end_ms。结果已按期末日期倒序，第一行是最新一期。`

// FinancialBalanceSheet 是资产负债表。
var FinancialBalanceSheet = Spec{
	Name: "hithink.finance.financial.statement.balance",
	Description: `获取资产负债表。返回 assets_total(总资产), total_current_assets(流动资产),
non_current_nets_total(非流动资产净额), total_debt(总债务), holder_equity_total(股东权益),
cash(货币资金), accounts_receivable(应收账款) 等。

` + FinancialPeriodFieldDoc + `
数据区间 2017Q3–2026Q2。使用示例：thscode="600519.SH", periods=4

配合 financial.indicator.detail 里的 assets_debt_ratio（资产负债率）、
current_ratio（流动比率）一起看，能判断偿债能力与财务健康度。

注意：年报（fiscal_period=FY）与四季报是同一报告期的两种口径，数值相同，
返回中若同时出现，以年报为准。`,
	DB: "financials",
	SQL: `
		SELECT period, fiscal_year, fiscal_period, period_end_ms, currency,
		       total_current_assets, non_current_nets_total, assets_total,
		       total_debt, holder_equity_total, cash, accounts_receivable
		FROM v_balance_sheet
		WHERE thscode = ?
		` + FinancialPeriodOrderBy + `
		LIMIT ?
	`,
	Binds: []string{"thscode", "periods"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "periods",
			Type:    "integer",
			Desc:    "报告期数（默认 {default}，最大 {max}）",
			Default: 4,
			Max:     intMax(20),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "periods",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到资产负债表数据：{entity}",
	},
}

// FinancialIncomeStatement 是利润表。
var FinancialIncomeStatement = Spec{
	Name: "hithink.finance.financial.statement.income",
	Description: `获取股票的利润表数据。返回 operating_income(营业收入), operating_costs(营业成本),
operating_profit(营业利润), profit_total(利润总额), net_profit(净利润),
parent_holder_net_profit(归母净利润), basic_eps(基本每股收益) 等。

注意：字段名与旧版不同——营收是 operating_income（不是 revenue），
每股收益是 basic_eps（不是 eps）。

` + FinancialPeriodFieldDoc + `
使用示例：thscode="600519.SH", periods=4`,
	DB: "financials",
	SQL: `
		SELECT period, fiscal_year, fiscal_period, period_end_ms,
		       operating_income, operating_costs, operating_profit, profit_total,
		       net_profit, parent_holder_net_profit, basic_eps
		FROM v_income_statement
		WHERE thscode = ?
		` + FinancialPeriodOrderBy + `
		LIMIT ?
	`,
	Binds: []string{"thscode", "periods"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "periods",
			Type:    "integer",
			Desc:    "报告期数（默认 {default}，最大 {max}）",
			Default: 4,
			Max:     intMax(20),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "periods",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到财务数据：{entity}",
	},
}

// FinancialCashFlow 是现金流量表。
var FinancialCashFlow = Spec{
	Name: "hithink.finance.financial.statement.cashflow",
	Description: `获取现金流量表。返回
act_cash_flow_net(经营活动现金流净额), invest_cash_flow_net(投资活动现金流净额),
financing_cash_flow_net(筹资活动现金流净额), cash_equivalents_net_addition(现金净增加额),
pay_dividends_profits_interest_cash(分配股利利润偿付利息支付的现金),
pay_fixed_assets_etc_cash(购建固定资产等支付的现金) 等。

**分析要点**：把 act_cash_flow_net 和利润表的 net_profit 对比——
经营现金流长期远低于净利润，是利润质量存疑的经典信号（赚的是纸面钱）。
经营现金流为负而利润为正时，通常意味着应收账款在膨胀。

` + FinancialPeriodFieldDoc + `
使用示例：thscode="600519.SH", periods=4`,
	DB: "financials",
	SQL: `
		SELECT period, fiscal_year, fiscal_period, period_end_ms, currency,
		       act_cash_flow_net, invest_cash_flow_net, financing_cash_flow_net,
		       cash_equivalents_net_addition,
		       pay_dividends_profits_interest_cash, pay_fixed_assets_etc_cash
		FROM v_cash_flow_statement
		WHERE thscode = ?
		` + FinancialPeriodOrderBy + `
		LIMIT ?
	`,
	Binds: []string{"thscode", "periods"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "periods",
			Type:    "integer",
			Desc:    "报告期数（默认 {default}，最大 {max}）",
			Default: 4,
			Max:     intMax(20),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "periods",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到现金流量表数据：{entity}",
	},
}

// FinancialIndicatorDetail 是财务能力指标明细。
//
// 它的排序**不可**换成 FinancialPeriodOrderBy：v_financial_indicators_detail
// 视图只有 thscode / report / 25 个指标列，period、period_end_ms、fiscal_year、
// fiscal_period 四列全部不存在，套用会直接 binder 报错；这张表的 report 取值
// 实测统一为 'YYYY-N'（N 为 1-4 单字符），字典序与时间序完全一致，
// ORDER BY report DESC 是正确的。
//
// financial_indicator_detail_test.go 锁住这条，防止后来人"修复"成坏查询。
var FinancialIndicatorDetail = Spec{
	Name: "hithink.finance.financial.indicator.detail",
	Description: `获取个股的财务能力指标，直接给出算好的比率，不需要自己用三表拼。

盈利能力：weighted_avg_roe(加权ROE), deduct_weighted_avg_roe(扣非加权ROE),
          sale_gross_margin(销售毛利率), sale_net_interest_ratio(销售净利率),
          parent_holder_net_profit_yoy(归母净利同比), operating_income_yoy(营收同比)
偿债能力：current_ratio(流动比率), quick_ratio(速动比率), cash_ratio(现金比率),
          assets_debt_ratio(资产负债率), long_term_debt_equity_ratio
运营效率：total_assets_turnover_ratio(总资产周转率), inventory_turnover_ratio(存货周转率),
          receive_account_turnover_ratio(应收账款周转率), current_assets_turnover_ratio
利润质量：net_profit_cash_content(净利润现金含量), cash_operating_index,
          operating_cash_flow_net_divide_income(经营现金流/收入), cash_meet_invest_ratio

**分析要点**：net_profit_cash_content（净利润现金含量）低于 0.8 要警惕，
说明利润没变成真金白银；sale_gross_margin 持续下滑说明议价能力在弱化。

注意：报告期字段是 report（不是 period），格式为 YYYY-N，N 取 1–4，N=4 即年报
（FY 必须映射成 -4，否则与三表 join 会静默返回空）。report 是零填充不足的定长
格式（YYYY 占 4 位、N 占 1 位），因此字典序即时间序，ORDER BY report DESC 正确；
结果第一行是最新一期。数据区间 2024-1 至 2026-2。
使用示例：thscode="600519.SH", periods=4`,
	DB: "financials",
	SQL: `
	SELECT report,
	       operating_income_yoy, operating_profit_yoy,
	       parent_holder_net_profit_yoy, total_assets_growth_ratio,
	       fixed_asset_invest_expansion_ratio,
	       weighted_avg_roe, deduct_weighted_avg_roe,
	       sale_gross_margin, sale_net_interest_ratio,
	       current_ratio, quick_ratio, cash_ratio, earned_interest_multiple,
	       assets_debt_ratio, long_term_debt_equity_ratio,
	       total_assets_turnover_ratio, inventory_turnover_ratio,
	       current_assets_turnover_ratio, receive_account_turnover_ratio,
	       net_profit_cash_content, cash_operating_index,
	       operating_cash_flow_net_divide_income, cash_meet_invest_ratio
	FROM v_financial_indicators_detail
	WHERE thscode = ?
	ORDER BY report DESC
	LIMIT ?
`,
	Binds: []string{"thscode", "periods"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "periods",
			Type:    "integer",
			Desc:    "报告期数（默认 {default}，最大 {max}）",
			Default: 4,
			Max:     intMax(20),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "periods",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到财务指标数据：{entity}。注意该表只覆盖 2024-1 之后的报告期，更早的公司请改用三表工具。",
	},
}

// FinancialValuationSnapshot 是单只股票的最新估值快照。
//
// 输出是那一行本身（加上回显的 thscode），不是数组。
var FinancialValuationSnapshot = Spec{
	Name: "hithink.finance.financial.valuation.snapshot",
	Description: `获取单只股票的最新估值快照。返回 snapshot_date, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm。

注意：本数据源没有市值字段（market_cap / circ_market_cap 均不存在），需要市值请改用
financials.v_financial_indicators 或直接用 hithink.finance.query.sql。

使用示例：thscode="600519.SH"`,
	DB:    "financials",
	SQL:   `SELECT snapshot_date, pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm FROM v_valuation_latest WHERE thscode = ?`,
	Binds: []string{"thscode"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码，如 600519.SH",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
	},
	Envelope: Envelope{
		Single:   true,
		Echo:     []string{"thscode"},
		Empty:    EmptyError,
		EmptyMsg: "股票不存在或无估值数据：{entity}",
	},
}
