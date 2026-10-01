package financial

import (
	"strings"
	"testing"
)

// 本测试锁住三张报表的时间序与报告期文案。
//
// 这两件事曾经同时是错的，且是同一个根因：把 period 列当成了报告期。
// period 取值只有 annual / quarterly（口径），不是期别；真实期别在
// fiscal_year + fiscal_period。于是 `ORDER BY period DESC` 排的是一个两值的
// 字符串列，17 万行 quarterly 同值，排序退化成任意顺序——`periods=4` 返回
// 的是任意 4 期而不是最近 4 期。三个工具的 Description 同样告诉模型
// 「报告期字段是 period（形如 2026Q2）」，模型据此推理必然出错。
//
// 排序错误对单期查询（periods=1）不显形，所以一直没被发现，直到下游要做
// 成长性同比这类依赖时间序列的分析。修一次容易，忘了就会退回原状——
// 这些断言就是防退回的。

func TestStatementQueriesOrderByPeriodEnd(t *testing.T) {
	queries := map[string]string{
		"balanceSheetQuery":    balanceSheetQuery,
		"incomeStatementQuery": incomeStatementQuery,
		"cashFlowQuery":        cashFlowQuery,
	}

	for name, q := range queries {
		t.Run(name, func(t *testing.T) {
			if !strings.Contains(q, "ORDER BY period_end_ms DESC, period ASC") {
				t.Errorf("%s 必须按报告期末日期倒序。\n实际 SQL：\n%s", name, q)
			}
			// 精确匹配带空格的坏形式，避免误伤 "ORDER BY period_end_ms"。
			if strings.Contains(q, "ORDER BY period DESC") {
				t.Errorf("%s 退回按 period 列排序了——那是报表口径不是报告期，"+
					"会把 periods=N 变成随机 N 期。\n实际 SQL：\n%s", name, q)
			}
			// period_end_ms 必须在 SELECT 里：模型要能直接读出期末日期，
			// 不该被要求拿 fiscal_year + fiscal_period 自己拼。
			if !strings.Contains(q, "period_end_ms") {
				t.Errorf("%s 的 SELECT 未返回 period_end_ms", name)
			}
		})
	}
}

// 三张表都有 period_end_ms，缺一说明 schema 漂移或视图改名了。
func TestPeriodOrderDependsOnRealColumn(t *testing.T) {
	if !strings.Contains(periodOrderBy, "period_end_ms") {
		t.Fatalf("periodOrderBy 必须以 period_end_ms 为排序基准，当前：%s", periodOrderBy)
	}
}

func TestStatementDescriptionsExplainPeriodCorrectly(t *testing.T) {
	descs := map[string]func() string{
		"balanceSheetDesc":    NewBalanceSheetTool(nil).Description,
		"incomeStatementDesc": NewIncomeStatementTool(nil).Description,
		"cashFlowDesc":        NewCashFlowStatementTool(nil).Description,
	}

	// 旧文案的错法：告诉模型 period 形如 2026Q2。
	// 实际 period 是 annual/quarterly，模型照着理解会把年度和季度混为一谈。
	wrong := "period（形如 2026Q2"

	for name, fn := range descs {
		t.Run(name, func(t *testing.T) {
			d := fn()
			if strings.Contains(d, wrong) {
				t.Errorf("%s 仍在声称 period 形如 2026Q2，但该列只有 annual/quarterly。"+
					"模型会据此误读报告期。", name)
			}
			// 必须明确告诉模型期别看哪两列，否则它无从判断期别。
			if !strings.Contains(d, "fiscal_year") || !strings.Contains(d, "fiscal_period") {
				t.Errorf("%s 未说明报告期应读 fiscal_year + fiscal_period", name)
			}
			if !strings.Contains(d, "period_end_ms") {
				t.Errorf("%s 未说明 period_end_ms 是报告期末日期", name)
			}
		})
	}
}

// 年报与四季报是同一报告期的两种口径（FY 与 Q4 的 period_end_ms 实测一致，
// 均为年末日）。返回里若同时出现，模型需要知道以哪个为准，否则会当成两个
// 不同报告期、或误以为年报是第四季度的增量数据。
func TestDescriptionsDisambiguateAnnualVsQ4(t *testing.T) {
	for name, d := range map[string]string{
		"balanceSheetDesc":    balanceSheetDesc,
		"incomeStatementDesc": incomeStatementDesc,
		"cashFlowDesc":        cashFlowDesc,
	} {
		if !strings.Contains(d, "年报") || !strings.Contains(d, "FY") {
			t.Errorf("%s 未交代 FY（年报）与 Q4 是同一报告期的两种口径", name)
		}
	}
}
