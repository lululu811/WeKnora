package spec

// special 家族的声明。
//
// 「某个交易日的榜单」这一族有共同的一处形状：日期参数为「latest」或空时查最新
// 交易日，给了具体日期就查那一天——两条 WHERE 不同，绑定也不同，所以用 Variants
// 声明两条形态，而不是用 Filters（Filters 只能表达"可选地加一个条件"）。

// SpecialHotStockRankTrend 是个股的人气排名趋势。
//
// 它的窗口参数叫 limit 而不是 days，而且没有上限——声明里 Max 为 nil，
// 所以 schema 里不会出现 maximum，这与迁移前一致。
var SpecialHotStockRankTrend = Spec{
	Name:        "hithink.finance.special.hot_stock.rank_trend",
	Description: `获取指定个股的历史人气排名趋势。返回 thscode, trend_date, date_ms, rank。用于研判资金关注度是持续升温还是退潮。`,
	DB:          "special",
	SQL:         `SELECT thscode, trend_date, date_ms, rank FROM v_hot_stock_rank_trend WHERE thscode = ? ORDER BY trend_date DESC LIMIT ?`,
	Binds:       []string{"thscode", "limit"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "股票代码，如 600519.SH、600418.SH",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回历史天数（默认 {default}）",
			Default: 30,
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "count",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到 {entity} 的人气排名趋势数据",
	},
}

// SpecialDragonTigerList 是龙虎榜。
var SpecialDragonTigerList = Spec{
	Name: "hithink.finance.special.dragon_tiger.list",
	Description: `获取龙虎榜数据。返回 thscode, name, trade_date, board_type, net_value(净买额), net_rate(净买占比), buy_value, sell_value, org_net_value(机构净买), change, hot_rank, range_days 等。

注意：净买额字段是 net_value，不是 net_buy；上榜原因(reason)不在本地数据里。

使用示例：trade_date="latest"`,
	DB:    "special",
	SQL:   `SELECT trade_date, board_type, thscode, name, net_value, net_rate, buy_value, sell_value, org_net_value, change, hot_rank, range_days FROM v_dragon_tiger WHERE trade_date = (SELECT MAX(trade_date) FROM v_dragon_tiger) ORDER BY net_value DESC LIMIT ?`,
	Binds: []string{"limit"},
	Variants: []Variant{
		{
			Param: "trade_date",
			SQL:   `SELECT trade_date, board_type, thscode, name, net_value, net_rate, buy_value, sell_value, org_net_value, change, hot_rank, range_days FROM v_dragon_tiger WHERE trade_date = ? ORDER BY net_value DESC LIMIT ?`,
			Binds: []string{"trade_date", "limit"},
		},
	},
	Params: []Param{
		{
			Name:    "trade_date",
			Type:    "string",
			Desc:    "交易日期（YYYY-MM-DD）或 'latest' 表示最新",
			Default: "latest",
			Blank:   []string{"latest"},
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 50,
		},
	},
	Envelope: Envelope{
		RowEcho:  "trade_date",
		CountKey: "count",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到龙虎榜数据。请检查日期是否正确或数据是否已同步",
	},
}

// SpecialHotStockSkyrocket 是飙升榜。
var SpecialHotStockSkyrocket = Spec{
	Name: "hithink.finance.special.hot_stock.skyrocket",
	Description: `获取飙升榜（热股榜）数据。返回 thscode, name, capture_date, rank, heat(热度), rank_change, rank_trend 等。

注意：日期列是 capture_date 不是 trade_date，热度列是 heat 不是 hot_value，本地
数据不含价格字段。

使用示例：trade_date="latest"`,
	DB:    "special",
	SQL:   `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_skyrocket WHERE capture_date = (SELECT MAX(capture_date) FROM v_skyrocket) ORDER BY rank ASC LIMIT ?`,
	Binds: []string{"limit"},
	Variants: []Variant{
		{
			Param: "trade_date",
			SQL:   `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_skyrocket WHERE capture_date = ? ORDER BY rank ASC LIMIT ?`,
			Binds: []string{"trade_date", "limit"},
		},
	},
	Params: []Param{
		{
			Name:    "trade_date",
			Type:    "string",
			Desc:    "交易日期（YYYY-MM-DD）或 'latest' 表示最新",
			Default: "latest",
			Blank:   []string{"latest"},
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 50,
		},
	},
	Envelope: Envelope{
		RowEcho:      "trade_date",
		RowEchoField: "capture_date",
		CountKey:     "count",
		Data:         true,
		Empty:        EmptyError,
		EmptyMsg:     "未找到飙升榜数据。请检查数据是否已同步",
	},
}

// SpecialHotStockTopList 是全市场人气总榜。
var SpecialHotStockTopList = Spec{
	Name:        "hithink.finance.special.hot_stock.top_list",
	Description: `获取全市场人气总榜（按综合关注度与热度排序）。返回 thscode, name, capture_date, rank, heat(热度值), rank_change, rank_trend 等。`,
	DB:          "special",
	SQL:         `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_hot_stock WHERE capture_date = (SELECT MAX(capture_date) FROM v_hot_stock) ORDER BY rank ASC LIMIT ?`,
	Binds:       []string{"limit"},
	Variants: []Variant{
		{
			Param: "trade_date",
			SQL:   `SELECT capture_date, period, rank, thscode, ticker, name, heat, rank_change, rank_trend FROM v_hot_stock WHERE capture_date = ? ORDER BY rank ASC LIMIT ?`,
			Binds: []string{"trade_date", "limit"},
		},
	},
	Params: []Param{
		{
			Name:    "trade_date",
			Type:    "string",
			Desc:    "交易日期（YYYY-MM-DD）或 'latest' 表示最新",
			Default: "latest",
			Blank:   []string{"latest"},
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 50,
		},
	},
	Envelope: Envelope{
		RowEcho:      "trade_date",
		RowEchoField: "capture_date",
		CountKey:     "count",
		Data:         true,
		Empty:        EmptyError,
		EmptyMsg:     "未找到人气榜数据",
	},
}

// SpecialLimitUpPool 是涨停池。
//
// 它是这一族里唯一校验日期格式的：trade_date 是 DATE 列，绑定一个非法字面量会让
// DuckDB 在解析阶段就报 Binder Error，提前拦下来能给调用方一句能看懂的话。
// 真正的注入防线是 ? 绑定，格式校验只是让错误可读。
var SpecialLimitUpPool = Spec{
	Name: "hithink.finance.special.limit.limit_up_pool",
	Description: `获取指定日期的涨停池数据。返回 thscode, name, trade_date, limit_up_time, stat 等。

使用示例：trade_date="latest"`,
	DB:  "special",
	SQL: `SELECT thscode, name, trade_date, limit_up_time, continue_day_cnt, seal_money, last_price FROM v_limit_up_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool) ORDER BY seal_money DESC LIMIT {limit}`,
	Variants: []Variant{
		{
			Param: "trade_date",
			SQL:   `SELECT thscode, name, trade_date, limit_up_time, continue_day_cnt, seal_money, last_price FROM v_limit_up_pool WHERE trade_date = ? ORDER BY seal_money DESC LIMIT {limit}`,
			Binds: []string{"trade_date"},
		},
	},
	Params: []Param{
		{
			Name:       "trade_date",
			Type:       "string",
			Desc:       "交易日期（YYYY-MM-DD）或 'latest' 表示最新",
			Default:    "latest",
			Blank:      []string{"latest"},
			Pattern:    `^\d{4}-\d{2}-\d{2}$`,
			PatternMsg: "trade_date 格式非法：%q，应为 YYYY-MM-DD",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 100,
		},
	},
	Envelope: Envelope{
		RowEcho:  "trade_date",
		CountKey: "count",
		Data:     true,
		Empty:    EmptyError,
		EmptyMsg: "未找到涨停数据。请检查日期是否正确或数据是否已同步",
	},
}

// SpecialAnomalyStockReason 是个股异动归因。
//
// 给了 thscode 就查那只股票，不给就查全市场最新一期——两条 SQL 形态不同，
// 迁移前是 if/else 各写一条，现在由 Variants 声明。
var SpecialAnomalyStockReason = Spec{
	Name: "hithink.finance.special.anomaly.stock_reason",
	Description: `获取指定股票近期（或全市场最新）大涨、大跌、盘中异动归因分析、驱动逻辑与关键词列表。

返回 capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list。`,
	DB:    "special",
	SQL:   `SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list FROM v_anomaly_list WHERE capture_date = (SELECT MAX(capture_date) FROM v_anomaly_list) ORDER BY capture_date DESC LIMIT ?`,
	Binds: []string{"limit"},
	Variants: []Variant{
		{
			Param: "thscode",
			SQL:   `SELECT capture_date, thscode, stock_name, tag_name, analysis_content, keyword_list FROM v_anomaly_list WHERE thscode = ? ORDER BY capture_date DESC LIMIT ?`,
			Binds: []string{"thscode", "limit"},
		},
	},
	Params: []Param{
		{
			Name: "thscode",
			Type: "string",
			Desc: "同花顺股票代码（如 600519.SH 或 000002.SZ）。若为空则返回全市场最新异动列表",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回记录条数（默认 {default}，最大 {max}）",
			Default: 10,
			Max:     intMax(50),
			Over:    OverRangeReset,
		},
	},
	Envelope: Envelope{Bare: true},
}

// SpecialAuctionSnapshot 是 9:25 盘前集合竞价异动。
var SpecialAuctionSnapshot = Spec{
	Name: "hithink.finance.special.auction.snapshot",
	Description: `获取 9:25 盘前集合竞价异动股票（抢筹爆量、大幅超预期高开/低开）。

返回 auction_price, auction_pct, auction_amount, auction_volume_ratio, auction_turnover_pct 等。`,
	DB:  "special",
	SQL: `SELECT snapshot_date, thscode, ticker, name, auction_price, auction_pct, auction_volume, auction_amount, auction_unmatched, auction_turnover_pct, auction_yesterday_ratio_pct, auction_volume_ratio, pre_close_price, open_price, last_price, float_market_cap, stage FROM v_auction_snapshot WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM v_auction_snapshot) ORDER BY {order} LIMIT {limit}`,
	Params: []Param{
		{
			Name:    "order_by",
			Type:    "string",
			Desc:    "排序方式：'amount' (竞价成交额，默认) 或 'gainers' (竞价涨幅最高) 或 'losers' (竞价跌幅最深)",
			Default: "amount",
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
		Default: "auction_amount DESC",
		By: map[string]string{
			"gainers": "auction_pct DESC",
			"losers":  "auction_pct ASC",
		},
	},
	Envelope: Envelope{Bare: true},
}

// SpecialLimitBreakPool 是最新交易日的炸板池。
var SpecialLimitBreakPool = Spec{
	Name: "hithink.finance.special.limit.break_pool",
	Description: `获取最新交易日炸板池（盘中曾触及涨停但被砸开、开板次数、收盘涨跌幅及亏钱效应）。

返回 trade_date, thscode, name, last_price, open_times, price_change_ratio。`,
	DB:    "special",
	SQL:   `SELECT trade_date, thscode, name, last_price, open_times, price_change_ratio FROM v_limit_break_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_break_pool) ORDER BY open_times DESC LIMIT ?`,
	Binds: []string{"limit"},
	Params: []Param{
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}，最大 {max}）",
			Default: 30,
			Max:     intMax(100),
			Over:    OverRangeReset,
		},
	},
	Envelope: Envelope{Bare: true},
}

// SpecialLimitLadderDetail 是最新交易日的连板梯队明细。
//
// min_board 是连板天数的门槛，直接进 WHERE；它没有上限，只有缺省值。
var SpecialLimitLadderDetail = Spec{
	Name: "hithink.finance.special.limit.ladder_detail",
	Description: `获取最新交易日连板梯队个股清单（市场最高空间板、各连板档位个股明细、封单金额、首次封板时间）。

返回 trade_date, continue_day_cnt, thscode, name, last_price, limit_up_time, seal_money。`,
	DB:    "special",
	SQL:   `SELECT trade_date, continue_day_cnt, thscode, name, last_price, limit_up_time, seal_money FROM v_limit_up_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool) AND continue_day_cnt >= ? ORDER BY continue_day_cnt DESC, seal_money DESC LIMIT ?`,
	Binds: []string{"min_board", "limit"},
	Params: []Param{
		{
			Name:    "min_board",
			Type:    "integer",
			Desc:    "最低连板天数门槛（默认 {default}，即 2 板及以上；设为 1 则包含首板）",
			Default: 2,
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回最多股票数（默认 {default}，最大 {max}）",
			Default: 50,
			Max:     intMax(100),
			Over:    OverRangeReset,
		},
	},
	Envelope: Envelope{Bare: true},
}

// SpecialSentimentStats 是市场短线情绪统计。
//
// 这个工具是"多查询"那一族的代表：它要同时看涨停池、跌停池、炸板池三张表，
// 才能算出炸板率与最高连板。三条查询在声明里各占一条，compose 按名字取结果。
//
// ⚠️ 迁移前那两条查询的错误是**静默吞掉**的（`downRows, _ := ...`）：池子查不
// 到就当是 0 家，不报错。Query.Optional 保留了这个行为——第一条（涨停池）失败
// 才致命。
var SpecialSentimentStats = Spec{
	Name: "hithink.finance.special.sentiment.stats",
	Description: `获取市场短线情绪综合统计（涨停家数、跌停家数、炸板家数、炸板率、最高连板高度等）。

用于研判短线情绪周期（启动/发酵/高潮/退潮）。`,
	Composed: true,
	Queries: []Query{
		{
			Name: "limit_up",
			DB:   "special",
			SQL:  `SELECT trade_date, thscode, name, continue_day_cnt, seal_money FROM v_limit_up_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_up_pool)`,
		},
		{
			Name:     "limit_down",
			DB:       "special",
			SQL:      `SELECT trade_date, thscode, name, price_change_ratio FROM v_limit_down_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_down_pool)`,
			Optional: true,
		},
		{
			Name:     "limit_break",
			DB:       "special",
			SQL:      `SELECT trade_date, thscode, name, open_times, price_change_ratio FROM v_limit_break_pool WHERE trade_date = (SELECT MAX(trade_date) FROM v_limit_break_pool)`,
			Optional: true,
		},
	},
}
