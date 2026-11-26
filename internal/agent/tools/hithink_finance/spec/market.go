package spec

// MarketPriceHistorical 是前复权历史日线。
var MarketPriceHistorical = Spec{
	Name: "hithink.finance.market.price.historical",
	Description: `获取股票的历史 K 线数据（前复权）。返回每日的 date, open, high, low, close, volume, turnover。

使用示例：thscode="600519.SH", days={days}`,
	DB:    "market",
	SQL:   `SELECT date, open, high, low, close, volume, turnover FROM v_daily_qfq WHERE thscode = ? ORDER BY date DESC LIMIT ?`,
	Binds: []string{"thscode", "days"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "查询天数（默认 {default}，最大 {max}）",
			Default: 30,
			Max:     intMax(500),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "days",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "股票不存在或无数据：{entity}",
	},
}

// MarketPriceSnapshot 是单只股票的最新行情快照。
//
// 输出是那一行本身（加上回显的 thscode），不是数组；数据超过三天没更新时，
// 结果里多一条 warning——那件事由 Freshness 声明，不是代码。
var MarketPriceSnapshot = Spec{
	Name: "hithink.finance.market.price.snapshot",
	Description: `获取单只股票的最新行情快照（前复权）。

返回字段：
- date: 交易日期
- open: 开盘价
- high: 最高价
- low: 最低价
- close: 收盘价
- volume: 成交量（股）
- turnover: 成交额（元）

使用示例：
- 获取茅台最新行情：thscode="600519.SH"
- 获取宁德时代最新行情：thscode="300750.SZ"`,
	DB: "market",
	SQL: `
		SELECT date, open, high, low, close, volume, turnover
		FROM v_daily_qfq
		WHERE thscode = ?
		ORDER BY date DESC
		LIMIT 1
	`,
	Binds: []string{"thscode"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "同花顺股票代码，如 600519.SH（茅台）、300750.SZ（宁德时代）",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
	},
	Envelope: Envelope{
		Single: true,
		Echo:   []string{"thscode"},
		Freshness: &Freshness{
			Field: "date",
			Days:  3,
			Msg:   "数据已过期 {days} 天，最后更新日期：{date}。请检查数据同步状态",
		},
		Empty:    EmptyError,
		EmptyMsg: "股票不存在：{entity}。请检查股票代码是否正确，格式应为 '600519.SH' 或 '300750.SZ'",
	},
}

// MarketDividendCalendar 是分红派息与除权除息日历。
//
// 两条查询形态：给了 thscode 就查那一只股的历史分红，没给就查全区间的日历。
// 起始日期的缺省是「当年年初」——它是**运行时**才算的（RuntimeDefault），
// 因为写死成 2026 就会在跨年之后悄悄筛起去年；schema 里不写缺省，与迁移前一致。
//
// 输出是紧凑 JSON：迁移前这个工具吐的就是紧凑的，缩进不缩进是模型读到的
// 契约的一部分，不是排版偏好。
var MarketDividendCalendar = Spec{
	Name: "hithink.finance.market.dividend.calendar",
	Description: `查询股票分红派息与除权除息日历（送转股、派发现金）。
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
- 查询近期全市场分红日历：start_date="2026-06-01", limit=30`,
	DB: "market",
	SQL: `
			SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio
			FROM raw_adjustment_events e
			LEFT JOIN v_symbol s ON e.thscode = s.thscode
			WHERE e.ex_date >= ? AND e.ex_date <= ? AND e.dividend_per_share >= ? AND e.per_share_bonus >= ?
			ORDER BY e.ex_date DESC
			LIMIT ?
		`,
	Binds: []string{"start_date", "end_date", "min_dividend", "min_bonus", "limit"},
	Variants: []Variant{
		{
			Param: "thscode",
			SQL: `
			SELECT e.thscode, s.name, e.ex_date, e.dividend_per_share, e.per_share_bonus, e.allotment_ratio
			FROM raw_adjustment_events e
			LEFT JOIN v_symbol s ON e.thscode = s.thscode
			WHERE e.thscode = ?
			ORDER BY e.ex_date DESC
			LIMIT ?
		`,
			Binds: []string{"thscode", "limit"},
		},
	},
	Params: []Param{
		{
			Name: "thscode",
			Type: "string",
			Desc: "可选。股票代码，如 600519.SH、000001.SZ。指定时查询该股分红历史",
		},
		{
			Name:           "start_date",
			Type:           "string",
			Desc:           "可选。起始除息日（YYYY-MM-DD），默认当年年初",
			RuntimeDefault: "{year}-01-01",
		},
		{
			Name:           "end_date",
			Type:           "string",
			Desc:           "可选。截止除息日（YYYY-MM-DD）",
			RuntimeDefault: "2099-12-31",
		},
		{
			Name:    "min_dividend",
			Type:    "number",
			Desc:    "可选。每股最低派现金额（元）",
			Default: 0,
		},
		{
			Name:    "min_bonus",
			Type:    "number",
			Desc:    "可选。每股最低送转股比例（如 0.3 表示每10股送转3股以上）",
			Default: 0,
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "可选。返回数量限制（默认 50，最大 200）",
			Default: 50,
			Max:     intMax(200),
		},
	},
	Envelope: Envelope{
		EchoIfSet: []string{"thscode"},
		CountKey:  "count",
		Data:      true,
		DataKey:   "items",
		Empty:     EmptyError,
		EmptyMsg:  "未查询到符合条件的分红除权记录",
		Compact:   true,
	},
}

// MarketDividendYieldScreener 是高股息率筛选。
//
// 查询是声明里的；筛股息率是 Go 侧的活（结果行里才有 div_yield_pct），
// 于是这一族走「取数 → Go 侧计算 → 输出」那条路。
//
// 起始日期是**算出来的**：模型只给分红年度，日期是"{year}-01-01"。
// 缺省年份写死成 2026 是迁移前的行为——跨年之后它会静默地筛 2026 年的高股息，
// 那是产品决定，不是这里能顺手改掉的。
var MarketDividendYieldScreener = Spec{
	Name: "hithink.finance.market.dividend.yield_screener",
	Description: `筛选高股息率红利标的（基于最新现金分红与最新收盘价计算真实股息率）。
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
- 筛选高息股 Top 30：min_yield_pct=4.0, limit=30`,
	DB: "market",
	Queries: []Query{
		{
			Name: "items",
			DB:   "market",
			SQL: `
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
	`,
			Binds: []string{"start_date", "limit"},
		},
	},
	Derive: []Derive{
		{Name: "start_date", Template: "{year}-01-01"},
	},
	Composed: true,
	Params: []Param{
		{
			Name:    "year",
			Type:    "string",
			Desc:    "分红年度（YYYY），默认 2026",
			Default: "2026",
		},
		{
			Name:           "min_yield_pct",
			Type:           "number",
			Desc:           "最低预估股息率（%，默认 3.0）",
			Default:        3.0,
			ZeroMeansUnset: true,
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 50）",
			Default: 50,
			Max:     intMax(200),
		},
	},
}
