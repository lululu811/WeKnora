package spec

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// Parameters 由声明派生模型看到的 JSON Schema。
//
// 派生出来的字节与迁移前手写的那份一致，唯一多出来的是「钳到上限」的那部分
// 窗口参数的 "maximum"——那是刻意加的：钳制上限从此在 schema 里机器可读，
// 而不是只活在实现里。
//
// 「超范围回缺省值」的参数不发布 maximum：那个数不是上限，发布一个运行时并不
// 兑现的约束，比不发布更糟。
func Parameters(s Spec) json.RawMessage {
	properties := make(map[string]interface{}, len(s.Params))
	required := make([]string, 0, len(s.Params))
	for _, p := range s.Params {
		prop := map[string]interface{}{
			"type":        p.Type,
			"description": renderParamDesc(p),
		}
		if p.Default != nil {
			prop["default"] = p.Default
		}
		if p.Max != nil && p.Over == OverRangeClamp {
			prop["maximum"] = *p.Max
		}
		if len(p.Enum) > 0 {
			prop["enum"] = p.Enum
		}
		properties[p.Name] = prop
		if p.Required {
			required = append(required, p.Name)
		}
	}
	schema := map[string]interface{}{
		"type":       "object",
		"properties": properties,
		"required":   required,
	}
	data, _ := json.Marshal(schema)
	return data
}

// Description 渲染给模型看的说明。模板里的 {参数名} 换成该参数的缺省值，
// 所以说明文字里的「days=30」不会和 schema 里的 default 各说各话。
func Description(s Spec) string {
	return renderTemplate(s.Description, paramDefaults(s))
}

// Execute 按声明执行一个工具。
//
// 这是深模块：调用方（家族包里的适配器）只需要知道 Spec，剩下的六步——同步窗口、
// 解析参数、判空与超范围处置、组装 SQL、执行、行整形与信封——全在这里。调用方
// 不用知道参数怎么绑、错误怎么措辞、输出长什么样。
func Execute(
	ctx context.Context,
	cfg *hithink_finance.Config,
	s Spec,
	args json.RawMessage,
) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	values, err := resolve(s.Params, s.Derive, args)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	if s.SQLFromParam != "" {
		// 自由 SQL：语句与库名都是模型给的。
		//
		// 错误原样报出，不套友好措辞——错的是模型自己写的 SQL，包起来它就
		// 看不见自己错在哪了。
		rows, qerr := runFreeSQL(ctx, cfg, s, values)
		if qerr != nil {
			return &types.ToolResult{Success: false, Error: qerr.Error()}, nil
		}
		return shape(s, values, rows), nil
	}

	sql, binds := buildSQL(s, values)

	rows, qerr := hithink_finance.QueryDuckDBParams(ctx, cfg, s.DB, sql, binds...)
	if qerr != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(qerr, s.Name)}, nil
	}

	return shape(s, values, rows), nil
}

// runFreeSQL 执行模型自己写的 SQL：先过只读校验，再补上默认 LIMIT。
func runFreeSQL(
	ctx context.Context,
	cfg *hithink_finance.Config,
	s Spec,
	values map[string]any,
) ([]map[string]any, error) {
	sql, _ := values[s.SQLFromParam].(string)
	if err := hithink_finance.ValidateReadOnlySQL(sql); err != nil {
		return nil, err
	}
	sql = hithink_finance.EnsureLimit(sql, hithink_finance.DefaultLimit)
	db, _ := values[s.DBFromParam].(string)
	// 不带任何绑定值——语句是模型写的，里面已经有它自己的字面量。
	return hithink_finance.QueryDuckDBParams(ctx, cfg, db, sql)
}

// executeComposed 执行一个「取数 → Go 侧计算 → 输出」这一族工具。
//
// 与 Execute 的差别只有两点：取数由工具提供（可以有多条查询），结果由工具的
// Compose 组装（因为它不是行，是算出来的）。同步窗口与参数解析仍然在执行器
// 这里——那几个工具曾经各自抄了一遍。
func executeComposed(
	ctx context.Context,
	cfg *hithink_finance.Config,
	s Spec,
	fetch Fetch,
	compose Compose,
	args json.RawMessage,
) (*types.ToolResult, error) {
	if !s.SkipSyncWindow {
		if err := hithink_finance.CheckSyncWindow(); err != nil {
			return &types.ToolResult{Success: false, Error: err.Error()}, nil
		}
	}
	values, err := resolve(s.Params, s.Derive, args)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}
	var rows any
	if len(s.Queries) > 0 {
		// 多查询族：声明里写死的那几条查询，由执行器跑。
		sets := make(map[string][]map[string]any, len(s.Queries))
		for _, q := range s.Queries {
			r, err := hithink_finance.QueryDuckDBParams(ctx, cfg, q.DB,
				renderTemplate(q.SQL, values), queryBinds(q, values)...)
			if err != nil {
				if q.Optional {
					// 这条失败不影响整体——按空集继续（迁移前就是静默吞掉）。
					continue
				}
				return &types.ToolResult{Success: false,
					Error: hithink_finance.FriendlyQueryError(err, s.Name)}, nil
			}
			sets[q.Name] = r
		}
		rows = sets
	} else {
		r, err := fetch(ctx, cfg, values)
		if err != nil {
			return &types.ToolResult{Success: false, Error: err.Error()}, nil
		}
		rows = r
	}
	if compose == nil {
		return &types.ToolResult{Success: false, Error: "工具没有提供计算钩子"}, nil
	}
	return compose(rows, values)
}

// variantMatches 判断这条变体是否适用。
func variantMatches(v Variant, values map[string]any) bool {
	value, _ := values[v.Param].(string)
	switch {
	case v.Match == "":
		return !isBlank(values[v.Param])
	case strings.HasPrefix(v.Match, "suffix:"):
		return strings.HasSuffix(strings.ToUpper(value),
			strings.ToUpper(strings.TrimPrefix(v.Match, "suffix:")))
	case strings.HasPrefix(v.Match, "prefix:"):
		return strings.HasPrefix(strings.ToUpper(value),
			strings.ToUpper(strings.TrimPrefix(v.Match, "prefix:")))
	}
	return false
}

// whereMarker 是 SQL 模板里过滤条件所在位置的占位符。
func (s Spec) whereMarker() string { return "{where}" }

// queryBinds 按声明的绑定顺序取出一组绑定值。
func queryBinds(q Query, values map[string]any) []any {
	binds := make([]any, 0, len(q.Binds))
	for _, name := range q.Binds {
		binds = append(binds, values[name])
	}
	return binds
}

// resolve 解析参数：按声明取出每个参数的值，判空，取缺省值，按声明处置超范围。
//
// 缺省与超范围处置的顺序跟迁移前一致：整数参数 <= 0 取缺省值；超过上限时，
// OverRangeClamp 钳到上限，OverRangeReset 换回缺省值。
// 这也是「窗口只有一处声明」的地方——schema 里的 default / maximum 和运行时的
// 取值来自同一个 Param。
func resolve(params []Param, derive []Derive, args json.RawMessage) (map[string]any, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(args, &raw); err != nil {
		return nil, fmt.Errorf("参数解析失败：%v", err)
	}

	values := make(map[string]any, len(params))
	for _, p := range params {
		text, present := raw[p.Name]
		if !present {
			// 缺省时取缺省值；没有缺省值就取该类型的零值——跟"反序列化进一个
			// Go 结构体"的结果一样：字符串是 ""、整数是 0、数值是 0、布尔是 false。
			// 少了这一步，缺省的字符串会变成 nil，回显到输出里就成了
			// "tag": null 而不是 "tag": ""。
			switch {
			case p.NoDefault:
				// 声明说不兜底：缺省留零值，由那一边的实现按模式自己定。
				values[p.Name] = zeroValue(p.Type)
			case p.Default != nil:
				values[p.Name] = p.Default
			default:
				values[p.Name] = zeroValue(p.Type)
			}
			continue
		}
		switch p.Type {
		case "string":
			var v string
			if err := json.Unmarshal(text, &v); err != nil {
				return nil, fmt.Errorf("参数解析失败：%s 期望 string", p.Name)
			}
			values[p.Name] = trimIf(v, p.Trim)
		case "integer":
			var v int
			if err := json.Unmarshal(text, &v); err != nil {
				return nil, fmt.Errorf("参数解析失败：%s 期望 integer", p.Name)
			}
			values[p.Name] = v
		case "number":
			var v float64
			if err := json.Unmarshal(text, &v); err != nil {
				return nil, fmt.Errorf("参数解析失败：%s 期望 number", p.Name)
			}
			values[p.Name] = v
		case "boolean":
			var v bool
			if err := json.Unmarshal(text, &v); err != nil {
				return nil, fmt.Errorf("参数解析失败：%s 期望 boolean", p.Name)
			}
			values[p.Name] = v
		default:
			return nil, fmt.Errorf("参数解析失败：%s 的类型 %q 未定义", p.Name, p.Type)
		}
	}

	// 「算没给」的取值按空处理：命中 Blank 的取值换回空串，
	// 依赖它的过滤条件于是自动不成立。
	for _, p := range params {
		if len(p.Blank) == 0 {
			continue
		}
		str, _ := values[p.Name].(string)
		for _, b := range p.Blank {
			if str == b {
				values[p.Name] = ""
				break
			}
		}
	}

	// 格式校验。真正的注入防线是 ? 绑定，这个正则只负责让错误可读。
	for _, p := range params {
		if p.Pattern == "" {
			continue
		}
		str, _ := values[p.Name].(string)
		// 空值（"latest" 归一之后）不校验格式——那不是日期，是"没给日期"。
		if str == "" {
			continue
		}
		if !patternMatch(p.Pattern, str) {
			return nil, fmt.Errorf(p.PatternMsg, str)
		}
	}

	// 判空只看必填的字符串参数。
	for _, p := range params {
		if !p.Required || p.Type != "string" {
			continue
		}
		str, _ := values[p.Name].(string)
		if str == "" {
			return nil, fmt.Errorf("%s", p.EmptyMsg)
		}
	}

	// 整数的缺省与超范围处置。
	for _, p := range params {
		if p.Type != "integer" {
			continue
		}
		n, _ := values[p.Name].(int)
		switch {
		case n <= 0:
			if p.NoDefault {
				// 声明说不兜底：缺省留零值，由那一边的实现按模式自己定。
				break
			}
			if d, ok := p.effectiveDefault().(int); ok {
				n = d
			}
		case p.Max != nil && n > *p.Max:
			if p.Over == OverRangeReset {
				if d, ok := p.effectiveDefault().(int); ok {
					n = d
				}
			} else {
				n = *p.Max
			}
		}
		values[p.Name] = n
	}

	// 数值参数的"0 视为没给"——只有声明里说了才算数。
	for _, p := range params {
		if p.Type != "number" || !p.ZeroMeansUnset {
			continue
		}
		n, _ := values[p.Name].(float64)
		if n <= 0 {
			if d, ok := p.effectiveDefault().(float64); ok {
				n = d
			}
		}
		values[p.Name] = n
	}

	// 运行时的缺省值：值算空时用它。
	//
	// 这一步排在整数的处置之后——整数那条已经按 effectiveDefault() 取过值了，
	// 再走一遍不会重复覆盖（取到的值不是空的）。
	for _, p := range params {
		if p.RuntimeDefault == nil {
			continue
		}
		if !isBlank(values[p.Name]) {
			continue
		}
		values[p.Name] = p.runtimeDefault()
	}

	// 多值参数：逗号分隔的原始字符串拆成列表。
	for _, p := range params {
		if !p.Multi {
			continue
		}
		sep := p.Separator
		if sep == "" {
			sep = ","
		}
		raw, _ := values[p.Name].(string)
		var list []string
		for _, item := range strings.Split(raw, sep) {
			if v := strings.TrimSpace(item); v != "" {
				list = append(list, v)
			}
		}
		values[p.Name] = list
	}

	// 显式取值集合：不在集合里的换回缺省值。
	for _, p := range params {
		if len(p.Explicit) == 0 {
			continue
		}
		str, _ := values[p.Name].(string)
		if !containsString(p.Explicit, str) {
			values[p.Name] = p.Default
		}
	}

	// 算出来的参数：由模型给的参数推导出绑定值。
	//
	// 排在最后——它要用的是**解析后**的参数值（年份、代码都已经定型了）。
	for _, d := range derive {
		values[d.Name] = renderTemplate(d.Template, values)
	}

	return values, nil
}

// zeroValue 是某个参数类型在「没给」时的零值。
func zeroValue(typ string) any {
	switch typ {
	case "integer":
		return 0
	case "number":
		return float64(0)
	case "boolean":
		return false
	default:
		return ""
	}
}

// effectiveDefault 是这个参数在运行时真正用的缺省值。
//
// RuntimeDefault 优先于 Default：模型看到的缺省（Default）与工具真正用的缺省
// 不一致时，以工具真正用的那个为准——不然声明就是在说谎。
func (p Param) effectiveDefault() any {
	if p.RuntimeDefault != nil {
		return p.RuntimeDefault
	}
	return p.Default
}

// runtimeDefault 是运行时缺省值的最终形态。
//
// 字符串按模板渲染，于是缺省值可以跟着日历走：分红日历的起始日期缺省是
// 「当年年初」，写死成 2026 就会在跨年之后悄悄筛起去年。
func (p Param) runtimeDefault() any {
	if s, ok := p.RuntimeDefault.(string); ok {
		return renderTemplate(s, ambientValues())
	}
	return p.RuntimeDefault
}

// ambientValues 是运行时才知道的几个值，可以出现在缺省值模板里。
func ambientValues() map[string]any {
	now := time.Now()
	return map[string]any{
		"year":  now.Format("2006"),
		"today": now.Format("2006-01-02"),
		"now":   now.Format(time.RFC3339),
	}
}

// buildSQL 选出查询形态，渲染模板，拼出 WHERE 与排序，组装绑定值。
//
// 绑定顺序：过滤条件的值在前，静态绑定的值在后。所以 SQL 模板里的 {where}
// 必须排在任何静态 ? 之前——这是声明的硬约束，由契约测试守住。
func buildSQL(s Spec, values map[string]any) (string, []any) {
	sql, binds := s.SQL, s.Binds
	for _, v := range s.Variants {
		if variantMatches(v, values) {
			sql, binds = v.SQL, v.Binds
			break
		}
	}

	repl := make(map[string]any, len(values)+2)
	for k, v := range values {
		repl[k] = v
	}
	if s.OrderBy != nil {
		repl["order"] = s.orderClause(values)
	}

	// 过滤条件拼在 {where} 位置，它们的绑定值也落在同一个位置——
	// 也就是模板里 {where} 之前有几个 ?，过滤条件的绑定值就插在第几个。
	// （期货那两个工具的 {where} 在最前面，所以过滤条件的绑定值排在最前，
	//  与迁移前一致；板块成分的 {where} 在板块条件之后，绑定值就排在中间。）
	filterParams := []string{}
	if len(s.Filters) > 0 {
		clause, names := s.whereClause(values)
		repl["where"] = clause
		filterParams = names
	}

	// 过滤条件的绑定值插在 {where} 所在的位置：**模板**里 {where} 之前有几个 ?，
	// 就插在第几个（要在渲染之前数——渲染之后 {where} 已经被替换掉了）。
	// 没有 {where} 时一律追加到末尾——那种声明是错的，契约测试会报。
	at := len(binds)
	if idx := strings.Index(sql, s.whereMarker()); idx >= 0 {
		at = strings.Count(sql[:idx], "?")
	}

	out := renderTemplate(sql, repl)
	all := make([]any, 0, len(filterParams)+len(binds))
	for i, name := range binds {
		if i == at && len(filterParams) > 0 {
			for _, fn := range filterParams {
				all = append(all, s.bindValue(fn, values))
			}
		}
		all = append(all, s.bindValue(name, values))
	}
	if at >= len(binds) && len(filterParams) > 0 {
		for _, fn := range filterParams {
			all = append(all, s.bindValue(fn, values))
		}
	}
	return out, all
}

// whereClause 拼出可选的 WHERE 子句，并给出这次真正要绑定的参数名。
func (s Spec) whereClause(values map[string]any) (string, []string) {
	join := s.FilterJoin
	if join == "" {
		join = "AND"
	}
	prefix := s.WherePrefix
	if prefix == "" {
		prefix = " WHERE "
	}
	var parts []string
	var names []string
	for _, f := range s.Filters {
		if isBlank(values[f.Param]) {
			continue
		}
		parts = append(parts, strings.ReplaceAll(f.Clause, "{v}", "?"))
		names = append(names, f.Param)
	}
	if len(parts) == 0 {
		return "", nil
	}
	return prefix + strings.Join(parts, " "+join+" "), names
}

// orderClause 按参数选出排序片段，不识别的取值用缺省片段。
func (s Spec) orderClause(values map[string]any) string {
	if s.OrderBy == nil {
		return ""
	}
	key, _ := values[s.OrderBy.Param].(string)
	if c, ok := s.OrderBy.By[key]; ok {
		return c
	}
	return s.OrderBy.Default
}

// bindValue 取出绑定值，并按声明做大小写变换。
func (s Spec) bindValue(name string, values map[string]any) any {
	for _, p := range s.Params {
		if p.Name == name {
			return upperIf(values[name], p.Upper)
		}
	}
	return values[name]
}

// param 按名字取参数声明。
func (s Spec) param(name string) (Param, bool) {
	for _, p := range s.Params {
		if p.Name == name {
			return p, true
		}
	}
	return Param{}, false
}

// shape 按声明组装输出信封。
func shape(s Spec, values map[string]any, rows []map[string]interface{}) *types.ToolResult {
	e := s.Envelope

	if e.Single {
		if len(rows) == 0 {
			return &types.ToolResult{Success: false, Error: renderTemplate(e.EmptyMsg,
				map[string]any{"entity": entityValue(s, values)})}
		}
		row := make(map[string]interface{}, len(rows[0])+len(e.Echo))
		for k, v := range rows[0] {
			row[k] = v
		}
		for _, name := range e.Echo {
			row[name] = values[name]
		}
		for _, name := range e.EchoIfSet {
			if isBlank(values[name]) {
				continue
			}
			row[name] = values[name]
		}
		if e.Freshness != nil {
			if dateStr, ok := row[e.Freshness.Field].(string); ok {
				if dataDate, err := time.Parse("2006-01-02", dateStr); err == nil {
					daysOld := int(time.Since(dataDate).Hours() / 24)
					if daysOld > e.Freshness.Days {
						row["warning"] = renderTemplate(e.Freshness.Msg, map[string]any{
							"days": daysOld, "date": dateStr,
						})
					}
				}
			}
		}
		res, err := marshalOutput(row, e.Compact)
		if err != nil {
			return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}
		}
		return &types.ToolResult{Success: true, Output: string(res)}
	}

	if e.Bare {
		res, err := marshalOutput(rows, e.Compact)
		if err != nil {
			return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}
		}
		return &types.ToolResult{Success: true, Output: string(res)}
	}

	if len(rows) == 0 && e.Empty == EmptyError {
		msg := renderTemplate(e.EmptyMsg, map[string]any{"entity": entityValue(s, values)})
		return &types.ToolResult{Success: false, Error: msg}
	}

	// 行的载荷必须写成复合字面量：dead_columns.go 用正则认「查询结果以裸标识符
	// 出现在复合字面量的值位置」来判定「整行透传」——工具把整份结果交给模型，
	// 读列的是模型而不是代码，于是它选的所有列都算被消费。spec 目录下的 SQL 与
	// 这段组装代码同属一个包，透传判定按包算，所以这些列不会被误报成死列。
	dataKey := e.DataKey
	if dataKey == "" {
		dataKey = "data"
	}
	var output map[string]interface{}
	if e.Data {
		output = map[string]interface{}{dataKey: rows}
	} else {
		output = map[string]interface{}{}
	}

	for _, name := range e.Echo {
		output[name] = values[name]
	}
	// 这一组是"用过的过滤条件"——没给过滤条件时，输出里也就没有它。
	for _, name := range e.EchoIfSet {
		if isBlank(values[name]) {
			continue
		}
		output[name] = values[name]
	}
	if e.RowEcho != "" && len(rows) > 0 {
		field := e.RowEchoField
		if field == "" {
			field = e.RowEcho
		}
		output[e.RowEcho] = rows[0][field]
	}
	if e.CountKey != "" {
		output[e.CountKey] = len(rows)
	}
	if len(rows) == 0 && e.Empty == EmptyPayload {
		output["hint"] = e.EmptyHint
	}

	res, err := marshalOutput(output, e.Compact)
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}
	}
	return &types.ToolResult{Success: true, Output: string(res)}
}

// marshalOutput 序列化输出。
//
// 紧凑还是缩进不是排版偏好：输出是模型读到的契约的一部分，分红那两个工具
// 迁移前吐出来的就是紧凑 JSON。
func marshalOutput(v any, compact bool) ([]byte, error) {
	if compact {
		return json.Marshal(v)
	}
	return json.MarshalIndent(v, "", "  ")
}

// renderParamDesc 渲染参数说明里的 {default} 与 {max}。
//
// 参数说明里出现「默认 30，最大 500」这种话时，它说的是窗口；窗口的缺省值和
// 上限已经在 Param 里声明过一次了，所以这里渲染而不是再写一遍——说明文字改
// 窗口上限时，不用记得同时改三处。
func renderParamDesc(p Param) string {
	values := map[string]any{
		"default": p.Default,
		"max":     "",
	}
	if p.Max != nil {
		values["max"] = *p.Max
	}
	return renderTemplate(p.Desc, values)
}

// renderTemplate 把模板里的 {名字} 换成对应的值。
//
// 同一个模板语法在两处用，含义不同：Description 模板里的 {参数名} 换成该参数的
// 缺省值，SQL 模板里的 {参数名} 换成该参数解析后的值。
func renderTemplate(tpl string, values map[string]any) string {
	var b strings.Builder
	for i := 0; i < len(tpl); {
		if tpl[i] != '{' {
			b.WriteByte(tpl[i])
			i++
			continue
		}
		end := strings.IndexByte(tpl[i:], '}')
		if end < 0 {
			b.WriteString(tpl[i:])
			break
		}
		name := tpl[i+1 : i+end]
		v, ok := values[name]
		if !ok {
			b.WriteString(tpl[i : i+end+1])
		} else {
			b.WriteString(fmt.Sprint(v))
		}
		i += end + 1
	}
	return b.String()
}

// isBlank 判断一个参数值是否算「没给」。
func isBlank(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		return strings.TrimSpace(t) == ""
	case int:
		return t == 0
	case float64:
		return t == 0
	case bool:
		// 布尔没有"没给"这回事：false 是一个值，不是一片空白。
		return false
	}
	return false
}

// patternMatch 判断一个值是否符合参数声明的格式。
func patternMatch(pattern, value string) bool {
	re, err := regexp.Compile(pattern)
	if err != nil {
		return true
	}
	return re.MatchString(value)
}

// containsString 判断字符串是否在集合里。
func containsString(list []string, s string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}
