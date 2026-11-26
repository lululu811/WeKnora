// Package spec holds the declarative description of the finance tools and the
// executor that runs them.
//
// 为什么是声明而不是代码：这批工具里有十一个形状完全一样——一个代码、一个窗口、
// 一张表、一句说明。它们今天各自把同一套六步抄了一遍，抄到第 11 遍时窗口上限
// 已经漂了：schema 里没有 maximum，说明文字里有五个工具没提上限，运行时钳制在
// 500，而另两个工具的钳制在 200、800。把「什么是这个工具」收成一份声明，那套
// 六步就只剩一份实现，窗口也只有一处声明。
//
// 为什么放在子包里而不是包根：schema_contract_test.go 用 os.ReadDir(".") 只
// 下钻子目录，discover.go 用 //go:embed */*.go 也只覆盖一层。放在包根，这两个
// 扫描器会同时瞎掉——它们是这套工具唯一还在生效的防线。
//
// 这个包不 import 上层的 hithink_finance 之外的东西；反过来，上层也不 import
// 它。执行器需要 common.go 里的 QueryDuckDBParams / CheckSyncWindow /
// FriendlyQueryError，所以 spec → hithink_finance 是单向的。
package spec

import "strings"

// OverRange 是参数超出上限时的处置方式。
//
// 这两种语义在迁移前就并存，今天被统一到同一个字段上：
//
//	OverRangeClamp —— 钳到上限。窗口参数（days）一直是这个语义。
//	OverRangeReset —— 回到缺省值。数量参数（limit）一直是这个语义：
//	                    请求 200 条不是拿到 100 条，而是拿到缺省的 30 条。
//
// 注意 Reset 语义下上限**不是**上限，schema 里也就不能发布 maximum ——
// 发布了就是在告诉模型一个运行时并不兑现的约束。
type OverRange int

const (
	// OverRangeClamp 把超范围的值钳到上限。
	OverRangeClamp OverRange = iota
	// OverRangeReset 把超范围的值换回缺省值。
	OverRangeReset
)

// Param 是一个工具的参数。它同时携带模型看到的 JSON Schema 片段和运行时
// 执行的语义——两者同源，所以「窗口」（缺省值 + 上限）只有一处声明。
type Param struct {
	// Name 是参数名，也是 JSON Schema 里的属性名。
	Name string
	// Type 是 JSON Schema 的 type："string"、"integer"、"number" 或 "boolean"。
	Type string
	// Desc 是 JSON Schema 的 description，可以含 {default} 与 {max}。
	Desc string
	// Default 是缺省值。参数缺省、或整数参数 <= 0 时取用。nil 表示没有缺省值。
	Default any
	// RuntimeDefault 是**运行时**真正用的缺省值。参数没给（或给了空串）时用它。
	//
	// 它与 Default 分开，是因为模型看到的缺省与工具真正用的缺省有三处本来
	// 就不一致，而 schema 是模型看得见的那一份：
	//
	//   - 分红日历的 start_date：schema 不写缺省，运行时用「当年年初」；
	//   - 分红日历的 end_date：schema 不写缺省，运行时用 2099-12-31；
	//   - discover 的 depth：schema 写 1，运行时用 3（1 只放行一段名，等于
	//     永远返回空清单）。
	//
	// 三处都不打算在这次迁移里改行为——改了就超出「只允许新增 maximum」那条例外。
	// 所以两边都记下来，矛盾才是可见的：谁想知道"模型填 1 到底会怎样"，看
	// 这一处就能看见。
	//
	// 值是字符串时按模板渲染，可以用 {year} 与 {today}——分红日历的
	// start_date 就是这样：缺省值是当年年初，跟着日历走，写死在声明里会在
	// 跨年之后悄悄筛起去年。
	RuntimeDefault any
	// Max 是上限。
	//
	// OverRangeClamp 时它就是钳制的目标，同时发布到 schema 的 maximum；
	// OverRangeReset 时它是「超范围」的判据，不发布到 schema——运行时并不会把值
	// 钳到这里，发布一个不兑现的 maximum 比不发布更糟。
	Max *int
	// Required 为真时该参数进 schema 的 required 列表。
	Required bool
	// Over 是超范围时的处置方式，默认钳到上限。
	Over OverRange
	// Explicit 是显式取值集合。取值不在集合里的，换回 Default。
	//
	// 例：板块排行的 tag 只认 "cn_concept"，其余一律当 "industry"。
	Explicit []string
	// Enum 是取值集合，**只发布到 schema**——给模型看它能填什么。
	//
	// 它不是运行时的约束：自由 SQL 的 db 写了哪些库是模型的事，填了不在集合
	// 里的库名就让它去查那个库、拿到那个库的报错，而不是被声明悄悄改写。
	//
	// 例：query.sql 的 db 列出七个 DuckDB 库名。
	Enum []string
	// Blank 是「算没给」的取值集合。命中它们的取值按空处理——
	// 于是依赖这个参数的过滤条件自动不成立。
	//
	// 例：交易日期传 "latest" 与不传是同一件事。
	Blank []string
	// Pattern 是该参数必须匹配的格式。不匹配时报 PatternMsg。
	//
	// 真正的注入防线是 ? 绑定；这个正则只负责让错误可读。
	Pattern string
	// PatternMsg 是格式不合法的报错文案。
	PatternMsg string
	// Trim 为真时，值在「判空 / 回显 / 绑定」之前先去掉首尾空白。
	Trim bool
	// Upper 为真时，绑定进 SQL 的值转成大写。回显的值不受影响。
	Upper bool
	// EmptyMsg 是值为空时的报错文案。
	EmptyMsg string
	// Multi 为真时，这个参数是逗号分隔的多值——执行器把它拆成列表，
	// compose 拿到的是列表而不是原始字符串。
	//
	// 板块归属那个工具的 tags 就是这样：逗号分隔的板块类型，拆开后按集合过滤。
	Multi bool
	// Separator 是多值参数的分隔符，默认 ","。
	Separator string
	// ZeroMeansUnset 为真时，数值参数 <= 0 视为「没给」，取缺省值。
	//
	// 整数参数一直是这个语义（"整数参数 <= 0 时取缺省值"）；数值参数只有股息率
	// 筛选那一个是这个语义——分红日历的 min_dividend 是真阈值（0 就是 0，
	// 负数也是负数），所以这条得由声明显式说出来，而不是按类型一刀切。
	ZeroMeansUnset bool
	// NoDefault 为真时，参数缺省时**不**取缺省值，而是留该类型的零值。
	//
	// 它存在是因为有一处缺省是**按模式**定的：discover 的 depth 在 tools 模式下
	// 缺省是 3，在 columns 模式下不兜底（那里 depth 只用来判断"用户是不是想用
	// 它"，兜了底就会误报"你给了 depth"）。这种缺省没法由一个参数声明表达，
	// 于是这里开一个口：声明说"不兜底"，具体怎么兜由那一边的实现决定。
	NoDefault bool
}

// Filter 是一个可选的 WHERE 条件：参数为空则这条不拼进 SQL。
//
// 多个条件之间的连接词由 Spec.FilterJoin 决定。
type Filter struct {
	// Param 是提供值的参数名。
	Param string
	// Clause 是条件模板，{v} 是绑定值的位置。
	Clause string
}

// OrderBy 是按参数选择排序子句。
type OrderBy struct {
	// Param 是选择排序的参数名。
	Param string
	// Default 是缺省（以及取值不识别时）用的 SQL 片段，不带 ORDER BY 关键字。
	Default string
	// By 是取值 → SQL 片段。
	By map[string]string
}

// Variant 是同一个工具的一种查询形态：选择它的参数非空时用这一条。
type Variant struct {
	// Param 是选择它的参数。
	Param string
	// Match 决定这条变体何时被选中：
	//
	//   ""           —— Param 非空时选中（默认）
	//   "suffix:.TI" —— Param 的值以 .TI 结尾时选中（忽略大小写）
	//   "prefix:xxx" —— Param 的值以 xxx 开头时选中（忽略大小写）
	//
	// 板块成分那个工具就是这样：板块代码（881101.TI）走 thscode 列，板块中文名
	// 走 name 列 —— 同一个参数，两种形态。
	Match string
	// SQL 是这一形态的查询。
	SQL string
	// Binds 是 ? 的绑定顺序（参数名）。
	Binds []string
}

// Envelope 描述输出信封。行的键名固定是 "data"，提示的键名固定是 "hint"——
// 这两个是模型读到的契约的一部分。
type Envelope struct {
	// Echo 是原样回显的参数名（空值也回显）。
	Echo []string
	// EchoIfSet 是"只在非空时回显"的参数名——回显的是你用过的过滤条件：
	// 没给过滤条件时，输出里也就没有它。
	//
	// 期货仓单与基差那两个工具用的是这一条。
	EchoIfSet []string
	// RowEcho 是回显的键名；值取自结果第一行。
	//
	// 有些工具回显的是数据里的日期（结果行自带的），不是传进来的参数。
	RowEcho string
	// RowEchoField 是取值所在的列名；为空时与 RowEcho 同名。
	//
	// 飙升榜与人气榜回显的键叫 trade_date，值却来自结果行的 capture_date。
	RowEchoField string
	// CountKey 是行数放在哪个键下。"" 表示不放行数。
	CountKey string
	// Data 为真时把行放在 DataKey 键下。
	Data bool
	// DataKey 是行所在的键名；为空时用 "data"。
	//
	// 有一族工具的行不叫 data 叫 items（期货仓单与基差）——那也是模型读到的
	// 契约的一部分，不能顺手改个名字。
	DataKey string
	// Bare 为真时整份行数组就是输出，没有信封。
	Bare bool
	// Empty 是空结果的处置方式。
	Empty EmptyMode
	// EmptyMsg 是 EmptyError 的报错文案模板，{entity} 会被替换成回显的实体值。
	EmptyMsg string
	// EmptyHint 是 EmptyPayload 时附在 "hint" 键下的提示。
	EmptyHint string
	// Single 为真时，输出是结果的第一行（加上 Echo 的参数），不是数组。
	Single bool
	// Compact 为真时输出不缩进——紧凑 JSON。
	//
	// 输出是模型读到的契约的一部分，而分红那两个工具迁移前就是紧凑的：
	// 缩进不缩进不是排版偏好，是那两个工具给模型的字节长什么样。
	Compact bool
	// Freshness 是数据新鲜度告警；为 nil 表示不检查。
	Freshness *Freshness
}

// Freshness 是数据新鲜度告警：结果行里的日期字段距今超过天数时，给结果加一条
// warning。
type Freshness struct {
	// Field 是结果行里的日期字段名。
	Field string
	// Days 是容忍的天数，超过就告警。
	Days int
	// Msg 是告警文案，{days} 是过期天数，{date} 是那个日期。
	Msg string
}

// EmptyMode 决定查不到行时怎么办。
type EmptyMode int

const (
	// EmptyAsUsual 没有空结果特判：照常返回载荷，data 是空数组。
	EmptyAsUsual EmptyMode = iota
	// EmptyError 空结果报错，文案是 Envelope.EmptyMsg。
	EmptyError
	// EmptyPayload 空结果返回带提示的载荷，提示是 Envelope.EmptyHint。
	EmptyPayload
)

// Query 是多查询族里的一条查询。
type Query struct {
	// Name 是这一组查询的名字，compose 按名字取结果。
	Name string
	// DB 是 DuckDB 库名。
	DB string
	// SQL 是查询文本。
	SQL string
	// Binds 是 ? 的绑定顺序（参数名）。
	Binds []string
	// Optional 为真时，这条查询失败不影响整体——按空集继续。
	//
	// 迁移前那两条就是这样：错误被静默吞掉（`downRows, _ := ...`），池子查不到
	// 就当是 0 家。这个"静默"是原行为，保留。
	Optional bool
}

// Derive 是「算出来的参数」：由模型给的参数推导出绑定值。
//
// 它不进 schema——模型看不见它，也不该填它；它只出现在绑定值里。
//
// 例：股息率筛选那个工具的起始日期是「分红年度-01-01」，模型只给年份
// （year="2026"），日期是工具算出来的。
type Derive struct {
	// Name 是算出来的参数名。
	Name string
	// Template 是取值公式，模板里可以用别的参数名。
	Template string
}

// Spec 是一个工具的声明。
type Spec struct {
	// Name 是工具名，与家族包里的 Name() 字面量、注册 switch 的 case 标签三处
	// 必须一致——由 spec 包的契约测试守住。
	Name string
	// Description 是给模型看的说明。模板里的 {参数名} 会被替换成该参数的缺省值。
	Description string
	// DB 是 DuckDB 库名。
	DB string
	// SQL 是查询模板。
	//
	//   ?          绑定占位符，绑定顺序见 Binds。
	//   {参数名}    替换成该参数解析后的值。
	//   {order}    替换成 OrderBy 选出的排序片段。
	//   {where}    替换成 Filters 拼出的 WHERE 子句（含前导空格），没有条件时为空。
	SQL string
	// Binds 是 ? 的绑定顺序（参数名）。Variants 形态用自己的 Binds。
	Binds []string
	// Params 是参数列表。
	Params []Param
	// Envelope 是输出信封。
	Envelope Envelope
	// Filters 是可选的 WHERE 条件。
	Filters []Filter
	// FilterJoin 是多个过滤条件之间的连接词（"AND" 或 "OR"），默认 "AND"。
	FilterJoin string
	// WherePrefix 是这些条件拼进 SQL 时的前缀，默认 " WHERE "。
	//
	// 条件自己就是整个 WHERE 子句时用默认值；挂在已有 WHERE 后面时用 " AND "
	// （板块成分就是：WHERE u.name = ? {where}）。
	WherePrefix string
	// OrderBy 是按参数选排序。
	OrderBy *OrderBy
	// Variants 是按参数状态二选一的查询形态。非空时，先找 Param 非空的变体；
	// 都为空则用 SQL 与 Binds。
	Variants []Variant
	// Queries 是这个工具要跑的一组查询。
	//
	// 多查询族（市场情绪统计要同时看涨停池、跌停池、炸板池）用这个：几条查询
	// 各取各的，compose 按名字取结果。顺序即执行顺序。
	Queries []Query
	// Composed 标记这个工具的结果不是行、是**算出来的**（道氏理论、威科夫、
	// 形态识别）。这一族工具取数要查多个库再在 Go 里合并，表达不成数据，
	// 于是取数与计算以 Fetch / Compose 两个钩子的形式留在工具那边。
	//
	// 标记为 true 的声明没有 SQL ——契约测试据此知道这一族该验什么。
	Composed bool
	// Derive 是算出来的绑定值。
	Derive []Derive
	// SQLFromParam 是「SQL 文本从哪个参数来」。
	//
	// 自由 SQL 那个工具就是这样：查询语句是模型给的，不是声明里写死的。声明里
	// 写死的那条 SQL 在这个模式下不参与。
	//
	// 这个模式下的错误是**原样**报出的，不套友好措辞：错的是模型自己写的
	// SQL，把它包进「这个工具暂时不可用」那层话里，模型就看不见自己错在哪。
	SQLFromParam string
	// DBFromParam 是「库名从哪个参数来」。
	DBFromParam string
	// SkipSyncWindow 为真时不检查同步窗口。
	//
	// discover 那个工具迁移前就没有这道检查：它查的是工具清单与列目录，不是
	// 行情数据，同步窗口跟它无关。
	SkipSyncWindow bool
}

// intMax 是一个取地址的辅助函数，让 spec 里的窗口上限写起来像字面量。
func intMax(v int) *int { return &v }

// paramDefaults 把声明里的缺省值摊平成一张表，Description 模板用它渲染。
func paramDefaults(s Spec) map[string]any {
	out := make(map[string]any, len(s.Params))
	for _, p := range s.Params {
		out[p.Name] = p.Default
	}
	return out
}

// entityValue 取回显的第一个参数的值，供空结果文案里的 {entity} 使用。
func entityValue(s Spec, values map[string]any) any {
	if len(s.Envelope.Echo) == 0 {
		return ""
	}
	return values[s.Envelope.Echo[0]]
}

// trimIf 按声明裁掉首尾空白。
func trimIf(v any, trim bool) any {
	if !trim {
		return v
	}
	if str, ok := v.(string); ok {
		return strings.TrimSpace(str)
	}
	return v
}

// upperIf 按声明把绑定值转成大写。
func upperIf(v any, upper bool) any {
	if !upper {
		return v
	}
	if str, ok := v.(string); ok {
		return strings.ToUpper(str)
	}
	return v
}
