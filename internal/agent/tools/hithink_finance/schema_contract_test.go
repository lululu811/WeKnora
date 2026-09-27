package hithink_finance_test

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
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
				sql := m[1]
				if !looksLikeSQL(sql) {
					continue
				}
				loc := fmt.Sprintf("%s 第 %d 条 SQL", path, i+1)
				checkedSQL++

				// 1) 引用的对象必须存在于快照。
				tables := fromRe.FindAllStringSubmatch(sql, -1)
				if len(tables) == 0 {
					t.Errorf("%s 未解析出 FROM/JOIN 对象，跳过：%.60s", loc, sql)
					continue
				}
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
