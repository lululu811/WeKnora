package pattern

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// 本测试补上 schema_contract_test.go 看不见的那一半。
//
// 上游那份契约闸门解析 SELECT 列表时会先用 `\([^()]*\)` 把函数调用的参数整段抹掉，
// 再校验剩下的标识符。实测：把一个不存在的列名写进 `COALESCE(坏列, 0)` 里，
// TestToolSQLMatchesSchema **不会报错**；只有裸写的 `SELECT 坏列 FROM ...` 才会被抓。
//
// 而这个包的 query.go 里每一个指标列都恰好是 COALESCE 包着的——因为缺失必须补 0
// 才能进 row 结构。也就是说，对本包而言那份闸门恰好在最需要它的地方失明：所有
// 指标列名它一个都验不到，一个拼错的列名会安静地查不出数据、下游恒读出 0，
// 再由阈值判据报出一堆看起来完全正常的信号。
//
// 上游那份测试不在本任务的可改范围内，所以在这里补一份针对性的：把 query.go 里
// 每个 COALESCE 的**第一个实参**（也就是真实列名）连同裸列一起，对着
// testdata/schema.json 的 indicators.v_indicators_daily 逐条校验。
// 快照本身由上游那个测试守护（库数、跨库列那些断言都在那边）。

// coalesceColRe 抓 COALESCE(列, ...) 里的列名。
var coalesceColRe = regexp.MustCompile(`(?i)COALESCE\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*,`)

// bareColRe 抓 CAST(列 AS VARCHAR) 这类不套 COALESCE 的裸列。
var bareColRe = regexp.MustCompile(`(?i)CAST\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s+VARCHAR\s*\)`)

func TestQueryColumnsExistInSchema(t *testing.T) {
	src, err := os.ReadFile("query.go")
	if err != nil {
		t.Fatalf("读取 query.go 失败：%v", err)
	}
	text := string(src)

	// 快照路径相对本包：pattern/ → hithink_finance/testdata/
	raw, err := os.ReadFile(filepath.Join("..", "testdata", "schema.json"))
	if err != nil {
		t.Fatalf("读取 schema 快照失败：%v", err)
	}
	var snap map[string]map[string]struct {
		Type    string   `json:"type"`
		Columns []string `json:"columns"`
	}
	if err := json.Unmarshal(raw, &snap); err != nil {
		t.Fatalf("schema 快照不是合法 JSON：%v", err)
	}
	obj, ok := snap["indicators"]["v_indicators_daily"]
	if !ok {
		t.Fatal("快照里没有 indicators.v_indicators_daily")
	}
	known := map[string]bool{}
	for _, c := range obj.Columns {
		known[c] = true
	}

	checked := 0
	fail := func(col, where string) {
		t.Errorf("query.go 引用了 indicators.v_indicators_daily 里不存在的列 %q（%s）。\n"+
			"快照是权威：%d 个列。若确属 schema 变更，先重新导出 testdata/schema.json。\n"+
			"注意上游 TestToolSQLMatchesSchema 看不见 COALESCE 里的列名，这一栏只有本测试守着。",
			col, where, len(obj.Columns))
	}

	for _, m := range coalesceColRe.FindAllStringSubmatch(text, -1) {
		checked++
		if !known[m[1]] {
			fail(m[1], "COALESCE 实参")
		}
	}
	for _, m := range bareColRe.FindAllStringSubmatch(text, -1) {
		checked++
		if !known[m[1]] {
			fail(m[1], "CAST 实参")
		}
	}
	// 少于 50 条说明正则失效了（query.go 里光 COALESCE 就有 60 多条），那本测试
	// 就变成了永远绿的空壳——空壳比没有更危险。
	if checked < 50 {
		t.Fatalf("只校验到 %d 个列名，正则多半没匹配上，本测试已失效", checked)
	}
	t.Logf("校验了 %d 个列名（COALESCE + CAST 实参）", checked)
}

// TestQueryCoversEverySelectedColumn 检查 row 结构体与 SQL 是一一对应的：
// SQL 里选出来的别名，必须在 mapToRow 里被读进某个字段。
//
// 理由：多选一列却不映射，等于白查；映射了却没人读，就是这个包一直在消灭的
// "死列"——momentum_kdj_9_3_j、bbands 的 middle、donchian lower、kc 的三条
// 全都是这样被查出来、在 row 里躺着、detectSignals 一行都没读的。这里不重复造
// 一套"谁被读了"的检查（Go 的编译器管不到结构体字段），但至少守住"选了就得映射"
// 这一半，代价很小。
func TestQueryCoversEverySelectedColumn(t *testing.T) {
	src, err := os.ReadFile("query.go")
	if err != nil {
		t.Fatalf("读取 query.go 失败：%v", err)
	}
	// 注释里也出现过 "x AS y" 这种示例写法，直接扫会把它们当成别名。先剥掉行注释。
	text := stripLineComments(string(src))

	// SQL 里的 `... AS <别名>`。
	aliasRe := regexp.MustCompile(`(?i)\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)`)

	aliases := map[string]bool{}
	for _, m := range aliasRe.FindAllStringSubmatch(text, -1) {
		// CAST(date AS VARCHAR) 的 VARCHAR 是类型名不是别名。
		if strings.EqualFold(m[1], "varchar") {
			continue
		}
		aliases[m[1]] = true
	}

	// mapToRow 里的 f64(m, "...") 和 str(m, "...")——date 走的是 str。
	keyRe := regexp.MustCompile(`(?:f64|str)\(m,\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\)`)
	mapped := map[string]bool{}
	for _, m := range keyRe.FindAllStringSubmatch(text, -1) {
		mapped[m[1]] = true
	}
	// close 不来自这条指标 SQL：indicators 库根本没有价格列，收盘价是从
	// market.v_daily_qfq 按 date 合并进来的（见 query.go 顶部注释），所以它的
	// closeSQL 里自然没有 "AS close"。这不是漏查，是跨库合并。
	delete(aliases, "close")
	delete(mapped, "close")
	if len(mapped) < 50 {
		t.Fatalf("只认出 %d 个字段映射，正则多半失效了", len(mapped))
	}

	for a := range aliases {
		if !mapped[a] {
			t.Errorf("SQL 里 AS %s 选出了这一列，但 mapToRow 没有把它读进 row —— 白查一趟。", a)
		}
	}
	for m := range mapped {
		if !aliases[m] {
			t.Errorf("mapToRow 读了别名 %q，但 SQL 里没有对应的 AS %s —— 恒读出 0。", m, m)
		}
	}
	t.Logf("别名 %d 个、字段映射 %d 个，双向一一对应（close 为跨库合并，不计）", len(aliases), len(mapped))
}

// stripLineComments 去掉 Go 行注释，保留原始行结构，避免拼坏字符串字面量。
func stripLineComments(s string) string {
	var b strings.Builder
	for _, line := range strings.Split(s, "\n") {
		if i := strings.Index(line, "//"); i >= 0 {
			// 跳过形如 "http://" 的行内 URL（本文件没有，此处只为保险）。
			if i+2 >= len(line) || line[i+2] != '/' {
				line = line[:i]
			}
		}
		b.WriteString(line)
		b.WriteString("\n")
	}
	return b.String()
}
