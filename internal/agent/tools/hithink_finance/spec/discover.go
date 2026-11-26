package spec

// DiscoverIndicatorGroups 是 v_indicators_daily 的列族前缀，按字母序（输出按 count 排序）。
var DiscoverIndicatorGroups = []string{
	"candles", "cycles", "momentum", "overlap", "performance",
	"statistics", "trend", "volatility", "volume", "zettaranc",
}

// DiscoverMetaGroup 收纳不属于任何指标列族的列：thscode/date/backend/computed_at。
const DiscoverMetaGroup = "meta"

// DiscoverGroups 是列清单模式可用的全部列族（含 meta）。
func DiscoverGroups() []string {
	g := append([]string{}, DiscoverIndicatorGroups...)
	return append(g, DiscoverMetaGroup)
}

// Discover 是工具发现工具。
//
// 两种模式：按名称前缀列工具（默认），或列指标视图的实时列清单。
//
// 两种模式的取数与组装都跨包（读注册表、读 information_schema），表达不成数据，
// 于是它们以 Fetch / Compose 两个钩子的形式留在 discover 包；声明这边只管身份
// 与契约：名字、说明、参数。
//
// 它不检查同步窗口：查的是工具清单与列目录，不是行情数据。
var Discover = Spec{
	Name: "hithink.finance.discover",
	Description: `发现 hithink 金融数据能力。两种模式，用 kind 切换。

【kind="tools"（默认）】按名称前缀列出已注册的子 tools。
  prefix 留空 → 返回全部 hithink.finance tools（默认 depth=3，够覆盖所有三层工具名）。
  prefix="hithink.finance.market" depth=2 → 只列 market 分支。
  depth=工具名相对 prefix 最多允许多多段："hithink.finance.market.price.snapshot" 相对
  prefix="hithink.finance.market" 是 2 段，相对 "hithink.finance" 是 3 段。

【kind="columns"】列出 indicators.v_indicators_daily 的**全部指标列**，用于确认某个
指标在库里到底存不存在；确认后用 hithink.finance.query.sql（db="indicators"）取数。
  列名实时取自 DuckDB 的 information_schema，不是硬编码清单，因此不会与真实库漂移。
  group="momentum"   按列族过滤：momentum/volume/volatility/trend/overlap/cycles/
                     statistics/performance/candles/zettaranc；meta=主键与时间戳列
                     （thscode/date/backend/computed_at，非指标列）
  match="trix"       按列名子串过滤（不区分大小写）
  consumed="yes"     只看**已被现成工具消费的列**：某个 hithink finance 工具的 SQL 里
                     显式 SELECT 了它（analysis/indicator/pattern 三个包），意味着有现成
                     的信号工具在用它。consumed="no" 反过来——这些列没有任何现成信号
                     工具，你只能自己写 query.sql 取。默认 "any" 两者都要。
  三个条件可以叠加。不带任何过滤时只返回分组概览（每组列数 + 样本列名），
  不会把上百个列名一次性灌进上下文。

边界（别指望它做别的事）：
  - 本工具只回答「**有什么**」，不回答「**该用哪个 tool**」——那是系统提示词里的
    路由表负责的事，本工具不做语义推荐。
  - kind="columns" 只覆盖 indicators.v_indicators_daily。行情/财务/指数/特色/基金/
    期货的视图与列清单写在 hithink.finance.query.sql 的 description 里。
  - 读不到 DuckDB 时本工具**返回错误而不是空清单**：空清单会被读成「这些列不存在」。`,
	Composed:       true,
	SkipSyncWindow: true,
	Params: []Param{
		{
			Name:    "kind",
			Type:    "string",
			Desc:    "tools=按前缀列工具（默认）；columns=列 indicators.v_indicators_daily 的指标列清单。",
			Default: "tools",
			// enum 只发布给模型看：运行时还认 "tool" / "column" 这两个别名。
			Enum: []string{"tools", "columns"},
		},
		{
			Name: "prefix",
			Type: "string",
			Desc: "kind=tools：tool 名称前缀，如 'hithink.finance.market'。留空返回所有 hithink.finance tools。",
		},
		{
			Name: "depth",
			Type: "integer",
			Desc: "kind=tools：展开深度（默认 3），指工具名相对 prefix 最多允许多少段。" +
				"注意 'hithink.finance.market.price.snapshot' 相对 'hithink.finance.market' 是 2 段，" +
				"要列全 market 分支需 depth>=2。",
			// schema 写 1、运行时用 3——这两处本来就不一致（1 只放行一段名，等于
			// 永远返回空清单）。改 schema 超出「只允许新增 maximum」那条例外，所以
			// 照抄进声明，矛盾才是可见的。
			//
			// NoDefault：缺省值是**按模式**定的——tools 模式缺省是 3，columns 模式
			// 不兜底（那里 depth 只用来判断"用户是不是想用它"，兜了底就会误报
			// "你给了 depth"）。一个参数声明表达不了这种缺省，于是开这个口：
			// 声明说不兜底，具体怎么兜由那一边的实现决定。
			Default:   1,
			NoDefault: true,
		},
		{
			Name: "group",
			Type: "string",
			Desc: "kind=columns：按列族前缀过滤。meta 表示主键/时间戳等非指标列。",
			Enum: DiscoverGroups(),
		},
		{
			Name: "match",
			Type: "string",
			Desc: "kind=columns：按列名子串过滤（不区分大小写），如 'trix'。",
		},
		{
			Name:    "consumed",
			Type:    "string",
			Desc:    "kind=columns：只保留有现成信号工具消费的列（yes）、只保留没有任何现成工具消费、需自己 query.sql 的列（no），或不筛选（any）。",
			Default: "any",
			Enum:    []string{"any", "yes", "no"},
		},
	},
}
