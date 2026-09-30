package analysis

import (
	"strings"
	"testing"
)

// buildDataQuality 的断言重点：缺数据必须**说出来**。
// 上游 toF64 把 NULL 映射成 0，0 在 ADX/RSI/均线里都是合法读数，
// 所以这个函数是"缺失有没有被如实报告"的最后一道闸门。
func TestBuildDataQualityFlagsMissingIndicators(t *testing.T) {
	// 60 根 K 线，一根指标都没有 —— 最常见的退化形态
	rows := make([]marketRow, 60)
	for i := range rows {
		rows[i] = marketRow{Date: "2026-09-01", Close: 10, OHLCValid: true}
	}
	dq := buildDataQuality(rows, rows[0])

	if dq["degraded"] != true {
		t.Fatal("60 根 K 线零指标，必须标记为 degraded")
	}
	if dq["bars"] != 60 {
		t.Errorf("bars = %v，期望 60", dq["bars"])
	}
	if dq["indicator_bars"] != 0 {
		t.Errorf("indicator_bars = %v，期望 0", dq["indicator_bars"])
	}
	if dq["indicator_coverage"] != float64(0) {
		t.Errorf("indicator_coverage = %v，期望 0", dq["indicator_coverage"])
	}
	notes, _ := dq["notes"].([]string)
	joined := strings.Join(notes, " ")
	if !strings.Contains(joined, "不可采信") {
		t.Errorf("notes 没有说清不可采信：%v", notes)
	}
}

func TestBuildDataQualityPartialCoverage(t *testing.T) {
	rows := make([]marketRow, 10)
	for i := range rows {
		rows[i] = marketRow{Date: "2026-09-01", Close: 10, OHLCValid: true}
		// 只有前 3 行有真实指标值
		if i < 3 {
			rows[i].IndicatorValid = true
			rows[i].MA5 = 10.5
		}
	}
	dq := buildDataQuality(rows, rows[0])
	if dq["indicator_bars"] != 3 {
		t.Errorf("indicator_bars = %v，期望 3", dq["indicator_bars"])
	}
	notes, _ := dq["notes"].([]string)
	joined := strings.Join(notes, " ")
	if !strings.Contains(joined, "3/10") {
		t.Errorf("notes 应写明覆盖 3/10：%v", notes)
	}
}

func TestBuildDataQualityFullCoverageNotDegraded(t *testing.T) {
	rows := make([]marketRow, 120)
	for i := range rows {
		rows[i] = marketRow{Date: "2026-09-01", Close: 10, OHLCValid: true,
			IndicatorValid: true, MA5: 10.5, RSI14: 55}
	}
	dq := buildDataQuality(rows, rows[0])
	if dq["degraded"] != false {
		t.Fatalf("120 根 K 线全指标齐备，不该报 degraded：%v", dq["notes"])
	}
	if dq["indicator_coverage"] != float64(1) {
		t.Errorf("indicator_coverage = %v，期望 1", dq["indicator_coverage"])
	}
	// 但 60 根以下仍然要提示：MA60 算不出来
	short := make([]marketRow, 40)
	for i := range short {
		short[i] = marketRow{Date: "2026-09-01", Close: 10, OHLCValid: true,
			IndicatorValid: true, MA5: 10.5}
	}
	dq2 := buildDataQuality(short, short[0])
	if dq2["degraded"] != true {
		t.Error("40 根 K 线应报 degraded：MA60 不成立")
	}
}

// 行数 0 不能返回 NaN 或除零。
func TestBuildDataQualityEmptyRows(t *testing.T) {
	var rows []marketRow
	dq := buildDataQuality(rows, marketRow{})
	if dq["bars"] != 0 {
		t.Errorf("bars = %v，期望 0", dq["bars"])
	}
	if c, ok := dq["indicator_coverage"].(float64); !ok || c != 0 {
		t.Errorf("空输入的覆盖率应是 0，实际 %v", dq["indicator_coverage"])
	}
}

// IndicatorValid 为 true 但所有指标字段都是 0 —— 这种情况仍然算缺失。
// 这正是"上游注了 COALESCE(col,0)"会造成的形态，必须能识破。
func TestBuildDataQualityDetectsCoalescedZero(t *testing.T) {
	rows := []marketRow{{
		Date: "2026-09-01", Close: 10, OHLCValid: true,
		IndicatorValid: true,                                  // 上游声称有效
		MA5:            0, MA10: 0, MA20: 0, RSI14: 0, ADX: 0, // 但值全是 0
	}}
	dq := buildDataQuality(rows, rows[0])
	if dq["indicator_bars"] != 0 {
		t.Errorf("全 0 的指标行不该被算作有效，indicator_bars = %v 期望 0", dq["indicator_bars"])
	}
	if dq["degraded"] != true {
		t.Error("全 0 指标必须报 degraded")
	}
}

func TestBuildDataQualityFlagsIncompleteLatestBar(t *testing.T) {
	rows := []marketRow{{Date: "2026-09-01", Close: 10, OHLCValid: false}}
	dq := buildDataQuality(rows, rows[0])
	if dq["latest_ohlc_complete"] != false {
		t.Error("最新 K 线价格不完整时该标志为 false")
	}
	notes, _ := dq["notes"].([]string)
	joined := strings.Join(notes, " ")
	if !strings.Contains(joined, "价格字段不完整") {
		t.Errorf("notes 应写明价格字段不完整：%v", notes)
	}
}

func TestAnyIndicatorValue(t *testing.T) {
	if (marketRow{}).AnyIndicatorValue() {
		t.Error("全零行不应报告有指标值")
	}
	if !(marketRow{MA20: 10.5}).AnyIndicatorValue() {
		t.Error("有非零指标的行应报告有指标值")
	}
	if !(marketRow{ADX: 30}).AnyIndicatorValue() {
		t.Error("只有 ADX 非零也算有指标值")
	}
}
