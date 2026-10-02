package hithink_finance

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"regexp"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/types"
)

// DiscoverTool is the discovery tool for hithink finance tools.
//
// 两种模式：
//   - kind="tools"（默认）：按名称前缀列出已注册的子 tools。行为自 2026-09 起保持不变。
//   - kind="columns"：列出 indicators.v_indicators_daily 的**实时**列清单。
//
// 列清单为什么必须实时取：hithink.finance.query.sql 没有列白名单，231 个指标列
// 全都可达，但模型无从知道它们存在——"有没有 TRIX 读数"这类问题因此答不上来。
// 而清单一旦被复制进源码/快照就会漂移，漂移的方向恰好是"谎报不存在"，
// 这类 bug 本仓已经吃过一次（当时的表现是模型反复猜列名、连试四次才绕道）。
// testdata/schema.json 同理不能当运行时数据源：它是 schema_contract_test.go
// 维护的**测试闸门**，不是运行时事实。
type DiscoverTool struct {
	registry *tools.ToolRegistry
	config   *Config
}

// defaultToolDepth 是 depth 省略时使用的值。
//
// **它曾经是 1，而 1 让本工具的默认调用永远返回 0 个工具。** depth 数的是「工具名
// 相对 prefix 的段数」，而真实的工具全是三段名——`hithink.finance.market.price.snapshot`
// 相对 `hithink.finance` 是 3 段、`hithink.finance.indicator.trend.ma` 也是 3 段。
// depth=1 只匹配到 `hithink.finance.discover` 自己，其余一个都匹配不上。
//
// 于是模型不传 depth 地调一次「看看有哪些工具」，拿到的是空清单——而空清单会被读成
// 「没有别的工具了」，和今晚修的那批静默失效是同一种病：系统什么都没说，模型却
// 以为自己知道了。描述里那句「prefix 留空返回全部 hithink.finance tools」当时也
// 是假的。
//
// 3 刚好覆盖现有的最深工具名（最深也是 3 段），既能一次列全，又不会把非本族的
// 工具拖进来。
const defaultToolDepth = 3

// NewDiscoverTool creates a new discovery tool.
//
// 保留这个签名（config 为 nil → 运行时回落到 DefaultConfig）是为了让既有调用方
// financialserv 注册路径零改动；带配置用 NewDiscoverToolWithConfig。
func NewDiscoverTool(registry *tools.ToolRegistry) *DiscoverTool {
	return &DiscoverTool{registry: registry}
}

// NewDiscoverToolWithConfig creates a discovery tool bound to a python-service config.
func NewDiscoverToolWithConfig(registry *tools.ToolRegistry, config *Config) *DiscoverTool {
	return &DiscoverTool{registry: registry, config: config}
}

func (t *DiscoverTool) Name() string {
	return "hithink.finance.discover"
}

func (t *DiscoverTool) Description() string {
	return `发现 hithink 金融数据能力。两种模式，用 kind 切换。

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
  - 读不到 DuckDB 时本工具**返回错误而不是空清单**：空清单会被读成「这些列不存在」。`
}

func (t *DiscoverTool) Parameters() json.RawMessage {
	groups := append([]string{}, indicatorGroups...)
	groups = append(groups, metaGroup)
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"kind": map[string]interface{}{
				"type":        "string",
				"enum":        []string{"tools", "columns"},
				"default":     "tools",
				"description": "tools=按前缀列工具（默认）；columns=列 indicators.v_indicators_daily 的指标列清单。",
			},
			"prefix": map[string]interface{}{
				"type":        "string",
				"description": "kind=tools：tool 名称前缀，如 'hithink.finance.market'。留空返回所有 hithink.finance tools。",
			},
			"depth": map[string]interface{}{
				"type": "integer",
				"description": "kind=tools：展开深度（默认 3），指工具名相对 prefix 最多允许多少段。" +
					"注意 'hithink.finance.market.price.snapshot' 相对 'hithink.finance.market' 是 2 段，" +
					"要列全 market 分支需 depth>=2。",
				"default": 1,
			},
			"group": map[string]interface{}{
				"type":        "string",
				"enum":        groups,
				"description": "kind=columns：按列族前缀过滤。meta 表示主键/时间戳等非指标列。",
			},
			"match": map[string]interface{}{
				"type":        "string",
				"description": "kind=columns：按列名子串过滤（不区分大小写），如 'trix'。",
			},
			"consumed": map[string]interface{}{
				"type":    "string",
				"enum":    []string{"any", "yes", "no"},
				"default": "any",
				"description": "kind=columns：只保留有现成信号工具消费的列（yes）、" +
					"只保留没有任何现成工具消费、需自己 query.sql 的列（no），或不筛选（any）。",
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *DiscoverTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Prefix   string `json:"prefix"`
		Depth    int    `json:"depth"`
		Kind     string `json:"kind"`
		Group    string `json:"group"`
		Match    string `json:"match"`
		Consumed string `json:"consumed"`
	}

	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	switch strings.ToLower(strings.TrimSpace(params.Kind)) {
	case "", "tools", "tool":
		// kind 省略时行为与该模式引入前逐字节一致。
		return t.executeTools(params.Prefix, params.Depth)
	case "columns", "column":
		return t.executeColumns(ctx, params.Group, params.Match, params.Consumed, params.Prefix, params.Depth)
	default:
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数错误：kind=%q 不认识。可用取值 'tools'（默认）或 'columns'。", params.Kind),
		}, nil
	}
}

// ---------- kind=tools ----------

// executeTools 保持原有实现不变：同样的过滤、同样的字段、同样的 hint 文案。
func (t *DiscoverTool) executeTools(prefix string, depth int) (*types.ToolResult, error) {
	if prefix == "" {
		prefix = "hithink.finance"
	}
	if depth <= 0 {
		depth = defaultToolDepth
	}

	// Get all registered tools
	allTools := t.registry.ListTools()

	// Filter by prefix and depth
	var matchedTools []ToolInfo
	seen := make(map[string]bool)

	for _, name := range allTools {
		if !strings.HasPrefix(name, prefix) {
			continue
		}

		// Calculate the relative path
		suffix := strings.TrimPrefix(name, prefix)
		if suffix == "" {
			continue // Skip the prefix itself
		}
		suffix = strings.TrimPrefix(suffix, ".")

		parts := strings.Split(suffix, ".")
		if len(parts) > depth {
			continue
		}

		// Avoid duplicates
		if seen[name] {
			continue
		}
		seen[name] = true

		// Get tool description
		tool, err := t.registry.GetTool(name)
		if err != nil {
			continue
		}

		matchedTools = append(matchedTools, ToolInfo{
			Name:        name,
			Description: tool.Description(),
		})
	}

	// Sort by name for stable output
	sort.Slice(matchedTools, func(i, j int) bool {
		return matchedTools[i].Name < matchedTools[j].Name
	})

	// Build result
	result := map[string]interface{}{
		"prefix": prefix,
		"depth":  depth,
		"tools":  matchedTools,
		"count":  len(matchedTools),
	}

	// Add usage hint
	if len(matchedTools) == 0 {
		result["hint"] = fmt.Sprintf("未找到前缀为 '%s' 的 tools。请尝试更短的前缀，如 'hithink.finance'", prefix)
	} else if len(matchedTools) > 20 {
		result["hint"] = fmt.Sprintf("找到 %d 个 tools，建议使用更具体的前缀，如 'hithink.finance.market' 或 'hithink.finance.financial'", len(matchedTools))
	}

	output, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(output),
	}, nil
}

// ---------- kind=columns ----------

const (
	indicatorDB   = "indicators"
	indicatorView = "v_indicators_daily"

	// maxColumnRows 是单次返回的列名上限。超了要**明说截断了**，而不是让模型
	// 以为这就是全部——截断的清单和不截断的清单，区别只有模型知不知道。
	maxColumnRows = 80
	// groupSampleSize 是无过滤时每个分组给的样本列名个数（优先给有现成工具消费的）。
	groupSampleSize = 6

	// metaGroup 收纳不属于任何指标列族的列：thscode/date/backend/computed_at。
	metaGroup = "meta"
)

// indicatorGroups 是 v_indicators_daily 的列族前缀，按字母序（输出按 count 排序）。
var indicatorGroups = []string{
	"candles", "cycles", "momentum", "overlap", "performance",
	"statistics", "trend", "volatility", "volume", "zettaranc",
}

// indicatorColumnsSQL 从 DuckDB 的 information_schema 直接读列清单。
//
// 为什么是 information_schema 而不是某张元数据表：前者由 DuckDB 自己维护，
// 与 SELECT * 实际能返回的列同源，加列/删列立刻可见；任何"我们自己维护的
// 清单"都需要第二次同步，而同步失败的表现是谎报列不存在。
//
// count(*) OVER () 是为了发现 python-service 的外层 LIMIT 把结果截断了
// （见 python-service/main.py _apply_limit）。截断时宁可报错也不返回半份清单。
const indicatorColumnsSQL = `SELECT column_name, data_type, count(*) OVER () AS total_columns
FROM information_schema.columns
WHERE table_name = ?
ORDER BY ordinal_position`

// ColumnInfo is one column of the indicator view.
type ColumnInfo struct {
	Name     string `json:"name"`
	Group    string `json:"group"`
	DataType string `json:"data_type"`
	// Consumed=true 表示某个现成 hithink finance 工具的 SQL 显式 SELECT 了这一列。
	Consumed bool `json:"consumed"`
}

// loadIndicatorColumns reads the live column inventory of the indicator view.
//
// 任何失败都原样上抛给调用方，由 executeColumns 转成可执行的错误文案。
// 这里**不返回空切片**：空清单对模型来说等于"这些列不存在"。
func (t *DiscoverTool) loadIndicatorColumns(ctx context.Context) ([]ColumnInfo, error) {
	rows, err := QueryDuckDBParams(ctx, t.config, indicatorDB, indicatorColumnsSQL, indicatorView)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("%s.%s 存在但没有返回任何列定义（information_schema 查询异常）", indicatorDB, indicatorView)
	}

	declared := toInt(rows[0]["total_columns"])
	if declared > len(rows) {
		return nil, fmt.Errorf("%s.%s 声明有 %d 列，只取回 %d 列（结果被截断），拒绝返回不完整的清单",
			indicatorDB, indicatorView, declared, len(rows))
	}

	cols := make([]ColumnInfo, 0, len(rows))
	for _, r := range rows {
		name := fmt.Sprint(r["column_name"])
		if name == "" || name == "<nil>" {
			continue
		}
		cols = append(cols, ColumnInfo{
			Name:     name,
			Group:    groupOf(name),
			DataType: fmt.Sprint(r["data_type"]),
		})
	}
	if len(cols) == 0 {
		return nil, fmt.Errorf("%s.%s 的列清单解析后为空（information_schema 返回了 %d 行但没有可用的 column_name）",
			indicatorDB, indicatorView, len(rows))
	}
	return cols, nil
}

// groupOf 把列名映射到列族。前缀不在已知列族里的一律归 meta —— 归到一个
// 不存在的列族比归 meta 更危险：那会让 group 过滤的结果看起来像"这一族是空的"。
func groupOf(name string) string {
	i := strings.Index(name, "_")
	if i <= 0 {
		return metaGroup
	}
	for _, g := range indicatorGroups {
		if name[:i] == g {
			return g
		}
	}
	return metaGroup
}

func (t *DiscoverTool) executeColumns(
	ctx context.Context, group, match, consumed, prefix string, depth int,
) (*types.ToolResult, error) {
	consumedMode := strings.ToLower(strings.TrimSpace(consumed))
	if consumedMode == "" {
		consumedMode = "any"
	}
	if consumedMode != "any" && consumedMode != "yes" && consumedMode != "no" {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数错误：consumed=%q 不认识。可用取值 'any'（不筛选）、'yes'、'no'。", consumed),
		}, nil
	}

	group = strings.ToLower(strings.TrimSpace(group))
	if group != "" && !isKnownGroup(group) {
		// 拼错列族时必须报错：返回空列表会被读成"这一族不存在"。
		return &types.ToolResult{
			Success: false,
			Error: fmt.Sprintf("参数错误：group=%q 不是 %s.%s 的列族。可用取值：%s",
				group, indicatorDB, indicatorView, strings.Join(knownGroups(), ", ")),
		}, nil
	}

	catalog, err := t.loadIndicatorColumns(ctx)
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   t.columnsUnavailableError(err),
		}, nil
	}

	consumedSet := consumedIndicatorColumns()
	totalConsumed := 0
	for i := range catalog {
		catalog[i].Consumed = consumedSet[catalog[i].Name]
		if catalog[i].Consumed {
			totalConsumed++
		}
	}

	matched := catalog
	if group != "" {
		matched = filterColumns(matched, func(c ColumnInfo) bool { return c.Group == group })
	}
	if m := strings.ToLower(strings.TrimSpace(match)); m != "" {
		matched = filterColumns(matched, func(c ColumnInfo) bool { return strings.Contains(c.Name, m) })
	}
	switch consumedMode {
	case "yes":
		matched = filterColumns(matched, func(c ColumnInfo) bool { return c.Consumed })
	case "no":
		matched = filterColumns(matched, func(c ColumnInfo) bool { return !c.Consumed })
	}

	result := map[string]interface{}{
		"kind":           "columns",
		"view":           indicatorDB + "." + indicatorView,
		"total_columns":  len(catalog),
		"consumed_total": totalConsumed,
		"filters": map[string]interface{}{
			"group":    group,
			"match":    strings.TrimSpace(match),
			"consumed": consumedMode,
		},
	}

	if prefix != "" || depth > 1 {
		result["note"] = "kind=columns 忽略 prefix/depth（它们只对 kind=tools 有效）"
	}

	// 无过滤 = 分组概览：上百个列名一次性返回是上下文污染，模型只会更瞎。
	if group == "" && strings.TrimSpace(match) == "" && consumedMode == "any" {
		result["groups"] = summarizeGroups(matched, totalConsumed)
		result["hint"] = fmt.Sprintf(
			"%s 共 %d 列，其中 %d 列被现成 hithink finance 工具直接消费（有现成信号工具在用）。"+
				"要具体列名请收窄：group=\"momentum\" 或 match=\"trix\"；只要有现成信号的列用 consumed=\"yes\"。"+
				"确认列名存在后用 hithink.finance.query.sql（db=\"%s\"）取数。",
			indicatorDB+"."+indicatorView, len(catalog), totalConsumed, indicatorDB)
	} else {
		shown := matched
		truncated := false
		if len(shown) > maxColumnRows {
			shown = shown[:maxColumnRows]
			truncated = true
		}
		if shown == nil {
			shown = []ColumnInfo{}
		}
		result["matched"] = len(matched)
		result["truncated"] = truncated
		result["columns"] = shown
		result["hint"] = columnsHint(len(catalog), matched, len(shown), truncated, group, match, consumedMode)
	}

	output, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}
	return &types.ToolResult{Success: true, Output: string(output)}, nil
}

func filterColumns(in []ColumnInfo, keep func(ColumnInfo) bool) []ColumnInfo {
	out := make([]ColumnInfo, 0, len(in))
	for _, c := range in {
		if keep(c) {
			out = append(out, c)
		}
	}
	return out
}

// summarizeGroups 按列族聚合。样本列名优先取"有现成工具消费"的，
// 因为那正是模型接下来最可能要用的一批。
func summarizeGroups(cols []ColumnInfo, totalConsumed int) []map[string]interface{} {
	byGroup := make(map[string][]ColumnInfo)
	for _, c := range cols {
		byGroup[c.Group] = append(byGroup[c.Group], c)
	}

	names := make([]string, 0, len(byGroup))
	for g := range byGroup {
		names = append(names, g)
	}
	sort.Strings(names)

	out := make([]map[string]interface{}, 0, len(names))
	for _, g := range names {
		items := byGroup[g]
		consumed, rest := splitByConsumed(items)
		sample := make([]string, 0, groupSampleSize)
		for i := 0; i < len(consumed) && len(sample) < groupSampleSize; i++ {
			sample = append(sample, consumed[i].Name)
		}
		for i := 0; i < len(rest) && len(sample) < groupSampleSize; i++ {
			sample = append(sample, rest[i].Name)
		}
		entry := map[string]interface{}{
			"group":    g,
			"count":    len(items),
			"consumed": len(consumed),
			"sample":   sample,
		}
		if omitted := len(items) - len(sample); omitted > 0 {
			entry["sample_omitted"] = omitted
			entry["sample_note"] = "该组共 " + fmt.Sprint(len(items)) + " 列，此处只列样本；用 group 过滤取全名"
		}
		out = append(out, entry)
	}
	return out
}

func splitByConsumed(cols []ColumnInfo) (consumed, rest []ColumnInfo) {
	for _, c := range cols {
		if c.Consumed {
			consumed = append(consumed, c)
		} else {
			rest = append(rest, c)
		}
	}
	sort.Slice(consumed, func(i, j int) bool { return consumed[i].Name < consumed[j].Name })
	sort.Slice(rest, func(i, j int) bool { return rest[i].Name < rest[j].Name })
	return consumed, rest
}

func columnsHint(totalCatalog int, matched []ColumnInfo, shown int, truncated bool, group, match, consumedMode string) string {
	if len(matched) == 0 {
		return fmt.Sprintf(
			"本次过滤（group=%q match=%q consumed=%q）没有命中任何列。%s.%s 实际有 %d 列，"+
				"所以这是过滤条件太窄，**不代表这些列不存在**。去掉 group/consumed 再试，"+
				"或只传 match 并换一个更短的子串。",
			group, match, consumedMode, indicatorDB, indicatorView, totalCatalog)
	}
	if truncated {
		return fmt.Sprintf("命中 %d 列，已截断到前 %d 列（上限 %d）。请加 group 或 match 收窄。",
			len(matched), shown, maxColumnRows)
	}
	if consumedMode == "yes" {
		return "这些列都有现成 hithink finance 工具在消费，优先用对应信号工具；" +
			"需要自己算再用 hithink.finance.query.sql（db=\"indicators\"）。"
	}
	if consumedMode == "no" {
		return "这些列**没有任何**现成信号工具消费，只能自己写 hithink.finance.query.sql 取数" +
			"（db=\"indicators\"）。注意 NULL 表示未计算，不要当 0 用。"
	}
	return fmt.Sprintf("命中 %d 列。确认列名存在后用 hithink.finance.query.sql 取数（db=\"%s\"）。",
		len(matched), indicatorDB)
}

// columnsUnavailableError 把"读不到库"翻译成模型能照着做的下一步。
//
// 关键约束：宁可报错也不返回空清单。空清单 = "这些列不存在"，
// 那正是本工具要消灭的那种谎。
func (t *DiscoverTool) columnsUnavailableError(err error) string {
	url := t.serviceURL()
	return fmt.Sprintf(
		"无法读取 %s.%s 的列清单：%v\n"+
			"本工具**不会**用空清单代替——空清单会被读成「这些列不存在」，而这是假的。\n"+
			"请检查：1) python-service 是否在运行（curl %s/health）；"+
			"2) PYTHON_SERVICE_URL 是否指向它；3) indicators.duckdb 是否可读。\n"+
			"恢复后重试。在此之前，只能依据 hithink.finance.query.sql 的 description 里"+
			"已写明的列族前缀推断，不能断言某一列存在或不存在。",
		indicatorDB, indicatorView, err, url)
}

func (t *DiscoverTool) serviceURL() string {
	if t.config != nil && t.config.ServiceURL != "" {
		return t.config.ServiceURL
	}
	return DefaultConfig().ServiceURL
}

func isKnownGroup(g string) bool {
	for _, known := range knownGroups() {
		if g == known {
			return true
		}
	}
	return false
}

func knownGroups() []string {
	g := append([]string{}, indicatorGroups...)
	return append(g, metaGroup)
}

func toInt(v interface{}) int {
	switch x := v.(type) {
	case float64:
		return int(x)
	case int64:
		return int(x)
	case int:
		return x
	case json.Number:
		i, _ := x.Int64()
		return int(i)
	case string:
		var n int
		_, _ = fmt.Sscanf(x, "%d", &n)
		return n
	default:
		return 0
	}
}

// ---------- 哪些列被现成工具消费 ----------

// toolSourceFS 把各工具子包的源码嵌进二进制。
//
// 为什么不维护一份"已消费列"的手写清单或生成文件：那份清单的更新只靠人记得
// 跑生成器，而漏更新的表现是"某列明明有现成工具却被标成没有"，模型据此绕开
// 更好的工具。源码是编译进二进制的同一份文本，go:embed 每次构建都重新采集，
// 所以这里不存在"忘了同步"这个失败模式。
//
// 匹配 */*.go（各工具子包一层深）而不是逐个列目录：新增子包无需改这里。
// 包根目录的文件不含工具 SQL（discover.go 自己就是），漏掉它们不改变结论。
//
//go:embed */*.go
var toolSourceFS embed.FS

// 与 schema_contract_test.go 同一套 SQL 字面量识别规则。之所以复刻而不是复用：
// 那是个 _test.go，测试二进制里才有，生产二进制里不存在。
var (
	// 反引号包裹、含独立 SELECT 关键字的字符串。用独立词而不是 (?i:SELECT)，
	// 否则 Go 的 struct tag 反引号会与原始字符串错位配对。
	toolSQLLiteralRe = regexp.MustCompile("`([^`]*\\bSELECT\\b[^`]*)`")
	// FROM / JOIN 后的对象名。
	sqlObjectRe = regexp.MustCompile(`(?i)\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)`)
	// SELECT 与 FROM 之间的显式列清单。
	sqlSelectListRe = regexp.MustCompile(`(?is)^\s*SELECT\s+(.*?)\s+FROM\b`)
	// 标识符。
	sqlIdentRe = regexp.MustCompile(`[a-zA-Z_][a-zA-Z0-9_]*`)
	// 表别名前缀 `alias.`，RE2 不支持前瞻所以用捕获组。
	sqlAliasRe = regexp.MustCompile(`\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)`)
	// 函数调用的参数部分：本工具的粒度是"这条 SELECT 点名的列"，聚合函数内部不展开。
	sqlFuncArgsRe = regexp.MustCompile(`\([^()]*\)`)
	// 别名：`x AS y` 只算 x。
	sqlAliasAsRe = regexp.MustCompile(`(?is)\bAS\s+[a-zA-Z_][a-zA-Z0-9_]*`)
)

// sqlNonColumn 不能当成列名校验的关键字/函数/类型。
var sqlNonColumn = map[string]bool{
	"select": true, "from": true, "where": true, "and": true, "or": true,
	"not": true, "null": true, "as": true, "cast": true, "varchar": true,
	"order": true, "by": true, "desc": true, "asc": true, "limit": true,
	"group": true, "having": true, "join": true, "left": true, "inner": true,
	"on": true, "max": true, "min": true, "count": true, "sum": true,
	"avg": true, "coalesce": true, "date": true, "in": true, "is": true,
	"true": true, "false": true, "int": true, "integer": true, "double": true,
	"between": true, "like": true, "distinct": true, "case": true, "when": true,
	"then": true, "else": true, "end": true, "interval": true,
}

// consumedIndicatorColumns 扫描嵌进二进制的工具源码，返回被现成工具显式 SELECT 过的
// 指标列集合。
//
// 判据只有一条：**某条硬编码 SQL 的 SELECT 清单里点名了它，且那条 SQL 读的是
// v_indicators_daily**。WHERE 里出现的列（如 thscode/date）不算——那是过滤条件，
// 不是"这个指标有人用"。集合与实时列清单在 executeColumns 里取交集，
// 所以源码里写了但库里没有的列不会凭空出现在 consumed 里。
func consumedIndicatorColumns() map[string]bool {
	consumed := make(map[string]bool)
	_ = fs.WalkDir(toolSourceFS, ".", func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil //nolint:nilerr // 读不到的文件跳过：少标一个 consumed 的后果远小于整体失败
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		src, readErr := toolSourceFS.ReadFile(path)
		if readErr != nil {
			return nil
		}
		for _, c := range indicatorColumnsInSource(string(src)) {
			consumed[c] = true
		}
		return nil
	})
	return consumed
}

// indicatorColumnsInSource 抽出 src 里所有"读 v_indicators_daily 的 SQL"的显式列名。
func indicatorColumnsInSource(src string) []string {
	var out []string
	for _, m := range toolSQLLiteralRe.FindAllStringSubmatch(src, -1) {
		sql := m[1]
		// 错位配对会把一段 Go 代码当成 SQL 抓出来。真 SQL 不会同时出现
		// struct 字面量、赋值语句或 json tag。
		if looksLikeGoCode(sql) {
			continue
		}
		if !containsObject(sql, indicatorView) {
			continue
		}
		sel := sqlSelectListRe.FindStringSubmatch(sql)
		if sel == nil {
			continue
		}
		cleaned := sqlAliasAsRe.ReplaceAllString(sel[1], " ")
		cleaned = sqlAliasRe.ReplaceAllString(cleaned, "$2")
		cleaned = sqlFuncArgsRe.ReplaceAllString(cleaned, " ")
		for _, ident := range sqlIdentRe.FindAllString(cleaned, -1) {
			if sqlNonColumn[strings.ToLower(ident)] {
				continue
			}
			out = append(out, ident)
		}
	}
	return out
}

func looksLikeGoCode(s string) bool {
	for _, marker := range []string{":=", "struct {", `json:"`, "func ", "return "} {
		if strings.Contains(s, marker) {
			return true
		}
	}
	return false
}

func containsObject(sql, obj string) bool {
	for _, m := range sqlObjectRe.FindAllStringSubmatch(sql, -1) {
		if strings.EqualFold(m[1], obj) {
			return true
		}
	}
	return false
}

// ToolInfo holds information about a tool.
type ToolInfo struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

// Ensure DiscoverTool implements the Tool interface.
var _ types.Tool = (*DiscoverTool)(nil)
