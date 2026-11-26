package hithink_finance

import (
	"embed"
	"io/fs"
	"regexp"
	"strings"
)

// IndicatorDB 与 IndicatorView 是指标视图所在的库与视图名。
//
// 它们放在包根是因为「哪些列被现成工具消费」那道判定要读各工具子包的源码，
// 而读源码靠的是根包里的 //go:embed */*.go——子包里够不到兄弟目录。
const (
	IndicatorDB   = "indicators"
	IndicatorView = "v_indicators_daily"
)

// ---------- 哪些列被现成工具消费 ----------

// toolSourceFS 把各工具子包的源码嵌进二进制。
//
// 为什么不维护一份"已消费列"的手写清单或生成文件：那份清单的更新只靠人记得
// 跑生成器，而漏更新的表现是"某列明明有现成工具却被标成没有"，模型据此绕开
// 更好的工具。源码是编译进二进制的同一份文本，go:embed 每次构建都重新采集，
// 所以这里不存在"忘了同步"这个失败模式。
//
// 匹配 */*.go（各工具子包一层深）而不是逐个列目录：新增子包无需改这里。
// 包根目录的文件不含工具 SQL（这一份自己就是），漏掉它们不改变结论。
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

// 判据只有一条：**某条硬编码 SQL 的 SELECT 清单里点名了它，且那条 SQL 读的是
// v_indicators_daily**。WHERE 里出现的列（如 thscode/date）不算——那是过滤条件，
// 不是"这个指标有人用"。集合与实时列清单在 executeColumns 里取交集，
// 所以源码里写了但库里没有的列不会凭空出现在 consumed 里。
// ConsumedIndicatorColumns 扫描嵌进二进制的工具源码，返回被现成工具显式
// SELECT 过的指标列集合。
//
// 它住在包根：读源码靠的是这里的 //go:embed */*.go——子包里够不到兄弟目录。
func ConsumedIndicatorColumns() map[string]bool {
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
		if !containsObject(sql, IndicatorView) {
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
