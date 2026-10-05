package financial

import (
	"strings"
	"testing"
)

// 本测试锁住财务能力指标工具的排序来源。
//
// 背景是一个被证伪的债务条目。docs/tech-debt（agent-subsystem.md:69、
// TECH-DEBT-REGISTER.md:94）把 `ORDER BY report DESC` 记为缺陷，理由是它
// 「绕过 financial/period.go 的唯一排序来源」。如果照那条记载把本查询改成
// periodOrderBy，查询会直接崩——两张表根本不是同一张：
//
//	v_income_statement / v_cash_flow_statement / v_balance_sheet
//	    有 period、period_end_ms、fiscal_year、fiscal_period
//	    → period.go 的 periodOrderBy 适用
//
//	v_financial_indicators_detail
//	    只有 thscode、report、25 个指标列，上面四列全部不存在
//	    → periodOrderBy 会 binder 报错
//
// period.go 记录的那个退化排序（period 列只取 annual/quarterly 两值、17 万行
// quarterly 全部同值，排出来是任意顺序）是前三张表的问题，不传染到本视图。
// 本视图的 report 实测全表统一为 'YYYY-N'（N 为 1–4 单字符），字典序等于
// 时间序，ORDER BY report DESC 正确。
//
// 这个测试的存在意义是**防误改**：它拦住的不是回归，是一次「照着债务登记册
// 执行修复」造成的生产事故。登记册条目已附证据改判，但代码里的护栏必须留着——
// 下一个人未必会先查库再动手。

func TestIndicatorDetailQueryOrdersByReportDesc(t *testing.T) {
	if !strings.Contains(financialIndicatorDetailQuery, "ORDER BY report DESC") {
		t.Errorf("财务能力指标查询必须按 report 倒序，第一行才是最新一期。\n实际 SQL：\n%s",
			financialIndicatorDetailQuery)
	}
}

func TestIndicatorDetailQueryDoesNotUsePeriodOrderBy(t *testing.T) {
	// v_financial_indicators_detail 没有 period / period_end_ms 列，
	// 套用 period.go 的排序会 binder 报错。债务登记册曾要求这么改，是错的。
	for _, bad := range []string{
		"ORDER BY period_end_ms",
		"ORDER BY period",
		periodOrderBy,
	} {
		if strings.Contains(financialIndicatorDetailQuery, bad) {
			t.Errorf("查询出现了 %q——本视图无 period / period_end_ms 列，"+
				"用 period.go 的排序会报错。\n实际 SQL：\n%s",
				bad, financialIndicatorDetailQuery)
		}
	}

	// 反向自证：本包三张报表的排序常量与本查询不同，且两者都不该被混用。
	if periodOrderBy == "ORDER BY report DESC" {
		t.Errorf("periodOrderBy 不应等于本查询的排序——两者服务的表不同")
	}
}

// report 列的格式与 FY 映射必须写进工具描述。
//
// 债务登记册这一条主张是成立的：AGENTS.md 把「report 是 YYYY-N、FY 必须映射
// 为 -4，否则 join 静默返回空」列为踩过坑的陷阱，而原 Description 只说了列名
// 不同（"报告期字段是 report（不是 period）"），没说格式，也没说 FY 映射。
// 模型拿不到这个信息，join 静默返回空时无从排查。
func TestIndicatorDetailDescriptionDocumentsReportFormat(t *testing.T) {
	desc := (&FinancialIndicatorDetailTool{}).Description()

	if !strings.Contains(desc, "YYYY-N") {
		t.Errorf("工具描述未说明 report 的 YYYY-N 格式")
	}
	if !strings.Contains(desc, "-4") || !strings.Contains(desc, "FY") {
		t.Errorf("工具描述未说明 FY 必须映射为 -4（否则与三表 join 静默返回空）")
	}
	// 原描述已有、且必须保留的读法警告。
	if !strings.Contains(desc, "report") {
		t.Errorf("工具描述丢失了 report 字段说明")
	}
}
