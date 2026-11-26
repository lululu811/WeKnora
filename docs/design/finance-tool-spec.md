# 金融工具的一份声明 —— `hithink_finance` 的 spec + executor

这份文档记录一次深化的结果：把 `internal/agent/tools/hithink_finance/` 里形状相同
的那批工具，从「十一个各自抄了一遍同一套六步」收成「一份声明 + 一个执行器」。

## 为什么做

这批工具里，十一个的形状完全一样：**一个代码、一个窗口、一张表、一句说明**。
它们各自把同一套六步抄了一遍：

```
同步窗口 → 解析参数 → 判空 → 钳制窗口 → 执行 → 组装输出
```

抄到第十一遍时，**窗口已经漂了**：

| 迁移前 | 状态 |
|---|---|
| schema 里的 `maximum` | 十个工具**一个都没有** —— 钳制上限只活在实现里，模型看不见 |
| 说明文字里的上限 | 七个写了「最大 N」，四个一个字没提 |
| 运行时的钳制 | 500 / 200 / 800 三种值 |

也就是说：**模型被告知的窗口，和它真正能拿到的窗口，对不上**。它请求 800 天，
拿到 500 天，而 schema 里连「有上限」这件事都没说。

同时抄漏的还有别的：`FriendlyQueryError`（把 DuckDB 的 Binder Error 翻译成
「别重试这个工具，去用 query.sql」的行动指引）只有一部分工具在用，另外三个
直接把 DuckDB 的原始报错和列名候选吐给模型。

## 形状：seam 在哪

```
模型 → registry → types.Tool（四个方法）→ ┃seam┃ → spec + executor → DuckDB
```

seam 左侧是**模型面** —— `types.Tool` 的四个方法，模型和测试都从这儿跨过来。
seam 右侧是**实现** —— 取行、组装、措辞。

`types.Tool` 接口本身不动（`internal/types/agent.go:368`）。这是「接口就是测试面」
的地方：调用方和测试从同一个面跨过去，要测就得从这儿测。

## 声明长什么样

```go
type Param struct {
    Name           string   // 参数名，也是 JSON Schema 的属性名
    Type           string   // "string" | "integer" | "number" | "boolean"
    Desc           string   // JSON Schema 的 description，可以含 {default} / {max}
    Default        any      // 缺省值
    RuntimeDefault any      // 运行时真正用的缺省值（与 Default 不一致时两者都在）
    Max            *int     // 上限；钳制语义下同时是 schema 的 maximum
    Required       bool
    Over           OverRange // 超范围的处置：钳到上限，还是换回缺省值
    Explicit       []string  // 显式取值集合；不在集合里的换回 Default
    Enum           []string  // 取值集合；只发布给模型看，不是运行时约束
    Blank          []string  // 「算没给」的取值
    Pattern        string    // 必须匹配的格式
    Trim           bool      // 判空/回显/绑定之前先去掉首尾空白
    Upper          bool      // 绑定进 SQL 的值转成大写
    EmptyMsg       string    // 值为空时的报错文案
    Multi          bool      // 逗号分隔的多值参数
    ZeroMeansUnset bool      // 数值参数 <= 0 视为「没给」
    NoDefault      bool      // 缺省不兜底（缺省按模式定的时候）
}

type Envelope struct {
    Echo      []string // 原样回显的参数名
    EchoIfSet []string // 只在非空时回显的参数名
    CountKey  string   // 行数放在哪个键下
    Data      bool     // 是否把行放在 "data" 键下
    DataKey   string   // 行所在的键名（默认 "data"）
    Bare      bool     // 整份行数组就是输出，没有信封
    Compact   bool     // 紧凑 JSON，不缩进
    Empty     EmptyMode
    EmptyMsg  string   // {entity} 会被替换成回显的实体值
    EmptyHint string
}

type Spec struct {
    Name           string
    Description    string // 模板；{参数名} 换成该参数的缺省值
    DB             string
    SQL            string // 模板；? 是绑定占位符，{参数名} 换成解析后的值
    Binds          []string // ? 的绑定顺序（参数名）
    Params         []Param
    Envelope       Envelope
    Filters        []Filter  // 可选的 WHERE 条件
    FilterJoin     string    // 多个过滤条件之间的连接词
    OrderBy        *OrderBy  // 按参数选排序
    Variants       []Variant // 按参数状态二选一的查询形态
    Queries        []Query   // 多查询族
    Derive         []Derive  // 算出来的绑定值
    Composed       bool      // 结果是算出来的（Fetch / Compose 两个钩子）
    SQLFromParam   string    // 语句从哪个参数来（自由 SQL）
    DBFromParam    string    // 库名从哪个参数来
    SkipSyncWindow bool      // 不受同步窗口拦截
}
```

**窗口只有一处声明。** `Param.Max` 同时是 schema 的 `maximum`、运行时钳制的目标、
以及说明文字里「最大 N」的来源。改窗口上限只改一个地方。

## 执行器

```go
func Parameters(s Spec) json.RawMessage
func Description(s Spec) string
func Execute(ctx context.Context, cfg *hithink_finance.Config,
              s Spec, args json.RawMessage) (*types.ToolResult, error)
```

`Execute` 独占那六步。调用方（家族包里的适配器）不需要知道参数怎么绑、错误
怎么措辞、输出长什么样。

## 适配器

每个家族一个 `adapters.go`，家族包里只留还没迁的工具：

```go
type MomentumKDJTool struct{ spec.Tool }

func NewMomentumKDJTool(config *hithink_finance.Config) *MomentumKDJTool {
    return &MomentumKDJTool{spec.Tool{Config: config, Spec: spec.IndicatorMomentumKDJ}}
}

func (t *MomentumKDJTool) Name() string { return "hithink.finance.indicator.momentum.kdj" }
```

`Name()` 必须返回**字面量** —— `zettaranc/whitelist_test.go` 用正则从源码里取工具名，
认不出从声明里查表。所以工具名出现在三处：声明、适配器的字面量、注册 switch 的
case 标签。契约测试保证三处一致。

`agent_service.go` 的 48 个 case 里，47 个**一行没改** —— 构造函数的包名、函数名、
签名都保持原样。唯一改动的是 `discover`：它搬进了 `discover/` 子包（包根与 `spec`
包互相依赖会成环），于是那一条从 `hithink_finance.NewDiscoverToolWithConfig` 变成
`discover.NewToolWithConfig`。

## 契约测试

这几条不变式是这次深化的产物，不是附带：

| 测试 | 断言 |
|---|---|
| `TestToolNameIsConsistentInThreePlaces` | 声明 == 适配器字面量 == 注册 switch |
| `TestSpecSetMatchesAdapterSet` | 声明集合与适配器集合相等（双向） |
| `TestParametersMatchFrozenContract` | 派生的 schema 与说明文字，与迁移前逐字一致（只允许多出 `maximum`） |
| `TestWindowMaxIsTheSchemaMaximum` | 声明的上限 == 冻结快照里的迁移前钳制值，且运行时真的钳到它 |
| `TestExecutorSendsTheFrozenSQL` | 端到端：执行器发出去的 SQL、绑定值、**以及返回给模型的正文**都与迁移前逐字相同 |
| `TestFreshnessWarning` | 数据新鲜度告警：过期就多一条 warning，没过期就没有 |

冻结快照**按设计绕开**了「参数没给」的那几条路 —— 那些路的输出跟着日历或环境走，
快照跟着它们漂，它就不再是「迁移前那一刻的字节」了。那几条路各有自己的测试：

| 测试 | 断言 |
|---|---|
| `TestRuntimeDefaultFollowsTheCalendar` | 分红日历不给 start_date 时，缺省是**当年年初**；且 schema 上不出现缺省 |
| `TestBooleanParamReachesTheHook` | `include_noisy` 真的传到了钩子：折叠掉的信号在 `include_noisy=true` 时回来 |
| `TestFreeSQLIsGuarded` | 自由 SQL 只放 SELECT，且被拦下的语句**不会发出去** |
| `TestDiscoverIgnoresTheSyncWindow` | 同步窗口关着的时候 discover 照样能答（别的工具被拦） |

前四条看的是声明本身；`TestExecutorSendsTheFrozenSQL` 真的把**工具**跑起来，
用一个假 python-service 接住发出去的查询，**并且把工具返回给模型的那段 JSON 与
错误措辞也逐字比对**。

假服务住在 `spec/fakeservice/`（仓库里，不在测试文件里）：契约测试要拿它跟
"迁移前的行为"逐字比对，而那份行为是用同一套假数据采下来的 —— 两边必须用
**同一份**假服务，否则比的是两套不同的数据，差异没有意义。

假服务按查询里的表名返回不同的数据：行情表给一段确定的价格序列，指标表给一段
**同一个日期序列**的指标（两个 DuckDB 独立只读、ATTACH 不了，合并在 Go 里按
日期做，日期对不上就合不出指标）。绑定的代码是 `NO_DATA` 时返回空集 ——
走"没有数据"的错误路径。

假服务**记录收到的每一条查询**（不只是最后一条）：多查询的工具一次发三条，
只记最后一条就漏掉了前面两条。
声明里的 SQL 写错了、窗口的钳制没接上、绑定顺序错了、输出信封的键名取错了 ——
它都会红。

输出比对比想象中更值钱：它当场抓出了四个真差异 ——
`market.price.snapshot` 的多行 SQL 被压成了一行；飙升榜与人气榜回显的键叫
`trade_date` 而值来自 `capture_date`（我一开始写成了回显 `capture_date`）；
`limit_up_pool` 的日期格式校验把 "latest" 归一后的空值也拦了；以及
`special.hot_stock.top_list` 的 schema 少写了 `required` 键。

假服务返回的那一行，各字段的值**互不相同**（`capture_date` 与 `trade_date` 不是同
一天）—— 否则「取错了列」根本看不出来。这条是补了第一版之后才想明白的。

冻结快照 `spec/testdata/frozen.json` 是**迁移前那批工具自己吐出来的字节**：
从 `git HEAD` 的工作树里跑一个临时程序，把每个工具的 `Parameters()` 与
`Description()` 原样导出；SQL 与绑定值则用一个假 python-service 接住真实发出的
查询读出来。所以快照不会跟着代码一起漂。

每个工具记**两份探针**：一份满配（排序开关、过滤条件、变体都走非缺省路径），
一份最小配（都走缺省路径）。一份探针盖不住两条分支 —— 只用满配的那份，
「ORDER BY 的缺省片段写错」就没人发现。

两份探针里的窗口值都传 99999，所以快照同时记下了两件事实：**迁移前真正到服务
手上的那个值是多少**，以及**它是被钳到上限还是被换回缺省值**。

## 已迁移的范围

四十八个工具，十个家族，分九批。

**第一批（11 个）** —— 形状是「一个代码 + 一个窗口 + 一行时间序列」：

| 家族 | 工具 |
|---|---|
| `indicator/` | momentum.kdj、trend.ma、momentum.macd_rsi、volatility.bbands、volatility.squeeze、trend.supertrend、volume.money_flow |
| `market/` | price.historical |
| `futures/` | price.historical |
| `index/` | sector.daily |
| `special/` | hot_stock.rank_trend |

**第二批（11 个）** —— 形状是「一个（可选的）代码 + 一个数量 + 一张榜单」：

| 家族 | 工具 |
|---|---|
| `fund/` | etf.snapshot、etf.institutional_ranking、holdings.portfolio、holdings.stock |
| `futures/` | price.snapshot、variety.list |
| `index/` | sector.ranking |
| `special/` | anomaly.stock_reason、auction.snapshot、limit.break_pool、limit.ladder_detail |

**第三批（7 个）** —— 「某个交易日的榜单」与「单行快照」：

| 家族 | 工具 |
|---|---|
| `special/` | dragon_tiger.list、hot_stock.skyrocket、hot_stock.top_list、limit.limit_up_pool |
| `index/` | overview |
| `market/` | price.snapshot |
| `financial/` | valuation.snapshot |

**第四批（4 个）** —— 「一个代码 + 一个报告期数 + 一张多期表」：

| 家族 | 工具 |
|---|---|
| `financial/` | statement.balance、statement.income、statement.cashflow、indicator.detail |

**第五批（4 个）** —— 「一个代码 + 一个窗口 + 一段行情算出来的结论」：

| 家族 | 工具 |
|---|---|
| `analysis/` | levels、pattern、trend、volume |

**第六批（1 个）** —— 「多条查询 + Go 侧聚合」：

| 家族 | 工具 |
|---|---|
| `special/` | sentiment.stats |

**第七批（2 个）** —— 「一个（可选的）代码 + 一个条数 + 一张表」：

| 家族 | 工具 |
|---|---|
| `futures/` | inventory.warehouse_receipts、basis.rate |

**第八批（2 个）** —— 「一个参数两种形态」与「多值参数 + Go 侧过滤」：

| 家族 | 工具 |
|---|---|
| `index/` | sector.constituents、sector.membership |

四批加起来，声明类型一共支持十种机制 —— 全是数据、不是代码：

| 机制 | 用在 | 迁移前长什么样 |
|---|---|---|
| `Over`（超范围处置） | 11 个 | `if Limit <= 0 \|\| Limit > 100 { Limit = 30 }` |
| `OrderBy`（按参数选排序） | 4 个 | `switch params.OrderBy { case "gainers": ... }` |
| `Filters`（可选 WHERE） | 2 个 | `conds = append(conds, ...); query += " WHERE " + strings.Join(conds, " OR ")` |
| `Variants`（按参数选 SQL） | 6 个 | `if thscode != "" { query = A } else { query = B }` |
| `Explicit`（取值回落） | 1 个 | `if params.Tag != "cn_concept" { params.Tag = "industry" }` |
| `Blank`（算没给的取值） | 4 个 | `if params.TradeDate == "" \|\| params.TradeDate == "latest" { params.TradeDate = "" }` |
| `Pattern`（格式校验） | 1 个 | `if !tradeDateRe.MatchString(params.TradeDate) { ... }` |
| `RowEcho`（回显结果行的列） | 4 个 | `"trade_date": results[0]["capture_date"]` |
| `Single`（输出是那一行） | 2 个 | `result := results[0]; result["thscode"] = params.Thscode` |
| `Freshness`（数据新鲜度告警） | 1 个 | `if daysOld > 3 { result["warning"] = ... }` |

**第四批一条新机制都没用上。** 之前的设计文档写着这四个工具需要「期数展开」——
`periods` 展开成 (fiscal_year, fiscal_period) 对、绑定值个数是变的、`Binds []string`
表达不了。那是**错的**：三张报表的 SQL 是静态的，期数就是一个 `LIMIT ?` 绑定
（`WHERE thscode = ? ... LIMIT ?`），`fiscal_year` / `fiscal_period` 只是被
SELECT 出来的列。查过源码也查过线上的查询，两处都对不上——不存在任何展开逻辑。

真正要迁的不是机制，是**三段共用文字**：三张报表共用的时间序常量与共用的报告期
读法说明。它们曾经各自内联，于是三份文案与三段排序各自漂移过一次。现在在 spec
包里各是一处常量（`FinancialPeriodOrderBy` / `FinancialPeriodFieldDoc`），
三张报表共用；`financial_indicator.detail` 刻意**不**共用——它那张视图压根没有
`period` / `period_end_ms` 列，套用会 binder 报错，债务登记册里要求"统一排序
来源"的那条记载本身是错的。

**第五批是第一个"结果不是行"的家族。** 前四批的工具都是"查一批行、装进信封"；
这四个的结果是**算出来的**（道氏理论、威科夫、形态识别、支撑阻力），取数还要
查两个 DuckDB 再在 Go 里按日期合并——两个库是独立的只读连接、ATTACH 不了。

所以这一族引入了两个新东西，都在 `spec` 包里：

- **`spec.Spec.Composed`** —— 标记这个工具的结果是算出来的。契约测试据此知道
  这一族该验什么（没有 SQL、没有绑定参数）。
- **`Fetch` / `Compose` 两个钩子** —— 取数与计算都是 Go 代码，表达不成数据，
  于是它们以钩子的形式留在 `analysis` 包那边。执行器仍然负责同步窗口与参数
  解析（那四个工具曾经各自抄了一遍）。

**留在钩子那边的，是这个工具的价值本身** —— 检测形态、判趋势、找支撑阻力。
搬进声明里的，是它的**身份与契约**：名字、说明、参数、窗口。

**第六批是第一个"多查询"的家族。** 市场情绪统计要同时看涨停池、跌停池、炸板池
三张表，才能算出炸板率与最高连板。三条查询在声明里各占一条
（`spec.Spec.Queries`），由执行器跑；聚合在工具那边。

⚠️ 迁移前那两条查询的错误是**静默吞掉**的（`downRows, _ := ...`）：池子查不到
就当是 0 家，不报错。`Query.Optional` 保留了这个行为 —— 只有第一条（涨停池）
失败才致命。这条语义冻结快照盖不住（假服务总是成功），所以另有一条测试直接
让服务按表名失败。

**第七批只需要两个新机制。** 这两个工具给了合约代码就查那个合约，没给就查最新
交易日全市场 —— 两条 SQL 不同、绑定值也不同（前者绑两个，后者只绑条数）。这正是
`Variants` 机制：参数非空时选变体，否则用默认那条 ✓（第三批就为 `special` 的
四个"某个交易日的榜单"备好了这个机制）。

另外两处不同：

- **输出的行不叫 `data` 叫 `items`** —— 那是模型读到的契约的一部分，不能顺手
  改名。信封因此有了 `DataKey`。
- **给了合约代码时输出里多一个 `thscode` 字段，没给时没有** —— 回显的是"你用过
  的过滤条件"。信封的 `Echo` 规则因此是「只回显非空的」；对已有的那些工具没有
  影响（它们的回显参数都是必填的，永远非空）。

**第八批要了两个新机制。**

**一、同一个参数两种形态。** 板块成分的 `sector` 可以是板块代码（881101.TI）
也可以是板块中文名（白酒）—— 走的是**不同的列**（thscode / name）。所以变体的
触发条件不能再只是"参数非空"，得看**值的形态**：

```go
Variants: []Variant{{
    Param: "sector",
    Match: "suffix:.TI",     // 值以 .TI 结尾就走这条
    SQL:   "... u.thscode = ? ...",
    Binds: []string{"sector", "limit"},
}},
```

**二、多值参数。** 板块归属的 `tags` 是逗号分隔的板块类型（"industry,cn_concept"），
过滤在 **Go 侧**做 —— 拼进 SQL 反而多一个注入面，而候选集最多几百行。声明里
`Param.Multi` 一标，执行器就把原始字符串拆成列表交给 compose。

顺带解决了一个**绑定顺序**问题：可选的 tag 消歧条件在 WHERE 里占一个位置，
它的绑定值也必须落在那个位置（板块名 → tag → limit），而不是一律排在最前。
所以过滤条件的绑定值插在 `{where}` 所在的位置 —— 模板里 `{where}` 之前有几个
`?`，就插在第几个。

板块归属还是"Go 侧计算"那一族：输出里那个 `tags` 字段（结果里出现过的板块
类型，去重后按出现顺序逗号连接）是算出来的，所以过滤与组装都在 compose 钩子里。

**第九批（6 个）** —— 形状真的不一样的那些。它们之前保持手写，理由是「表达不成
数据」；这一批给声明补了七个机制，把它们一个不剩地收了进来：

| 家族 | 工具 |
|---|---|
| `market/` | dividend.calendar、dividend.yield_screener |
| `fund/` | etf.holders_structure |
| `pattern/` | scan |
| `query/` | sql |
| `discover/` | discover |

补的机制：

| 机制 | 用在 | 为什么需要 |
|---|---|---|
| `Enum`（取值集合） | `query.sql`、`discover` | 只**发布给模型看**它能填什么，不是运行时约束 |
| `RuntimeDefault`（运行时缺省） | `market.dividend.calendar` | 模型看到的缺省与工具真正用的缺省**本来就不一致**，两边都要留着 |
| `Derive`（推导出的绑定值） | `market.dividend.yield_screener` | 模型只给分红年度，起始日期是「{year}-01-01」，工具自己算 |
| `ZeroMeansUnset`（0 视为没给） | `market.dividend.yield_screener` | 数值参数的「<= 0 取缺省」得由声明显式说出来，不能按类型一刀切 |
| `NoDefault`（不兜底） | `discover` | depth 的缺省**按模式**定：tools 模式是 3，columns 模式不兜底 |
| `Compact`（紧凑输出） | `market.dividend.calendar` | 输出是模型读到的契约的一部分，缩不缩进不是排版偏好 |
| `SQLFromParam` / `DBFromParam` | `query.sql` | 语句与库名都是模型给的，声明里写死的那条不参与 |
| `SkipSyncWindow`（不受窗口拦截） | `discover` | 它查的是工具清单与列目录，不是行情数据 |

加上前面几批带来的 `Composed` / `Fetch` / `Compose`、`Queries` / `Query.Optional`、
`DataKey`、`EchoIfSet`、`Multi` / `Separator`、`Variant.Match`，声明类型一共支持
**二十四种机制** —— 全部是数据。

**第九批里有一个搬包。** `discover` 原来在 `hithink_finance` 包根，而它的**声明**
要住在 `spec` 包 —— `spec` 包单向依赖包根（执行器要用 `Config` 与查询函数），
包根反过来依赖 `spec` 就成了环。

于是它搬进 `discover/` 子包：依赖包根（配置、查询、"哪些列被现成工具消费"那道
判定）与 `spec` 包（声明），谁也不依赖谁。包根留下的只有那道判定本身 ——
它靠 `//go:embed */*.go` 读各工具子包的源码，而**子包里够不到兄弟目录**，
所以它必须留在包根。

## 迁移后模型看到的东西变了吗

第一批只有一处**刻意**变化：schema 里多一个 `maximum`。

第二批**没有**这一处。原因是下面那条发现：数量参数的「最大 N」不是上限，
运行时并不兑现它，所以不发布 `maximum`——发布一个兑现不了的约束，比不发布更糟。
第二批的 schema 与迁移前**逐字相同**。

工具名、参数名、参数含义、返回结构、说明文字在两批里都**逐字不变**。

另有两处**错误文本**的变化，是刻意统一的：

- 三个工具（`market.price.historical`、`indicator.momentum.kdj`、`indicator.trend.ma`）
  原本把 DuckDB 的原始报错直接吐给模型，现在走 `FriendlyQueryError`；
- `index.sector.daily` 的同步窗口错误原本被二次包装，现在不包装了
  （`FriendlyQueryError` 的注释明说同步窗口已有专门的中文提示，不要再包一层）。

还有一处**空白字符**级别的变化：`index.sector.daily` 回显的板块代码现在去掉了
首尾空白（迁移前没有）。这只影响模型传了带空格的代码时 —— 迁移前那种代码绑定
进去是查不到数据的，现在能查到。判空与绑定的大小写行为保持原样。

第三批还有一处**语义等价**的字节变化：`special.hot_stock.top_list` 的 schema
原本没写 `required` 这个键（其余 28 个都写了），现在写上了空的 `required: []`。
JSON Schema 里「没有 required」与「required 是空数组」是同一件事，所以模型的
理解不变。这不是契约，是手写 schema 的不一致被统一了。契约测试按语义比，
不因为这个空数组去要求一个只为它存在的声明字段。

## 一处被契约测试挖出来的非确定性

`analysis.levels` 的输出**每次调用可能不一样**——同一次输入，汇合区里那几个
同价水平的先后顺序会抖。契约测试连跑十次，有一次红。

查下来是三处随机性叠在一起：

1. `levels.go` 遍历 `maLevels` 这个 map 攒候选价位 —— **map 的遍历顺序是随机的**；
2. 按价格排序用的是 `sort.Slice` —— **不稳定排序**，同价元素的相对顺序随机；
3. 汇合区之间再排一次序，判据是（成员数、价格），同判据的顺序也随机。

三处都改成**全序**（遍历 map 按键序、排序加同价时的次序判据）。改完同一个输入
永远给同一个输出 ✓。

迁移前的输出本来就是随机的，所以冻结快照里 `analysis.levels` 这一条记的是
**修复后**的确定性输出 —— 其余 39 条记的仍是迁移前那份。这是快照唯一的例外，
在这里写明。

## 第二处例外：`index.overview` 改了数据源

`hithink.finance.index.overview` 查的是 `v_index_latest` —— 那是**板块快照**视图
（真库里只有 industry / region / tszs / cn_concept），里面根本没有宽基指数。
所以这个工具**一直返回空数组**：模型问"上证今天多少点"，工具答"[]"。

2026-10-25 按要求改成查 `v_index_daily`（宽基指数的日线，真库每个指数
1200+ 个交易日），并自己算涨跌与涨跌幅 —— `v_index_daily` 只有 close，没有前收。

于是冻结快照里 `index.overview` 这一条记的是**改后**的实现，其余 47 条记的仍是
迁移前那份。schema 与说明文字**没变**（参数与字段含义都没动），变的只有数据源与
输出内容。

连带改了两处闸门本身：

- **schema 契约测试**开始剥字符串字面量（`'000001.SH'` 里的 `SH` 不是列名），
  并把 `ROUND` / `NULLIF` 等函数名加进"不是列"的名单。变异验证过：把真实列名
  写错仍然会被抓红。
- **假服务**多了一条按"查询形状"分派的分支：`index.sector.daily` 查的也是
  `v_index_daily`，光按表名分派会把板块日线误答成指数概览。

## 一处被这套深化挖出来的口径问题

第二批的数量参数，两批的「超范围」语义**本来就不一样**：

```
第一批（窗口参数 days）：if Days <= 0 → 30 ; if Days > 500 → 500      钳到上限
第二批（数量参数 limit）：if Limit <= 0 || Limit > 100 → 30             换回缺省值
```

第二种语义下，**「最大 100」不是上限**。模型请求 200 条，拿到的不是 100 条，
而是缺省的 30 条。探针传 99999 进去，真正到服务手上的是 30。

迁移前这些工具的说明文字写的是「（默认 30，最大 100）」——**这句话是假的**。
十个工具里有两个的说明文字压根没提上限（`futures.price.snapshot`、
`futures.variety.list`），另外八个提了，但提的是个兑现不了的数。

这次深化做了两件事：

1. **把语义显式化**：`Param.Over` 声明它是「钳到上限」还是「换回缺省值」，
   两种语义现在各自只有一处声明，运行时与 schema 都从它派生。
2. **不发布兑现不了的 `maximum`**：只有真正钳制的参数才在 schema 里出现
   `maximum`。第一批的十个钳制参数照旧发布；第二批的换回缺省值参数不发布。

**没有动的是那句说明文字** —— 它是模型读到的契约，改它是一次模型面变更，
得单独决定。两条路：

- 改行为：把「换回缺省值」改成「钳到上限」，与说明文字对齐。模型请求 200 条
  就真的拿到 100 条。代价是行为变了。
- 改文字：把「（默认 30，最大 100）」改成「（默认 30，超出范围返回 30 条）」之类
  的实话。代价是模型读到的描述变了。

**建议先不改** —— 现在至少这件事被写下来了、并且有测试守着（`TestWindowMaxIsThe
SchemaMaximum` 断言「schema 里有没有 maximum」与「运行时到底钳到哪」是同一件事）。
改它是产品决定，不是架构决定。

## 已知的一处取舍

`Envelope` 的七个字段里，`EmptyMode` + `EmptyHint` 只服务一个工具
（`index.sector.daily` 空结果时返回带提示的空载荷，而不是报错）。这是「一个声明
覆盖异构形状」的必然代价 —— 换来的是十一个工具共用一条执行路径。

## 后续

手写工具已经**一个不剩**：48 个工具全部有声明，声明集合与适配器集合相等。

**顺带挖出来的两个真问题**（不是架构问题，是代码问题），都还没改：

1. **`market/dividend.yield_screener` 的默认年份写死成了 `"2026"`**：
   `if params.Year == "" { params.Year = "2026" }`。明年一月它会静默地筛 2026 年的
   高股息。
2. **`discover` 的 `depth` 在 schema 里写着 1，运行时用的是 3** —— 1 只放行一段名，
   于是「看看有哪些工具」默认返回空清单。

两个都是模型面与行为面的变更，不属于这次深化。这次把它们**照抄进声明**
（`Default` 是模型看到的那个，`RuntimeDefault` / `NoDefault` 是工具真正用的那个），
矛盾因此是可见的 —— 谁想知道「模型填 1 到底会怎样」，看声明就能看见。

3. **目录生成物与 schema.json 快照已经不同步**：`testdata/schema.json` 里
   `v_indicators_daily` 有 **264** 列，而生成物 `spec/sql_query_catalog.go` 写的是
   **231** 列（外加三个快照里有、生成物里没有的 `raw_*` 表）。这不是这次迁移造成的 ——
   迁移前就是这样。

   重新 `go generate` 会把目录刷成新的，而目录是**模型看到的说明文字**的一部分，
   于是它与冻结快照（迁移前那一刻的字节）冲突。这次**没有重新生成** —— 生成物原样
   搬进了 `spec` 包，冻结快照与契约测试因此仍然成立。

   顺带记一个盲区：`schema_contract_test.go` 那条只验「描述里提到的列存在于快照」，
   不验「快照里的列都被描述提到了」，所以目录少了几十列它不会红。真正该问的是
   「模型看不到那 33 列会怎样」——它问的是「有没有 TRIX 读数」这类问题，而那正是
   `discover` 工具（实时读 `information_schema`）存在的理由。

**不变的三条**：工具名三处一致；声明集合与适配器集合相等（每个声明都要有适配器）；
schema 与说明文字与迁移前逐字相同（除了钳制参数新增的 `maximum`，以及那一个语义
等价的 `required: []`）。
