package hithink_finance_test

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/query"
)

// 本测试是 hithink-finance 工具 SQL 与真实 DuckDB schema 之间的**契约闸门**。
//
// 背景：这些工具把 SQL 硬编码在 Go 源码里，而 schema 活在用户机器上的
// ~/.hithink-finance/*.duckdb。两者之间没有任何编译期或运行期联系，于是
// 2026-09-27 一次排查发现 11 处硬编码 SQL 里有 6 处从未对上过真实 schema
// （date→snapshot_date、pb→pb_mrq、v_skyrocket 根本没有 price 列，以及两处
// 跨库取列——indicators.v_indicators_daily 没有 close，market.v_daily_qfq
// 没有 overlap_sma_*，而两个 DuckDB 是独立只读连接、无法 ATTACH）。这些工具
// 全部静默地 400 报错，只有真的调用到才会暴露。
//
// 做法：把真实 schema 导出成 testdata/schema.json 快照，测试扫描工具源码里
// 所有反引号 SQL，抽出它们引用的表和显式列名，逐一对快照校验。CI 不需要连
// 用户的 DuckDB 就能抓漂移。
//
// 快照过期怎么办：schema 变了以后本测试会失败，那是它该做的。此时重新导出：
//
//	docker exec WeKnora-python-service python -c "<dump script>"
//
// dump 脚本见 testdata/dump_schema.py。

type schemaObject struct {
	Type    string   `json:"type"`
	Columns []string `json:"columns"`
}

var loadSchema = func(t *testing.T) map[string]map[string]schemaObject {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "schema.json"))
	if err != nil {
		t.Fatalf("读取 schema 快照失败：%v", err)
	}
	var snap map[string]map[string]schemaObject
	if err := json.Unmarshal(raw, &snap); err != nil {
		t.Fatalf("schema 快照不是合法 JSON：%v", err)
	}
	return snap
}

var (
	// 反引号包裹、且包含独立 SELECT 关键字的字符串。
	//
	// 必须用 \bSELECT\b 而不是 (?i:SELECT)：Go 的 struct tag（`json:"thscode"`）
	// 同样用反引号，会与后面原始字符串的开头反引号**错位配对**，把一段 Go 代码
	// 当成 SQL 抓出来（实测把 `if err := json.Unmarshal(...)` 连同
	// ToolResult{Success:...} 一起匹配上了）。独立词 + looksLikeSQL 双重保险。
	sqlLiteralRe = regexp.MustCompile("`([^`]*\\bSELECT\\b[^`]*)`")
	// FROM / JOIN 后面的对象名。
	fromRe = regexp.MustCompile(`(?i)\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)`)
	// SELECT 与 FROM 之间的显式列清单。
	selectListRe = regexp.MustCompile(`(?is)^\s*SELECT\s+(.*?)\s+FROM\b`)
	// 标识符。
	identRe = regexp.MustCompile(`[a-zA-Z_][a-zA-Z0-9_]*`)
	// 表别名前缀 `alias.` —— 捕获组形式，RE2 不支持 (?=) 前瞻。
	aliasPrefixRe = regexp.MustCompile(`\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)`)
	// 不能当成列名校验的 SQL 关键字 / 函数 / 类型。
	nonColumn = map[string]bool{
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
)

// indexOwner 记录每个 v_ 对象属于哪个库。快照里已验证过表名跨库全局唯一，
// 所以可以安全地由表名反推所属库。
func indexOwner(snap map[string]map[string]schemaObject) map[string]string {
	owner := make(map[string]string)
	for db, objs := range snap {
		for name := range objs {
			owner[name] = db
		}
	}
	return owner
}

// looksLikeSQL 兜底排除错位配对抓到的 Go 代码片段。真正的 SQL 不会同时出现
// struct 字面量、赋值语句或 json tag。
func looksLikeSQL(s string) bool {
	for _, marker := range []string{":=", "struct {", `json:"`, "func ", "return "} {
		if strings.Contains(s, marker) {
			return false
		}
	}
	return true
}

// stripSQLComments removes `--` line comments and `/* */` block comments from a
// SQL literal, so the column-extraction regexes below never see prose.
//
// It is a small state machine rather than a regex because the naive version
// breaks in two directions that both show up in real tool SQL:
//
//   - a `--` inside a single-quoted literal ('a--b') is data, not a comment;
//   - a `”` inside a literal is an escaped quote, not a string terminator.
//
// DuckDB only has these two comment forms (no `#`), so that is all it handles.
// An unterminated block comment swallows the rest of the string rather than
// panicking — the caller only regex-matches the result.
func stripSQLComments(sql string) string {
	var b strings.Builder
	b.Grow(len(sql))
	inStr := false
	for i := 0; i < len(sql); {
		c := sql[i]
		if inStr {
			b.WriteByte(c)
			if c == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					b.WriteByte('\'')
					i += 2
					continue
				}
				inStr = false
			}
			i++
			continue
		}
		switch {
		case c == '\'':
			inStr = true
			b.WriteByte(c)
			i++
		case c == '-' && i+1 < len(sql) && sql[i+1] == '-':
			for i < len(sql) && sql[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < len(sql) && sql[i+1] == '*':
			i += 2
			for i+1 < len(sql) && !(sql[i] == '*' && sql[i+1] == '/') {
				if sql[i] == '\n' {
					b.WriteByte('\n') // 保行号
				}
				i++
			}
			if i+1 < len(sql) {
				i += 2
			} else {
				i = len(sql)
			}
		default:
			b.WriteByte(c)
			i++
		}
	}
	return b.String()
}

// TestStripSQLComments pins the two cases a plain regex gets wrong, plus the
// regression that motivated the function: a comment mentioning a column prefix
// used to be parsed as a column reference.
func TestStripSQLComments(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		// 行内注释吃掉**本行内容但保留换行**，所以剥完之后行数不变 ——
		// 报错信息里的行号仍然对得上源码。
		{"行内注释整行去掉（保留换行）", "SELECT a,\n-- zettaranc 适配列\nb FROM t", "SELECT a,\n\nb FROM t"},
		{"行尾注释", "SELECT a FROM t -- trailing", "SELECT a FROM t "},
		{"块注释", "SELECT /* mid */ a FROM t", "SELECT  a FROM t"},
		{"多行块注释", "SELECT a /*\nmulti\nline\n*/ FROM t", "SELECT a \n\n\n FROM t"},
		{"字符串里的双横线不是注释", "SELECT 'a--b' AS x FROM t", "SELECT 'a--b' AS x FROM t"},
		{"字符串里的双引号转义", "SELECT 'it''s--x' AS y FROM t", "SELECT 'it''s--x' AS y FROM t"},
		{"未闭合块注释不 panic", "SELECT a /* unterminated", "SELECT a "},
		{"无注释原样返回", "SELECT a, b FROM t", "SELECT a, b FROM t"},
	}
	for _, c := range cases {
		got := stripSQLComments(c.in)
		if got != c.want {
			t.Errorf("%s:\n got %q\nwant %q", c.name, got, c.want)
		}
		// 行号稳定性：只有单行块注释才会减少行数，逐个豁免太啰嗦，
		// 这里只要求"不会把多行查询压成一行"。
		if strings.Count(c.in, "\n") > 1 && strings.Count(got, "\n") < 2 {
			t.Errorf("%s: 多行查询被压成单行，报错行号会失真", c.name)
		}
	}

	// 回归本体：注释里出现列名前缀，剥掉之后就不该再被当成列引用。
	sql := stripSQLComments("SELECT\n  zettaranc_zg_white_10 AS w,\n" +
		"  -- 这里解释 zettaranc 适配列\n  close AS c\nFROM v_indicators_daily")
	cleaned := regexp.MustCompile(`(?is)\bAS\s+[a-zA-Z_][a-zA-Z0-9_]*`).ReplaceAllString(sql, " ")
	for _, ident := range identRe.FindAllString(cleaned, -1) {
		if ident == "zettaranc" {
			t.Fatalf("剥注释后仍从注释里读出了裸词 zettaranc：%q", sql)
		}
	}
}

func TestSchemaSnapshotIsUsable(t *testing.T) {
	snap := loadSchema(t)
	if len(snap) != 7 {
		t.Errorf("schema 快照应覆盖 7 个库，实际 %d 个：%v", len(snap), snap)
	}
	// 这两个断言就是那两处跨库 bug 的守门人：indicators 表有均线但没有价格，
	// market 表有价格但没有均线。任何一边被误加/误删都会在这里暴露。
	ind := snap["indicators"]["v_indicators_daily"]
	if ind.Type == "" {
		t.Fatal("indicators.v_indicators_daily 缺失")
	}
	if !contains(ind.Columns, "overlap_sma_5") {
		t.Error("indicators.v_indicators_daily 应含 overlap_sma_5（均线属于指标库，不属于 market 库）")
	}
	if contains(ind.Columns, "close") {
		t.Error("indicators.v_indicators_daily 不应有 close 列（价格属于 market 库；两个库无法 ATTACH，混取必然失败）")
	}
	mkt := snap["market"]["v_daily_qfq"]
	if !contains(mkt.Columns, "close") {
		t.Error("market.v_daily_qfq 应含 close")
	}
	if contains(mkt.Columns, "overlap_sma_5") {
		t.Error("market.v_daily_qfq 不应有 overlap_sma_5（均线属于 indicators 库）")
	}
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

// TestToolSQLMatchesSchema 是本文件的主测试：扫描工具源码里每一条硬编码 SQL，
// 校验它引用的对象存在、显式列名在该对象里真实存在。
func TestToolSQLMatchesSchema(t *testing.T) {
	snap := loadSchema(t)
	owner := indexOwner(snap)

	// 包目录即工具源码所在处；本测试文件在 hithink_finance 根，子包是各工具。
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("读取工具目录失败：%v", err)
	}

	checkedSQL, checkedCols := 0, 0
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		files, err := os.ReadDir(entry.Name())
		if err != nil {
			continue
		}
		for _, f := range files {
			name := f.Name()
			if f.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			path := filepath.Join(entry.Name(), name)
			src, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("读取 %s 失败：%v", path, err)
			}
			for i, m := range sqlLiteralRe.FindAllStringSubmatch(string(src), -1) {
				// 注释必须先剥掉。2026-10-07 之前这里直接把原文交给正则，
				// 于是 analysis/data.go 里一句 `-- zettaranc 适配列……` 的
				// 行内注释被 selectListRe 当成列清单，把注释里的裸词
				// `zettaranc` 报成「引用了不存在的列」——而真实列
				// `zettaranc_zg_white_10` 就在快照里。报错信息还完全指不到
				// 真正的原因，只能让人盯着 SQL 找。
				sql := stripSQLComments(m[1])
				if !looksLikeSQL(sql) {
					continue
				}
				loc := fmt.Sprintf("%s 第 %d 条 SQL", path, i+1)

				// 1) 引用的对象必须存在于快照。
				//
				// 没有 FROM/JOIN 的反引号字面量不是查询而是散文：工具描述开头那句
				// 「只允许 SELECT 查询」也含 SELECT 一词，会被同一条 sqlLiteralRe
				// 抓进来。真正的查询必有 FROM/JOIN，这里跳过散文、只把查询计入
				// checkedSQL，末尾的「一条都没扫到」守门依然有效。
				tables := fromRe.FindAllStringSubmatch(sql, -1)
				if len(tables) == 0 {
					continue
				}
				checkedSQL++
				// JOIN 会引用多张表，列必须取**并集**。曾经这里写成"只取第一张表
				// 的列"，结果 JOIN 里的第二张表独有的列（如 v_index_universe.tag）
				// 全被误报成不存在——测试自己成了噪音，闸门就形同虚设。
				colsUnion := map[string]bool{}
				matched := false
				for _, tb := range tables {
					tbl := tb[1]
					if !strings.HasPrefix(tbl, "v_") {
						continue // 子查询里对非视图的引用不在契约范围内
					}
					db, ok := owner[tbl]
					if !ok {
						t.Errorf("%s 引用了快照里不存在的对象 %q（可能改名/删除了，或该查的是 raw_ 表）", loc, tbl)
						continue
					}
					matched = true
					for _, c := range snap[db][tbl].Columns {
						colsUnion[c] = true
					}
				}
				if !matched {
					continue
				}

				// 2) SELECT 显式列清单里的每个标识符都必须真实存在。
				sel := selectListRe.FindStringSubmatch(sql)
				if sel == nil {
					continue
				}
				// 去掉别名：`x AS y` 只校验 x；`CAST(x AS VARCHAR) AS y` 同理。
				cleaned := regexp.MustCompile(`(?is)\bAS\s+[a-zA-Z_][a-zA-Z0-9_]*`).ReplaceAllString(sel[1], " ")
				// 去掉表别名前缀：`c.thscode` 只校验 thscode。c/u 是表别名而不是列，
				// 不剥掉就会凭空报「不存在的列 c」。
				// 用捕获组而不是 `(?=)` 前瞻——Go 的 regexp 是 RE2，不支持前瞻。
				cleaned = aliasPrefixRe.ReplaceAllString(cleaned, "$2")
				// 去掉函数调用参数里的内容，只留顶层标识符——本测试的粒度是
				// "这条 SELECT 点名的列"，聚合函数内部不展开。
				cleaned = regexp.MustCompile(`\([^()]*\)`).ReplaceAllString(cleaned, " ")
				for _, ident := range identRe.FindAllString(cleaned, -1) {
					if nonColumn[strings.ToLower(ident)] {
						continue
					}
					if !colsUnion[ident] {
						t.Errorf("%s 引用了不存在的列 %q。真实列见 testdata/schema.json；"+
							"若 schema 确实变了，先重新导出快照再改这里。\nSQL: %.120s",
							loc, ident, sql)
						continue
					}
					checkedCols++
				}
			}
		}
	}

	if checkedSQL == 0 {
		t.Fatal("没有扫描到任何工具 SQL —— 扫描逻辑可能失效，契约闸门形同虚设")
	}
	t.Logf("校验了 %d 条工具 SQL、%d 个列引用", checkedSQL, checkedCols)
}

// descCatalogRe 抓 Description() 里的 `v_xxx(列, 列, …)` 目录形状。只匹配 ASCII
// 圆括号：目录里用中文括号的说明（如 `（无 v_ 视图）`）不会误入。
var descCatalogRe = regexp.MustCompile(`\b(v_[a-z0-9_]+)\(([^()]*)\)`)

// descColumnRe 只认 ASCII 标识符列名，用来滤掉 `… 共 231 列` 这类截断标记。
var descColumnRe = regexp.MustCompile(`^[a-z_][a-z0-9_]*$`)

// TestToolDescriptionColumnsMatchSchema 守着描述里的表/列目录。
//
// TestToolSQLMatchesSchema 只从工具源码的反引号字面量里抓 SELECT（要求字面量以
// SELECT 开头），而工具描述本身是一整个大反引号字面量、以中文开头，于是描述里
// 所有 SELECT 的列名它一个都验不到——v_symbol / v_futures_daily / v_fund_nav
// 的列错能长期潜伏，正是这个盲区。本测试改为直接调用 Description()，抽
// `表(列, …)` 形状里的列名逐一对快照校验。目录由 go:generate 生成（见
// query/gencatalog），因此这条断言守住生成链的出口。
func TestToolDescriptionColumnsMatchSchema(t *testing.T) {
	snap := loadSchema(t)
	owner := indexOwner(snap)

	desc := query.NewSQLQueryTool(nil).Description()

	checkedTables, checkedCols := 0, 0
	seen := map[string]bool{}
	for _, m := range descCatalogRe.FindAllStringSubmatch(desc, -1) {
		tbl, list := m[1], m[2]
		db, ok := owner[tbl]
		if !ok {
			t.Errorf("描述里引用了快照中不存在的对象 %q（可能改名/删除了）", tbl)
			continue
		}
		if seen[tbl] {
			continue
		}
		seen[tbl] = true
		checkedTables++

		known := map[string]bool{}
		for _, c := range snap[db][tbl].Columns {
			known[c] = true
		}
		for _, token := range strings.Split(list, ",") {
			token = strings.TrimSpace(token)
			if !descColumnRe.MatchString(token) {
				continue // `… 共 N 列` 之类的截断标记不是列名
			}
			if !known[token] {
				t.Errorf("描述里 %s 的列 %q 不存在于快照（真实列见 testdata/schema.json）。\n"+
					"描述由 query/gencatalog 从快照生成：先确认快照是新的，再重新 go generate。",
					tbl, token)
				continue
			}
			checkedCols++
		}
	}

	// 目录应覆盖快照里全部 v_* 视图（当前 42 个）。少于 40 说明目录塌陷或正则失效，
	// 空壳断言比没有断言更危险。
	if checkedTables < 40 {
		t.Fatalf("描述目录只解析出 %d 张表（预期 ≥40）——目录或扫描正则可能失效", checkedTables)
	}
	t.Logf("校验了描述里 %d 张表的 %d 个列名", checkedTables, checkedCols)
}
