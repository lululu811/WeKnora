package hithink_finance

import (
	"reflect"
	"testing"
)

// 溯源注入的断言重点：表名要抽对、不能把 SQL 关键字当表名、
// 且**空结果不能凭空造出一行**（那会变成"有数据"）。
func TestExtractTables(t *testing.T) {
	cases := []struct {
		name string
		sql  string
		want []string
	}{
		{
			name: "单表",
			sql:  "SELECT snapshot_date, pe_ttm FROM v_valuation_latest WHERE thscode = ?",
			want: []string{"v_valuation_latest"},
		},
		{
			name: "JOIN 两表",
			sql: `SELECT u.name, c.thscode
			      FROM v_index_constituents c
			      JOIN v_index_universe u ON u.thscode = c.index_thscode
			      WHERE c.thscode = ?`,
			want: []string{"v_index_constituents", "v_index_universe"},
		},
		{
			name: "同一表出现多次只报一次",
			sql:  "SELECT a FROM t1 JOIN t2 ON t2.x = t1.x JOIN t1 AS other ON other.y = t2.y",
			want: []string{"t1", "t2"},
		},
		{
			name: "FROM 后面是子查询时不把关键字当表名",
			sql:  "SELECT * FROM (SELECT x FROM v_daily_qfq LIMIT 10) sub WHERE x > 0",
			want: []string{"v_daily_qfq"},
		},
		{
			name: "INNER JOIN 后的表名",
			sql:  "SELECT x FROM a INNER JOIN b ON a.id = b.id",
			want: []string{"a", "b"},
		},
		{
			name: "无 FROM 的语句返回空",
			sql:  "SELECT 1",
			want: []string{},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ExtractTables(c.sql)
			if len(got) == 0 && len(c.want) == 0 {
				return
			}
			if !reflect.DeepEqual(got, c.want) {
				t.Fatalf("ExtractTables(%q)\n  got  %v\n  want %v", c.sql, got, c.want)
			}
		})
	}
}

// 溯源必须附在行内：挂到行外的话 LLM 看不到，"有溯源"就等于没有。
func TestInjectProvenanceAttachesToRow(t *testing.T) {
	rows := []map[string]interface{}{
		{"thscode": "600519.SH", "pe_ttm": 18.99},
	}
	injectProvenance(rows, "financials", "SELECT pe_ttm FROM v_valuation_latest WHERE thscode = ?")

	src, ok := rows[0]["_source"].(map[string]interface{})
	if !ok {
		t.Fatalf("结果行上没有 _source，溯源丢了：%v", rows[0])
	}
	if src["db"] != "financials" {
		t.Errorf("db = %v，期望 financials", src["db"])
	}
	tables, ok := src["tables"].([]string)
	if !ok || len(tables) != 1 || tables[0] != "v_valuation_latest" {
		t.Errorf("tables = %v，期望 [v_valuation_latest]", src["tables"])
	}
	if _, exists := rows[0]["pe_ttm"]; !exists {
		t.Error("注入溯源时把原有业务字段弄丢了")
	}
}

// 空结果集绝不能被补上一行——那会让"查不到"看起来像"查到了"。
func TestInjectProvenanceOnEmptyRows(t *testing.T) {
	rows := []map[string]interface{}{}
	injectProvenance(rows, "special", "SELECT * FROM v_limit_up_pool")
	if len(rows) != 0 {
		t.Fatalf("空结果集被改成了 %d 行", len(rows))
	}
}

func TestInjectProvenanceEveryRow(t *testing.T) {
	rows := []map[string]interface{}{{"a": 1}, {"a": 2}, {"a": 3}}
	injectProvenance(rows, "market", "SELECT a FROM v_daily_qfq")
	for i, r := range rows {
		if _, ok := r["_source"]; !ok {
			t.Fatalf("第 %d 行没有 _source", i)
		}
	}
}
