package hithink_finance

import (
	"sort"
	"strings"
	"testing"
)

// 抽取器本身的单元测试：用合成源码，不依赖其它 worker 正在改的文件。
func TestIndicatorColumnsInSource(t *testing.T) {
	cases := []struct {
		name string
		src  string
		want []string
	}{
		{
			name: "读 v_indicators_daily 的显式列算 consumed，WHERE 里的不算",
			src:  "q := `SELECT date, overlap_sma_5 AS ma5 FROM v_indicators_daily WHERE thscode = ?`",
			// date 命中 SQL 关键字表被剔掉：它是主键列不是指标列，不影响
			// "哪些指标被现成工具消费"这个判断（真实库里唯一与关键字重名的列就是 date）。
			want: []string{"overlap_sma_5"},
		},
		{
			name: "别名只算原列名",
			src:  "`SELECT momentum_rsi_14 AS rsi FROM v_indicators_daily`",
			want: []string{"momentum_rsi_14"},
		},
		{
			name: "读别的视图的列不算",
			src:  "`SELECT date, close FROM v_daily_qfq WHERE thscode = ?`",
			want: nil,
		},
		{
			name: "错位配对抓到的 Go 代码被排除",
			src:  "`json:\"x\"`\nif err := json.Unmarshal([]byte(\"SELECT a FROM b\"), &p); err != nil {\nreturn\n}",
			want: nil,
		},
		{
			name: "JOIN 另一张表也认 v_indicators_daily",
			src:  "`SELECT i.momentum_trix_15 AS trix FROM v_daily_qfq d JOIN v_indicators_daily i ON i.thscode = d.thscode`",
			want: []string{"momentum_trix_15"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := indicatorColumnsInSource(c.src)
			sort.Strings(got)
			want := append([]string{}, c.want...)
			sort.Strings(want)
			if strings.Join(got, ",") != strings.Join(want, ",") {
				t.Fatalf("got %v, want %v", got, want)
			}
		})
	}
}

// embed 必须真的覆盖到工具子包；否则 consumed 会静默全为 false。
// embed 必须真的覆盖到工具子包；否则 consumed 会静默全为 false。
func TestEmbeddedToolSourcesCoverSubpackages(t *testing.T) {
	for _, sub := range []string{"analysis", "indicator", "market", "pattern", "query", "special"} {
		entries, err := toolSourceFS.ReadDir(sub)
		if err != nil {
			t.Errorf("嵌入的源码里没有 %s/：%v", sub, err)
			continue
		}
		if len(entries) == 0 {
			t.Errorf("%s/ 嵌进来是空的", sub)
		}
	}
}

// ---------- #4 库不可达时的降级 ----------
