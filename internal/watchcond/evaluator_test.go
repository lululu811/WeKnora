package watchcond

import (
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/types"
)

func dateOnly(t *testing.T, s string) *types.DateOnly {
	t.Helper()
	d, err := types.ParseDateOnly(s)
	require.NoError(t, err)
	return &d
}

// condition builds a stored-looking condition; last == nil means "never
// evaluated".
func condition(field, op string, value float64, last *bool, evalDate *types.DateOnly) *types.StockWatchCondition {
	return &types.StockWatchCondition{
		ID:            "cond-1",
		THSCode:       "600519.SH",
		Field:         field,
		Op:            op,
		Value:         value,
		LastSatisfied: last,
		LastEvalDate:  evalDate,
	}
}

// 每个字段的算术都在这里钉住：读数从 quotes 的哪个字段来、怎么比。
func TestEvaluateFieldArithmetic(t *testing.T) {
	readings := map[string]Reading{
		"600519.SH": {
			Name:        "贵州茅台",
			Date:        "2026-10-02",
			Close:       new(1200.0),
			ChangePct:   new(-3.2),
			VolumeRatio: new(2.31),
			MA20:        new(1000.0),
		},
	}
	cases := []struct {
		name      string
		field     string
		op        string
		value     float64
		satisfied bool
	}{
		{"price below, reading under the line", types.StockWatchConditionFieldPrice, "below", 1235, true},
		{"price below, reading over the line", types.StockWatchConditionFieldPrice, "below", 1100, false},
		{"price above, reading over the line", types.StockWatchConditionFieldPrice, "above", 1235, false},
		{"price above, reading under the line", types.StockWatchConditionFieldPrice, "above", 1100, true},
		{"pct_change below: -3.2 < -3.0", types.StockWatchConditionFieldPctChange, "below", -3.0, true},
		{"pct_change above: -3.2 > -2.0 为假", types.StockWatchConditionFieldPctChange, "above", -2.0, false},
		{"volume_ratio above", types.StockWatchConditionFieldVolumeRatio, "above", 2.0, true},
		{"volume_ratio below", types.StockWatchConditionFieldVolumeRatio, "below", 2.0, false},
		// close_vs_ma20 = (1200/1000 - 1) * 100 = 20
		{"close_vs_ma20 derivation", types.StockWatchConditionFieldCloseVsMA20, "above", 15, true},
		{"close_vs_ma20 below", types.StockWatchConditionFieldCloseVsMA20, "below", 25, true},
		{"strict inequality: equal is not above", types.StockWatchConditionFieldPrice, "above", 1200, false},
		{"strict inequality: equal is not below", types.StockWatchConditionFieldPrice, "below", 1200, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cond := condition(tc.field, tc.op, tc.value, nil, nil)
			out := Evaluate([]*types.StockWatchCondition{cond}, readings)
			require.Len(t, out, 1, "可判定的条件必须出现在结果里")
			assert.Equal(t, tc.satisfied, out[0].Satisfied)
			assert.False(t, out[0].Fired, "首次评估（last_satisfied=nil）只记录、不触发")
		})
	}
}

// 读数缺失/为 null 是"无法判定"，不是 false：整条条件从结果里消失，
// 调用方因而不会动 last_satisfied、也不会推进水位。
func TestEvaluateUndecidableWhenReadingIsNull(t *testing.T) {
	cases := []struct {
		name    string
		field   string
		reading *Reading
	}{
		{"symbol absent from the quote response", types.StockWatchConditionFieldPrice, nil},
		{"close is null (no local data)", types.StockWatchConditionFieldPrice, &Reading{Date: "2026-10-02"}},
		{"date is missing: cannot anchor the watermark", types.StockWatchConditionFieldPrice, &Reading{Close: new(1200.0)}},
		{"change_pct is null", types.StockWatchConditionFieldPctChange, &Reading{Date: "2026-10-02", Close: new(1200.0)}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			readings := map[string]Reading{}
			if tc.reading != nil {
				readings["600519.SH"] = *tc.reading
			}
			cond := condition(tc.field, "below", 1235, nil, nil)
			assert.Empty(t, Evaluate([]*types.StockWatchCondition{cond}, readings))
		})
	}
}

// ma20 为 null 或 0 都判不了 close_vs_ma20 —— 0 会把除法变成 +Inf，而不是"离均线很远"。
func TestEvaluateCloseVsMA20NeedsAUsableMA(t *testing.T) {
	readings := map[string]Reading{
		"600519.SH": {Date: "2026-10-02", Close: new(1200.0), MA20: nil},
	}
	cond := condition(types.StockWatchConditionFieldCloseVsMA20, "above", 1, nil, nil)
	assert.Empty(t, Evaluate([]*types.StockWatchCondition{cond}, readings))

	readings["600519.SH"] = Reading{Date: "2026-10-02", Close: new(1200.0), MA20: new(0.0)}
	assert.Empty(t, Evaluate([]*types.StockWatchCondition{cond}, readings), "ma20=0 不能算出 +Inf")
}

// 触发只认严格的 0→1 边沿。
func TestEvaluateFiresOnlyOnTheStrictEdge(t *testing.T) {
	readings := map[string]Reading{
		"600519.SH": {Date: "2026-10-02", Close: new(1200.0)},
	}
	cases := []struct {
		name       string
		last       *bool
		satisfied  bool // the reading's verdict for the threshold chosen below
		wantFired  bool
		wantRecord bool
	}{
		// "below 1235" → 1200 < 1235 → satisfied=true; "below 1100" → false.
		{"first ever evaluation records without firing", nil, true, false, true},
		{"false -> true fires", new(false), true, true, true},
		{"true -> true does not fire", new(true), true, false, true},
		{"false -> false does not fire", new(false), false, false, true},
		{"true -> false does not fire", new(true), false, false, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			value := 1235.0
			if !tc.satisfied {
				value = 1100 // 1200 > 1100, so "below 1100" is false
			}
			cond := condition(types.StockWatchConditionFieldPrice, "below", value, tc.last, nil)
			out := Evaluate([]*types.StockWatchCondition{cond}, readings)
			require.Len(t, out, 1)
			assert.Equal(t, tc.wantFired, out[0].Fired)
			assert.Equal(t, tc.wantRecord, true)
			assert.Equal(t, tc.satisfied, out[0].Satisfied, "无论是否触发，都要记录新状态")
			if tc.wantFired {
				assert.NotEmpty(t, out[0].Note, "触发必须带上人话说明")
			} else {
				assert.Empty(t, out[0].Note)
			}
		})
	}
}

// 水位 = 交易日：同一天不重评，只在新交易日才评。
func TestEvaluateWatermarkSkipsAlreadyDecidedTradingDay(t *testing.T) {
	readings := map[string]Reading{
		"600519.SH": {Date: "2026-10-02", Close: new(1200.0)},
	}
	t.Run("same day is skipped", func(t *testing.T) {
		cond := condition(types.StockWatchConditionFieldPrice, "below", 1235, new(false), dateOnly(t, "2026-10-02"))
		assert.Empty(t, Evaluate([]*types.StockWatchCondition{cond}, readings))
	})
	t.Run("ahead of the reading is skipped (ETL outage / holiday)", func(t *testing.T) {
		cond := condition(types.StockWatchConditionFieldPrice, "below", 1235, new(false), dateOnly(t, "2026-10-03"))
		assert.Empty(t, Evaluate([]*types.StockWatchCondition{cond}, readings))
	})
	t.Run("older date is evaluated", func(t *testing.T) {
		cond := condition(types.StockWatchConditionFieldPrice, "below", 1235, new(false), dateOnly(t, "2026-10-01"))
		out := Evaluate([]*types.StockWatchCondition{cond}, readings)
		require.Len(t, out, 1)
		assert.True(t, out[0].Fired, "新交易日上的 0→1 必须触发")
		assert.Equal(t, "2026-10-02", out[0].EvalDate)
	})
}

func TestDescribeCoversEveryFieldAndOp(t *testing.T) {
	cases := []struct {
		field, op, value, want string
	}{
		{types.StockWatchConditionFieldPrice, "below", "1235", "价格跌破 1235"},
		{types.StockWatchConditionFieldPrice, "above", "1235", "价格升破 1235"},
		{types.StockWatchConditionFieldPctChange, "below", "-3", "涨跌幅低于 -3%"},
		{types.StockWatchConditionFieldPctChange, "above", "2.5", "涨跌幅高于 2.5%"},
		{types.StockWatchConditionFieldVolumeRatio, "above", "2", "量比高于 2"},
		{types.StockWatchConditionFieldVolumeRatio, "below", "0.5", "量比低于 0.5"},
		{types.StockWatchConditionFieldCloseVsMA20, "above", "3", "距 MA20 高于 3%"},
		{types.StockWatchConditionFieldCloseVsMA20, "below", "3", "距 MA20 低于 3%"},
	}
	for _, tc := range cases {
		cond := &types.StockWatchCondition{Field: tc.field, Op: tc.op, Value: parseFloat(t, tc.value)}
		assert.Equal(t, tc.want, Describe(cond))
	}
}

func parseFloat(t *testing.T, s string) float64 {
	t.Helper()
	v, err := strconv.ParseFloat(s, 64)
	require.NoError(t, err)
	return v
}

// 聚合：一次运行一条消息，按票分组，含现值。
func TestFormatAlertAggregatesOneMessage(t *testing.T) {
	readings := map[string]Reading{
		"600519.SH": {Name: "贵州茅台", Date: "2026-10-02", Close: new(1204.53)},
		"000001.SZ": {Name: "平安银行", Date: "2026-10-02", VolumeRatio: new(2.31)},
	}
	transitions := []types.StockWatchConditionTransition{
		{
			THSCode: "600519.SH", Field: types.StockWatchConditionFieldPrice,
			Reading: 1204.53, EvalDate: "2026-10-02", Fired: true, Note: "价格跌破 1235",
		},
		{
			THSCode: "000001.SZ", Field: types.StockWatchConditionFieldVolumeRatio,
			Reading: 2.31, EvalDate: "2026-10-02", Fired: true, Note: "量比高于 2",
		},
		// Not fired → must not appear.
		{THSCode: "600519.SH", Field: types.StockWatchConditionFieldPctChange, Fired: false},
	}
	msg := FormatAlert("2026-10-02", readings, transitions)
	assert.Contains(t, msg, "【个股条件提醒】2026-10-02")
	assert.Contains(t, msg, "600519.SH 贵州茅台")
	assert.Contains(t, msg, "价格跌破 1235（现值 1204.53）")
	assert.Contains(t, msg, "000001.SZ 平安银行")
	assert.Contains(t, msg, "量比高于 2（现值 2.31）")
	assert.NotContains(t, msg, "涨跌幅")
	assert.Equal(t, 1, strings.Count(msg, "【个股条件提醒】"), "整轮只有一条消息")
}

func TestFormatAlertEmptyWhenNothingFired(t *testing.T) {
	assert.Empty(t, FormatAlert("2026-10-02", nil, nil))
	assert.Empty(t, FormatAlert("2026-10-02", nil, []types.StockWatchConditionTransition{{Fired: false}}))
}
