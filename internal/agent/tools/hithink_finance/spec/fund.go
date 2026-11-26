package spec

// fund 家族的声明：查基金与 ETF 的库（v_etf_latest / v_fund_holders /
// v_fund_holdings）。
//
// 这一族四个工具的 limit 都是「超范围回缺省值」语义——请求 200 条不是拿到 100
// 条，而是拿到缺省的 20 条。所以 Max 记的是判据，不发布到 schema 的 maximum。
var (
	// FundETFSnapshot 是全市场 ETF 行情快照。
	FundETFSnapshot = Spec{
		Name: "hithink.finance.fund.etf.snapshot",
		Description: `获取全市场 ETF 最新行情快照（包含宽基 ETF、行业主题 ETF、商品 ETF 等）。

支持按成交额排序（发现活跃 ETF）或按涨幅排序。`,
		DB:  "fund",
		SQL: `SELECT thscode, ticker, name, exchange, asset_type, trade_date, last_price, change_pct, volume, turnover, turnover_ratio_pct FROM v_etf_latest ORDER BY {order} LIMIT {limit}`,
		Params: []Param{
			{
				Name:    "order_by",
				Type:    "string",
				Desc:    "排序方式：'turnover' (成交额优先，默认) 或 'gainers' (涨幅优先) 或 'losers' (跌幅优先)",
				Default: "turnover",
			},
			{
				Name:    "limit",
				Type:    "integer",
				Desc:    "返回数量限制（默认 {default}，最大 {max}）",
				Default: 30,
				Max:     intMax(100),
				Over:    OverRangeReset,
			},
		},
		OrderBy: &OrderBy{
			Param:   "order_by",
			Default: "turnover DESC",
			By: map[string]string{
				"gainers": "change_pct DESC",
				"losers":  "change_pct ASC",
			},
		},
		Envelope: Envelope{Bare: true},
	}

	// FundETFInstitutionalRanking 是机构与散户占比的排行。
	FundETFInstitutionalRanking = Spec{
		Name:        "hithink.finance.fund.etf.institutional_ranking",
		Description: `查询全市场基金/ETF 机构投资者占比排行（机构重仓定价权 ETF vs 散户扎堆 ETF）。`,
		DB:          "fund",
		SQL:         `SELECT fund_thscode, merge_scope, report_date_ms, ins_position, holder_amount, avg_holder_share, psnl_rate, mgmt_staff_hold_rate FROM v_fund_holders WHERE ins_position IS NOT NULL ORDER BY {order} LIMIT {limit}`,
		Params: []Param{
			{
				Name:    "order_by",
				Type:    "string",
				Desc:    "排序依据：'institution' (机构占比从高到低，默认) 或 'retail' (散户占比从高到低)",
				Default: "institution",
			},
			{
				Name:    "limit",
				Type:    "integer",
				Desc:    "返回数量限制（默认 {default}，最大 {max}）",
				Default: 20,
				Max:     intMax(50),
				Over:    OverRangeReset,
			},
		},
		OrderBy: &OrderBy{
			Param:   "order_by",
			Default: "ins_position DESC",
			By: map[string]string{
				"retail": "psnl_rate DESC",
			},
		},
		Envelope: Envelope{Bare: true},
	}

	// FundHoldingsPortfolio 是指定基金的重仓股票组合。
	FundHoldingsPortfolio = Spec{
		Name:        "hithink.finance.fund.holdings.portfolio",
		Description: `查询指定基金或 ETF 的重仓股票组合（持仓明细、持股市值占比、增减持变动等）。`,
		DB:          "fund",
		SQL:         `SELECT fund_thscode, stock_thscode, stock_name, hold_ratio, position_count, security_market_value_rate_pct, period_increase_rate_pct, investment_rank FROM v_fund_holdings WHERE fund_thscode = ? ORDER BY hold_ratio DESC LIMIT {limit}`,
		Binds:       []string{"fund_thscode"},
		Params: []Param{
			{
				Name:     "fund_thscode",
				Type:     "string",
				Desc:     "基金代码，如 510300.SH (华泰柏瑞沪深300ETF) 或 110011.OF (易方达中小盘)",
				Required: true,
				Trim:     true,
				EmptyMsg: "fund_thscode 不能为空",
			},
			{
				Name:    "limit",
				Type:    "integer",
				Desc:    "返回数量限制（默认 {default}，最大 {max}）",
				Default: 20,
				Max:     intMax(100),
				Over:    OverRangeReset,
			},
		},
		Envelope: Envelope{Bare: true},
	}

	// FundHoldingsStock 是持有指定股票的基金明细（反向穿透）。
	FundHoldingsStock = Spec{
		Name: "hithink.finance.fund.holdings.stock",
		Description: `穿透查询持有指定 A 股股票的公募基金与 ETF 重仓明细。

用于分析某只股票的机构资金底仓、重仓基金列表及持股比例（机构筹码结构）。`,
		DB:    "fund",
		SQL:   `SELECT fund_thscode, stock_thscode, stock_name, hold_ratio, position_count, security_market_value_rate_pct, period_increase_rate_pct, investment_rank FROM v_fund_holdings WHERE stock_thscode = ? ORDER BY hold_ratio DESC LIMIT {limit}`,
		Binds: []string{"stock_thscode"},
		Params: []Param{
			{
				Name:     "stock_thscode",
				Type:     "string",
				Desc:     "股票同花顺代码，如 600519.SH (贵州茅台) 或 300750.SZ (宁德时代)",
				Required: true,
				Trim:     true,
				EmptyMsg: "stock_thscode 不能为空",
			},
			{
				Name:    "limit",
				Type:    "integer",
				Desc:    "返回数量限制（默认 {default}，最大 {max}）",
				Default: 20,
				Max:     intMax(100),
				Over:    OverRangeReset,
			},
		},
		Envelope: Envelope{Bare: true},
	}
)

// FundHoldersStructure 是基金/ETF 的持有人结构与前十大持有人。
//
// 两条查询合成一个结构：一条取汇总（机构/散户比例、户数），一条取前十大明细。
// 输出不是行，是一个拼起来的结构——于是它走「取数 → Go 侧计算 → 输出」那条路。
//
// 前十大那条的条数是**写进 SQL** 的（迁移前就是 fmt.Sprintf 拼进去的），
// 所以它不是绑定值：LIMIT {limit} 由声明在运行时渲染。
var FundHoldersStructure = Spec{
	Name: "hithink.finance.fund.etf.holders_structure",
	Description: `查询基金或 ETF 的持有人结构与前十大持有人。
包含机构投资者比例 (ins_position)、个人散户比例 (psnl_rate)、持有人户数 (holder_amount) 以及前十大持有人（如中央汇金、险资、券商集合理财、QFII）穿透清单。`,
	DB: "fund",
	Queries: []Query{
		{
			Name: "structure",
			DB:   "fund",
			SQL: "SELECT fund_thscode, merge_scope, report_date_ms, ins_position, holder_amount, " +
				"avg_holder_share, psnl_rate, mgmt_staff_hold_rate FROM v_fund_holders " +
				"WHERE fund_thscode = ? ORDER BY report_date_ms DESC LIMIT 1",
			Binds: []string{"fund_thscode"},
		},
		{
			Name: "top_holders",
			DB:   "fund",
			SQL: "SELECT fund_thscode, holder_name, holder_type, holder_code, rank, hold_share, " +
				"hold_rate_pct, report_date_ms, publish_date_ms FROM v_fund_top_holders " +
				"WHERE fund_thscode = ? ORDER BY rank ASC LIMIT {limit}",
			Binds: []string{"fund_thscode"},
		},
	},
	Composed: true,
	Params: []Param{
		{
			Name:     "fund_thscode",
			Type:     "string",
			Desc:     "基金代码，如 158000.SZ 或 510300.SH",
			Required: true,
			Trim:     true,
			EmptyMsg: "fund_thscode 不能为空",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "前十大持有人返回条数上限（默认 10）",
			Default: 10,
			Max:     intMax(20),
			Over:    OverRangeReset,
		},
	},
}
