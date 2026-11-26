// Package discover 是 hithink finance 的发现工具。
//
// 为什么它单独一个包：它要读工具注册表、还要查 information_schema 列清单，
// 两件事都跨包；而它的**声明**（名字、说明、参数）住在 spec 包。
//
// spec 包单向依赖 hithink_finance 根包（执行器要用 Config 与查询函数），
// 所以根包不能反过来依赖 spec 包——发现工具若留在根包，两者就成了环。
// 于是它住在这一层：依赖根包（配置、查询、被消费的列）与 spec 包（声明），
// 谁也不依赖谁。
package discover

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// Tool 是发现工具。
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
type Tool struct {
	spec.Tool
	registry *tools.ToolRegistry
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

// NewTool 创建发现工具。
//
// 保留这个签名（配置为 nil → 运行时回落到 DefaultConfig）是为了让既有调用方
// 注册路径零改动；带配置用 NewToolWithConfig。
func NewTool(registry *tools.ToolRegistry) *Tool {
	return NewToolWithConfig(registry, nil)
}

// NewToolWithConfig 创建带 python-service 配置的发现工具。
func NewToolWithConfig(registry *tools.ToolRegistry, config *hithink_finance.Config) *Tool {
	t := &Tool{registry: registry}
	t.Tool = spec.Tool{
		Config:  config,
		Spec:    spec.Discover,
		Fetch:   t.fetch,
		Compose: t.compose,
	}
	return t
}

// Name 返回工具名。
func (t *Tool) Name() string { return "hithink.finance.discover" }

// Ensure Tool implements types.Tool.
var _ types.Tool = (*Tool)(nil)

// fetch 是取数：两种模式各取各的。
//
// 参数解析与校验在 spec 那边（这里收到的是解析好的 values），这里只负责按模式
// 取原料：工具清单模式读注册表，列清单模式读 information_schema。
func (t *Tool) fetch(
	ctx context.Context,
	cfg *hithink_finance.Config,
	values map[string]any,
) (any, error) {
	kind, _ := values["kind"].(string)
	switch strings.ToLower(strings.TrimSpace(kind)) {
	case "", "tools", "tool":
		prefix, _ := values["prefix"].(string)
		depth, _ := values["depth"].(int)
		return t.listTools(prefix, depth), nil
	case "columns", "column":
		group, _ := values["group"].(string)
		consumed, _ := values["consumed"].(string)
		if _, _, err := checkColumnFilters(group, consumed); err != nil {
			return nil, err
		}
		cols, err := t.loadIndicatorColumns(ctx, cfg)
		if err != nil {
			// 读不到库时把错误翻译成模型能照着做的下一步——宁可报错也不返回
			// 空清单：空清单会被读成「这些列不存在」，而那是假的。
			return nil, errors.New(columnsUnavailableError(err, cfg))
		}
		return cols, nil
	default:
		return nil, fmt.Errorf(
			"参数错误：kind=%q 不认识。可用取值 'tools'（默认）或 'columns'。", kind)
	}
}

// compose 是组装：两种模式各装各的。
func (t *Tool) compose(rows any, values map[string]any) (*types.ToolResult, error) {
	kind, _ := values["kind"].(string)
	switch strings.ToLower(strings.TrimSpace(kind)) {
	case "", "tools", "tool":
		listing, _ := rows.(toolsListing)
		return composeTools(listing)
	case "columns", "column":
		catalog, _ := rows.([]ColumnInfo)
		group, _ := values["group"].(string)
		match, _ := values["match"].(string)
		consumed, _ := values["consumed"].(string)
		prefix, _ := values["prefix"].(string)
		depth, _ := values["depth"].(int)
		return composeColumns(catalog, group, match, consumed, prefix, depth)
	default:
		return nil, fmt.Errorf(
			"参数错误：kind=%q 不认识。可用取值 'tools'（默认）或 'columns'。", kind)
	}
}

// toolsListing 是工具清单模式的取数结果。
type toolsListing struct {
	Prefix string
	Depth  int
	Tools  []ToolInfo
}

// listTools 列出注册表里前缀匹配的工具。
//
// 保持原有实现不变：同样的过滤、同样的字段、同样的 hint 文案。
func (t *Tool) listTools(prefix string, depth int) toolsListing {
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

	return toolsListing{Prefix: prefix, Depth: depth, Tools: matchedTools}
}

// composeTools 组装工具清单模式的输出。
func composeTools(listing toolsListing) (*types.ToolResult, error) {
	matchedTools := listing.Tools

	// Build result
	result := map[string]interface{}{
		"prefix": listing.Prefix,
		"depth":  listing.Depth,
		"tools":  matchedTools,
		"count":  len(matchedTools),
	}

	// Add usage hint
	if len(matchedTools) == 0 {
		result["hint"] = fmt.Sprintf(
			"未找到前缀为 '%s' 的 tools。请尝试更短的前缀，如 'hithink.finance'", listing.Prefix)
	} else if len(matchedTools) > 20 {
		result["hint"] = fmt.Sprintf(
			"找到 %d 个 tools，建议使用更具体的前缀，如 'hithink.finance.market' 或 'hithink.finance.financial'",
			len(matchedTools))
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
	// maxColumnRows 是单次返回的列名上限。超了要**明说截断了**，而不是让模型
	// 以为这就是全部——截断的清单和不截断的清单，区别只有模型知不知道。
	maxColumnRows = 80
	// groupSampleSize 是无过滤时每个分组给的样本列名个数（优先给有现成工具消费的）。
	groupSampleSize = 6
)

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

// ToolInfo holds information about a tool.
type ToolInfo struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

// loadIndicatorColumns reads the live column inventory of the indicator view.
//
// 任何失败都原样上抛给调用方，由 composeColumns 转成可执行的错误文案。
// 这里**不返回空切片**：空清单对模型来说等于"这些列不存在"。
func (t *Tool) loadIndicatorColumns(
	ctx context.Context,
	cfg *hithink_finance.Config,
) ([]ColumnInfo, error) {
	rows, err := hithink_finance.QueryDuckDBParams(
		ctx, cfg, hithink_finance.IndicatorDB, indicatorColumnsSQL, hithink_finance.IndicatorView)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("%s.%s 存在但没有返回任何列定义（information_schema 查询异常）",
			hithink_finance.IndicatorDB, hithink_finance.IndicatorView)
	}

	declared := toInt(rows[0]["total_columns"])
	if declared > len(rows) {
		return nil, fmt.Errorf("%s.%s 声明有 %d 列，只取回 %d 列（结果被截断），拒绝返回不完整的清单",
			hithink_finance.IndicatorDB, hithink_finance.IndicatorView, declared, len(rows))
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
		return nil, fmt.Errorf(
			"%s.%s 的列清单解析后为空（information_schema 返回了 %d 行但没有可用的 column_name）",
			hithink_finance.IndicatorDB, hithink_finance.IndicatorView, len(rows))
	}
	return cols, nil
}

// groupOf 把列名映射到列族。前缀不在已知列族里的一律归 meta —— 归到一个
// 不存在的列族比归 meta 更危险：那会让 group 过滤的结果看起来像"这一族是空的"。
func groupOf(name string) string {
	i := strings.Index(name, "_")
	if i <= 0 {
		return spec.DiscoverMetaGroup
	}
	for _, g := range spec.DiscoverIndicatorGroups {
		if name[:i] == g {
			return g
		}
	}
	return spec.DiscoverMetaGroup
}

// checkColumnFilters 校验列清单模式的两个过滤参数。
//
// 拼错列族、拼错 consumed 时必须报错：返回空列表会被读成"这一族不存在"。
func checkColumnFilters(group, consumed string) (string, string, error) {
	consumedMode := strings.ToLower(strings.TrimSpace(consumed))
	if consumedMode == "" {
		consumedMode = "any"
	}
	if consumedMode != "any" && consumedMode != "yes" && consumedMode != "no" {
		return "", "", fmt.Errorf(
			"参数错误：consumed=%q 不认识。可用取值 'any'（不筛选）、'yes'、'no'。", consumed)
	}

	group = strings.ToLower(strings.TrimSpace(group))
	if group != "" && !isKnownGroup(group) {
		// 拼错列族时必须报错：返回空列表会被读成"这一族不存在"。
		return "", "", fmt.Errorf("参数错误：group=%q 不是 %s.%s 的列族。可用取值：%s",
			group, hithink_finance.IndicatorDB, hithink_finance.IndicatorView,
			strings.Join(knownGroups(), ", "))
	}
	return group, consumedMode, nil
}

// composeColumns 组装列清单模式的输出。
func composeColumns(
	catalog []ColumnInfo, group, match, consumed, prefix string, depth int,
) (*types.ToolResult, error) {
	group, consumedMode, err := checkColumnFilters(group, consumed)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	consumedSet := hithink_finance.ConsumedIndicatorColumns()
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
		"view":           hithink_finance.IndicatorDB + "." + hithink_finance.IndicatorView,
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
			hithink_finance.IndicatorDB+"."+hithink_finance.IndicatorView,
			len(catalog), totalConsumed, hithink_finance.IndicatorDB)
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
			entry["sample_note"] = "该组共 " + fmt.Sprint(len(items)) +
				" 列，此处只列样本；用 group 过滤取全名"
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
			group, match, consumedMode,
			hithink_finance.IndicatorDB, hithink_finance.IndicatorView, totalCatalog)
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
		len(matched), hithink_finance.IndicatorDB)
}

// columnsUnavailableError 把"读不到库"翻译成模型能照着做的下一步。
//
// 关键约束：宁可报错也不返回空清单。空清单 = "这些列不存在"，
// 那正是本工具要消灭的那种谎。
func columnsUnavailableError(err error, cfg *hithink_finance.Config) string {
	url := serviceURL(cfg)
	return fmt.Sprintf(
		"无法读取 %s.%s 的列清单：%v\n"+
			"本工具**不会**用空清单代替——空清单会被读成「这些列不存在」，而这是假的。\n"+
			"请检查：1) python-service 是否在运行（curl %s/health）；"+
			"2) PYTHON_SERVICE_URL 是否指向它；3) indicators.duckdb 是否可读。\n"+
			"恢复后重试。在此之前，只能依据 hithink.finance.query.sql 的 description 里"+
			"已写明的列族前缀推断，不能断言某一列存在或不存在。",
		hithink_finance.IndicatorDB, hithink_finance.IndicatorView, err, url)
}

func serviceURL(cfg *hithink_finance.Config) string {
	if cfg != nil && cfg.ServiceURL != "" {
		return cfg.ServiceURL
	}
	return hithink_finance.DefaultConfig().ServiceURL
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
	g := append([]string{}, spec.DiscoverIndicatorGroups...)
	return append(g, spec.DiscoverMetaGroup)
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
