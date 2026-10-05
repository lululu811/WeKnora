package query

import (
	"strings"
	"testing"
)

// 本测试锁住工具描述里的排序范例。
//
// financial/period_test.go 已经把三张报表的 SQL 排序锁住了，但那里锁不到
// 本文件：sql_query.go 的 Description() 是模型唯一能看到的 schema 目录，它在
// 生成目录的**尾部**手写了两条 financials 趋势范例，而那两条范例当年跟着
// period.go 一起修过——只有三张报表的 SQL 改了，描述里的范例没跟着改，于是
// 退化排序原封不动地继续教给模型。
//
// 这不是理论风险。实测 600519.SH 照抄修复前那条范例：
//
//	ORDER BY period DESC LIMIT 8
//	→ 2026Q2, 2025Q4, 2025Q3, 2024Q4, 2026Q1, 2025Q1, 2025Q2, 2024Q3
//
// 既非升序也非降序，且漏掉最近的 2025FY、混入两个 2024 年季度。模型拿这条
// 序列去做「利润率趋势」判断，读到的是乱序样本。
//
// 修一次容易，忘了就会退回原状——这些断言就是防退回的。

// barePeriodOrder 是坏形式。精确带空格，避免误伤 "ORDER BY period_end_ms"。
const barePeriodOrder = "ORDER BY period DESC"

const correctPeriodOrder = "ORDER BY period_end_ms DESC, period ASC"

func TestDescriptionHasNoDegeneratePeriodOrder(t *testing.T) {
	desc := (&SQLQueryTool{}).Description()

	if strings.Contains(desc, barePeriodOrder) {
		t.Errorf("工具描述里出现了 %s。\n"+
			"period 列只取 annual / quarterly 两个值，financials 库里 17 万行\n"+
			"quarterly 全部同值，按它排会退化成任意顺序，模型照抄就会拿到乱序趋势。\n"+
			"跨期查询只能写 %s。", barePeriodOrder, correctPeriodOrder)
	}
}

func TestDescriptionStatesThePeriodOrderingRule(t *testing.T) {
	desc := (&SQLQueryTool{}).Description()

	// 规则本身必须在描述里，否则模型只知道「别这么写」却不知道「该怎么写」。
	if !strings.Contains(desc, correctPeriodOrder) {
		t.Errorf("工具描述未给出正确的跨期排序写法 %s", correctPeriodOrder)
	}

	// 报告期读法：period 是口径不是期别。这条是 period.go 里 periodFieldDoc
	// 的同一条认知，描述层必须自带，不能指望模型读过 Go 源码。
	if !strings.Contains(desc, "fiscal_year") || !strings.Contains(desc, "fiscal_period") {
		t.Errorf("工具描述未告知模型报告期应读 fiscal_year + fiscal_period")
	}
}

// 两条趋势范例必须选到报告期列，且排序正确。
//
// 只断言全文字符串会太松：模型可能把正确排序写在别处、范例却仍是坏的。
// 这里逐条锁死「范例自身」的排序。
func TestFinancialTrendExamplesAreOrdered(t *testing.T) {
	desc := (&SQLQueryTool{}).Description()

	examples := []struct {
		name   string
		marker string
	}{
		{"利润率趋势", "v_income_statement"},
		{"现金流质量", "v_cash_flow_statement"},
	}

	for _, ex := range examples {
		t.Run(ex.name, func(t *testing.T) {
			line := findExampleLine(desc, ex.marker)
			if line == "" {
				t.Fatalf("描述里找不到引用 %s 的范例行", ex.marker)
			}
			if !strings.Contains(line, correctPeriodOrder) {
				t.Errorf("范例「%s」未使用 %s。\n实际范例：\n%s",
					ex.name, correctPeriodOrder, line)
			}
			if strings.Contains(line, barePeriodOrder) {
				t.Errorf("范例「%s」退回了 %s。\n实际范例：\n%s",
					ex.name, barePeriodOrder, line)
			}
			// 趋势查询要能读出期别，否则模型拿到一串无标签的数字。
			if !strings.Contains(line, "fiscal_year") {
				t.Errorf("范例「%s」未 SELECT 报告期列，模型无法识别期别。\n实际范例：\n%s",
					ex.name, line)
			}
		})
	}
}

// findExampleLine 返回引用 table 的那条**使用范例**行。
//
// 必须额外要求行内含 `sql="SELECT`：生成目录里的表清单也是 "- " 开头且同样
// 出现表名（形如 `- v_income_statement(thscode, period, …) — 利润表`），
// 只按表名匹配会抓到目录行而不是范例行。
func findExampleLine(desc, table string) string {
	for _, line := range strings.Split(desc, "\n") {
		if !strings.Contains(line, table) || !strings.HasPrefix(strings.TrimSpace(line), "- ") {
			continue
		}
		if strings.Contains(line, `sql="SELECT`) {
			return line
		}
	}
	return ""
}
