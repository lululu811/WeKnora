package spec

// FuturesPriceHistorical 是期货合约历史日线。
//
// 它的输出是一份裸数组，没有信封——所以 Envelope.Bare 为真。
var FuturesPriceHistorical = Spec{
	Name: "hithink.finance.futures.price.historical",
	Description: `获取指定期货合约（如 IFZL.CFE、CU2705.SHF）的历史日线 K 线行情。

返回 open_price, high_price, low_price, close_price, volume, turnover 等。`,
	DB:  "futures",
	SQL: `SELECT thscode, trade_date, open_price, high_price, low_price, close_price, volume, turnover FROM v_futures_daily WHERE thscode = ? ORDER BY trade_date DESC LIMIT {days}`,

	Binds: []string{"thscode"},
	Params: []Param{
		{
			Name:     "thscode",
			Type:     "string",
			Desc:     "期货合约代码，如 IFZL.CFE(沪深300主力连续)、CU2705.SHF、RB2611.SHF",
			Required: true,
			Trim:     true,
			EmptyMsg: "thscode 不能为空",
		},
		{
			Name:    "days",
			Type:    "integer",
			Desc:    "查询天数（默认 {default}，最大 {max}）",
			Default: 30,
			Max:     intMax(200),
		},
	},
	Envelope: Envelope{
		Bare: true,
	},
}

// FuturesPriceSnapshot 是期货品种或合约的最新行情快照。
//
// 品种代码与合约代码都是可选的过滤条件：给了哪个就按哪个过滤，两个都给就是
// 二选一（迁移前是运行时拼 WHERE，现在由 Filters 声明）。
var FuturesPriceSnapshot = Spec{
	Name: "hithink.finance.futures.price.snapshot",
	Description: `获取指定期货品种（如 CU、RB、SC、IF、LH、AU）或合约的最新行情快照。

返回 close_price, volume, turnover, trade_date 等。`,
	DB:  "futures",
	SQL: `SELECT thscode, variety_code, exchange_code, name, trade_date, close_price, volume, turnover FROM v_futures_latest{where} ORDER BY turnover DESC LIMIT {limit}`,
	Params: []Param{
		{
			Name:  "variety_code",
			Type:  "string",
			Desc:  "期货品种代码，如 CU(沪铜)、RB(螺纹钢)、SC(原油)、IF(沪深300股指)、AU(黄金)",
			Trim:  true,
			Upper: true,
		},
		{
			Name: "thscode",
			Type: "string",
			Desc: "具体合约代码，如 IFZL.CFE(沪深300主力连续)、CU2705.SHF",
			Trim: true,
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 20,
			Max:     intMax(100),
			Over:    OverRangeReset,
		},
	},
	Filters: []Filter{
		{Param: "variety_code", Clause: "variety_code = {v}"},
		{Param: "thscode", Clause: "thscode = {v}"},
	},
	FilterJoin: "OR",
	Envelope:   Envelope{Bare: true},
}

// FuturesVarietyList 是期货品种清单，交易所代码可选。
var FuturesVarietyList = Spec{
	Name: "hithink.finance.futures.variety.list",
	Description: `获取期货品种清单（含品种代码、名称、交易所、保证金比例、主力合约代码等）。

支持大宗商品（铜、铝、原油、黄金、螺纹钢、生猪等）与金融期货（IF、IC、IM、IH、国债）。`,
	DB:  "futures",
	SQL: `SELECT variety_code, exchange_code, name, has_night_session, margin_rate, main_contract_thscode, trade_unit FROM v_futures_varieties{where} ORDER BY variety_code LIMIT {limit}`,
	Params: []Param{
		{
			Name: "exchange_code",
			Type: "string",
			Desc: "交易所代码可选过滤：SHFE(上期所)、DCE(大商所)、CZCE(郑商所)、CFFEX(中金所)、INE(能源中心)",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回数量限制（默认 {default}）",
			Default: 100,
			Max:     intMax(200),
			Over:    OverRangeReset,
		},
	},
	Filters: []Filter{
		{Param: "exchange_code", Clause: "exchange_code = {v}"},
	},
	Envelope: Envelope{Bare: true},
}

// 期货仓单与基差这两个工具。
//
// 形状是「一个（可选的）代码 + 一个条数 + 一张表」，与第二批同形，但有两处不同：
//
//  1. 给了合约代码就查那个合约的历史，没给就查**最新交易日全市场**——两条 SQL
//     不同、绑定值也不同（前者绑两个，后者只绑条数）。这正是 Variants 机制：
//     参数非空时选变体，否则用默认那条。
//  2. 输出的行不叫 data 叫 items——那是模型读到的契约的一部分。
//
// 还有一个细节：给了合约代码时，输出里会多一个 thscode 字段（回显你用过的
// 过滤条件），没给时没有。信封的 Echo 规则因此是「只回显非空的」。

// FuturesInventoryWarehouseReceipts 是期货交易所注册仓单与日变动。
var FuturesInventoryWarehouseReceipts = Spec{
	Name: "hithink.finance.futures.inventory.warehouse_receipts",
	Description: `获取期货交易所注册仓单数据与日变动（amount 仓单量、amount_change 日增减量）。
仓单大幅注销流出代表现货紧缺/去库强烈，是商品与对应 A 股周期股行情的先行指标；仓单大幅累积则预示现货供过于求。

返回字段：
- trade_date: 交易日期
- thscode: 期货合约代码（如 AL2611.SHF、RB2701.SHF）
- amount: 注册仓单数量
- amount_change: 仓单日变动量（正数累库、负数去库）
- equivalent_lots: 折合手/标准手`,
	DB: "futures",
	SQL: `
			SELECT trade_date, thscode, amount, amount_change, equivalent_lots
			FROM raw_futures_warehouse_receipts
			WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_warehouse_receipts)
			ORDER BY ABS(amount_change) DESC
			LIMIT ?
		`,
	Binds: []string{"limit"},
	Variants: []Variant{
		{
			Param: "thscode",
			SQL: `
			SELECT trade_date, thscode, amount, amount_change, equivalent_lots
			FROM raw_futures_warehouse_receipts
			WHERE thscode = ?
			ORDER BY trade_date DESC
			LIMIT ?
		`,
			Binds: []string{"thscode", "limit"},
		},
	},
	Params: []Param{
		{
			Name: "thscode",
			Type: "string",
			Desc: "可选。期货合约代码，如 AL2611.SHF、RB2701.SHF。指定时查询该合约历史仓单走势",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回条数限制（默认 {default}）",
			Default: 30,
			Max:     intMax(100),
		},
	},
	Envelope: Envelope{
		EchoIfSet: []string{"thscode"},
		CountKey:  "count",
		Data:      true,
		DataKey:   "items",
		Empty:     EmptyError,
		EmptyMsg:  "未查询到仓单数据",
	},
}

// FuturesBasisRate 是现货期货基差与升贴水率。
var FuturesBasisRate = Spec{
	Name: "hithink.finance.futures.basis.rate",
	Description: `获取大宗商品期货与现货基差及升贴水率（spot_price 现货价、close_price 期货收盘价、close_basis 基差、close_basis_rate 基差率）。
基差深贴水通常代表期货大幅折价或现货紧俏，存在基差修复与期现套利机会。`,
	DB: "futures",
	SQL: `
			SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
			FROM raw_futures_basis
			WHERE trade_date = (SELECT MAX(trade_date) FROM raw_futures_basis) AND close_basis IS NOT NULL
			ORDER BY ABS(close_basis_rate) DESC
			LIMIT ?
		`,
	Binds: []string{"limit"},
	Variants: []Variant{
		{
			Param: "thscode",
			SQL: `
			SELECT trade_date, thscode, spot_price, close_price, close_basis, close_basis_rate
			FROM raw_futures_basis
			WHERE thscode = ?
			ORDER BY trade_date DESC
			LIMIT ?
		`,
			Binds: []string{"thscode", "limit"},
		},
	},
	Params: []Param{
		{
			Name: "thscode",
			Type: "string",
			Desc: "可选。期货合约代码，如 CU2611.SHF、AU2612.SHF。指定时查询该合约历史基差",
		},
		{
			Name:    "limit",
			Type:    "integer",
			Desc:    "返回条数限制（默认 {default}）",
			Default: 30,
			Max:     intMax(100),
		},
	},
	Envelope: Envelope{
		EchoIfSet: []string{"thscode"},
		CountKey:  "count",
		Data:      true,
		DataKey:   "items",
		Empty:     EmptyError,
		EmptyMsg:  "未查询到基差数据",
	},
}
