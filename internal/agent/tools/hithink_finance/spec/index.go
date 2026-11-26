package spec

// IndexSectorDaily 是板块指数日线。
//
// 两处特别：绑定的代码转成大写（板块代码统一大写），空结果不报错而是返回带
// 提示的空载荷——板块代码写错时，提示比一个干巴巴的 404 有用得多。
var IndexSectorDaily = Spec{
	Name: "hithink.finance.index.sector.daily",
	Description: `查板块指数的日线行情，用于板块轮动与强弱对比：拿到同一天多个板块的 close，
自己算区间涨幅即可比较哪个板块在领涨/掉队。

thscode 传板块代码（.TI 结尾，如 881101.TI）。不知道板块代码时先用
hithink.finance.query.sql 查：SELECT thscode, name, tag FROM v_index_universe WHERE name LIKE '%关键词%'

数据区间 2021-09-13 至今，约 977,170 条。
使用示例：thscode="881101.TI", days={days}`,
	DB: "index",
	SQL: `
		SELECT trade_date, open, high, low, close, volume, turnover
		FROM v_index_daily
		WHERE thscode = ?
		ORDER BY trade_date DESC
		LIMIT ?
	`,
	Binds: []string{"thscode", "days"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "板块指数代码，.TI 结尾，如 881101.TI（种植业与林业）",
			Required: true,
			Trim:     true,
			Upper:    true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "返回最近多少个交易日（默认 {default}，最大 {max}）",
			Default: 30,
			Max:     intMax(800),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"thscode"},
		CountKey: "count",
		Data:     true,
		Empty:    EmptyPayload,
		EmptyHint: "没有该板块的日线数据。板块代码是 .TI 结尾（不是 .SH/.SZ），" +
			"可用 hithink.finance.query.sql 查 v_index_universe 确认代码。",
	},
}

// IndexSectorRanking 是申万行业细分与概念板块的排行。
//
// tag 只认 "cn_concept"，其余一律当 "industry"——这条回落规则在声明里，
// 不用每个工具各写一遍。
var IndexSectorRanking = Spec{
	Name: "hithink.finance.index.sector.ranking",
	Description: `获取申万行业细分或热门概念板块（如 AI、半导体、低空经济等）的最新排行。

用于发现市场领涨领跌主线及资金成交焦点。`,
	DB:    "index",
	SQL:   `SELECT snapshot_date, thscode, name, tag, last_price, price_change, price_change_ratio, volume, turnover FROM v_index_latest WHERE tag = ? ORDER BY {order} LIMIT {limit}`,
	Binds: []string{"tag"},
	Params: []Param{
		{
			Name:     "tag",
			Type:     "string",
			Desc:     "板块类型：'industry' (行业细分，默认) 或 'cn_concept' (前沿概念)",
			Default:  "industry",
			Explicit: []string{"cn_concept"},
		},
		{
			Name:    "order_by",
			Type:    "string",
			Desc:    "排序方式：'gainers' (领涨榜，默认) 或 'losers' (领跌榜) 或 'turnover' (成交额榜)",
			Default: "gainers",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}，最大 {max}）",
			Default: 20,
			Max:     intMax(60),
			Over:    OverRangeReset,
		},
	},
	OrderBy: &OrderBy{
		Param:   "order_by",
		Default: "price_change_ratio DESC",
		By: map[string]string{
			"losers":   "price_change_ratio ASC",
			"turnover": "turnover DESC",
		},
	},
	Envelope: Envelope{Bare: true},
}

// IndexOverview 是核心宽基指数的最新点位。
//
// 它没有参数，SQL 是静态的——所以它是这一族里最简单的一个声明。
//
// **数据源是 v_index_daily，不是 v_index_latest。** 后者是**板块快照**视图
// （industry / region / tszs / cn_concept），里面根本没有宽基指数——查它永远
// 返回空数组。真库里宽基指数的日线在 v_index_daily（每个指数 1200+ 个交易日）。
//
// 涨跌与涨跌幅自己算：v_index_daily 只有 close，没有前收。LAG 按 trade_date
// 升序取**前一个交易日**的收盘；QUALIFY 取每个指数的最新一根。
//
// 用窗口函数而不是自连接，有三个理由：v_index_daily 与别的工具共用
// （index.sector.daily 查的也是它），先过滤就再也取不到前一行；外层 FROM
// 保持在 v_index_daily 上，schema 契约测试才校验得到这张表的列；以及冷启动
// ——相关子查询要扫全表（实测 0.97s），窗口函数只要一次排序（实测 13ms）。
//
// 名字用 CASE 映射——宽基指数不在 v_index_universe 里（那里只有板块），
// 而要查的这 7 个是固定名单，映射也就固定。
var IndexOverview = Spec{
	Name: "hithink.finance.index.overview",
	Description: `获取核心宽基指数（上证指数、深证成指、创业板指、科创50、沪深300、中证500、中证1000等）的最新点位、涨跌幅、成交额。

用于研判全市场大盘走势与宏观赚钱效应。`,
	DB: "index",
	SQL: `SELECT trade_date, thscode,
       CASE thscode
           WHEN '000001.SH' THEN '上证指数'
           WHEN '399001.SZ' THEN '深证成指'
           WHEN '399006.SZ' THEN '创业板指'
           WHEN '000688.SH' THEN '科创50'
           WHEN '000300.SH' THEN '沪深300'
           WHEN '000905.SH' THEN '中证500'
           WHEN '000852.SH' THEN '中证1000'
       END AS name,
       'index' AS tag,
       close AS last_price,
       ROUND(close - LAG(close) OVER (PARTITION BY thscode ORDER BY trade_date), 2) AS price_change,
       ROUND((close - LAG(close) OVER (PARTITION BY thscode ORDER BY trade_date))
             / NULLIF(LAG(close) OVER (PARTITION BY thscode ORDER BY trade_date), 0) * 100, 2) AS price_change_ratio,
       open, high, low, volume, turnover
FROM v_index_daily
WHERE thscode IN ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH', '000300.SH', '000905.SH', '000852.SH')
QUALIFY ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY trade_date DESC) = 1
ORDER BY thscode`,
	Envelope: Envelope{Bare: true},
}

// IndexSectorConstituents 是某个板块的成分股。
//
// sector 可以是板块代码（881101.TI）也可以是板块中文名（白酒）——**同一个参数、
// 两种形态**，走的是不同的列（thscode / name）。这靠 Variant.Match 的
// "suffix:.TI" 判定：值以 .TI 结尾就选代码那条变体。
//
// tag 是可选的消歧条件（板块中文名会重名），它在 WHERE 里占一个位置，所以
// 过滤条件的绑定值也落在那个位置——不是一律排在最前。
var IndexSectorConstituents = Spec{
	Name: "hithink.finance.index.sector.constituents",
	Description: `查某个板块的成分股列表。配合 index.sector.membership 使用即可做「同行对比」：
先查某只票在哪些板块，再用本工具拉同板块的其它票。

sector 可以传板块名（如 "白酒"）或板块代码（如 "881101.TI"）。
板块名是中文且可能重名，强烈建议带上 tag 消歧（如 tag="industry"）。

板块类型 tag 取值：cn_concept(概念板块) / industry(行业板块) / tszs(特色指数，含全A与涨跌停统计) / region(地域板块)

注意：v_index_constituents 只有**当前**成分快照，没有历史成分，做历史回测会有幸存者偏差。
使用示例：sector="白酒", tag="industry", limit=50`,
	DB: "index",
	SQL: `
		SELECT c.thscode, c.ticker, c.name, u.name AS board_name, u.tag
		FROM v_index_constituents c
		JOIN v_index_universe u ON u.thscode = c.index_thscode
		WHERE u.name = ?{where}
		ORDER BY c.thscode
		LIMIT ?
	`,
	Binds: []string{"sector", "limit"},
	Variants: []Variant{
		{
			Param: "sector",
			Match: "suffix:.TI",
			SQL: `
		SELECT c.thscode, c.ticker, c.name, u.name AS board_name, u.tag
		FROM v_index_constituents c
		JOIN v_index_universe u ON u.thscode = c.index_thscode
		WHERE u.thscode = ?{where}
		ORDER BY c.thscode
		LIMIT ?
	`,
			Binds: []string{"sector", "limit"},
		},
	},
	Filters: []Filter{
		{
			Param:  "tag",
			Clause: "u.tag = {v}",
		},
	},
	FilterJoin:  "AND",
	WherePrefix: " AND ",
	Params: []Param{
		{
			Name:     "sector",
			Type:     "string",
			Desc:     "板块名（中文，如 白酒）或板块代码（如 881101.TI）",
			Required: true,
			Trim:     true,
			Upper:    true,
			EmptyMsg: "参数错误：sector 不能为空",
		},
		{
			Name: "tag",
			Type: "string",
			Desc: "板块类型，可选：cn_concept / industry / tszs / region，用于同名消歧",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回条数上限（默认 {default}，最大 {max}）",
			Default: 50,
			Max:     intMax(500),
		},
	},
	Envelope: Envelope{
		Echo:     []string{"sector", "tag"},
		CountKey: "count",
		Data:     true,
		Empty:    EmptyPayload,
		EmptyHint: "没有匹配的板块成分。可先用 hithink.finance.query.sql 执行 " +
			"SELECT thscode, name, tag FROM v_index_universe WHERE name LIKE '%关键词%' 确认板块名与 tag 的准确写法，" +
			"再带 tag 参数重试。",
	},
}

// IndexSectorMembership 是一只股票属于哪些板块。
//
// 这个工具的结果**不是行**——输出里还有一个 tags 字段（结果里出现过的板块类型，
// 去重后按出现顺序逗号连接），而 tags 参数本身是逗号分隔的多值、过滤在 Go 侧做
// （拼进 SQL 反而多一个注入面，候选集最多几百行，内存过滤完全够用）。
//
// 所以它是"Go 侧计算"那一族：取数在 Fetch 钩子里，过滤与组装在 Compose 钩子里。
var IndexSectorMembership = Spec{
	Name: "hithink.finance.index.sector.membership",
	Description: `查一只股票所属的行业 / 概念 / 地域板块。这是做「同行对比」和「板块轮动」分析的前提：
先查这只票在哪些板块里，再用 index.sector.constituents 拉同板块的其它股票做对比。

板块类型 tag 取值：cn_concept(概念板块) / industry(行业板块) / tszs(特色指数，含全A与涨跌停统计) / region(地域板块)

返回字段：board_name(板块名), tag(板块类型), board_thscode(板块代码, .TI 结尾)
使用示例：thscode="600519.SH"`,
	DB: "index",
	SQL: `
		SELECT u.name AS board_name, u.tag, u.thscode AS board_thscode
		FROM v_index_constituents c
		JOIN v_index_universe u ON u.thscode = c.index_thscode
		WHERE c.thscode = ?
		ORDER BY u.tag, u.name
	`,
	Binds:    []string{"thscode"},
	Composed: true,
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "个股代码，如 600519.SH",
			Required: true,
			EmptyMsg: "参数错误：thscode 不能为空",
		},
		{
			Name:      "tags",
			Type:      "string",
			Desc:      "可选，只返回指定类型的板块，逗号分隔。留空返回全部。",
			Multi:     true,
			Separator: ",",
		},
	},
}
