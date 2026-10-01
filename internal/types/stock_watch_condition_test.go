package types

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// DateOnly 的 JSON 契约是 'YYYY-MM-DD'，不是 RFC3339 —— 前端按字符串比较，
// 一个带时区的时间戳会让"同一天"变成另一个瞬间。
func TestDateOnlyJSONContract(t *testing.T) {
	d, err := ParseDateOnly("2026-10-02")
	require.NoError(t, err)

	raw, err := json.Marshal(d)
	require.NoError(t, err)
	assert.JSONEq(t, `"2026-10-02"`, string(raw))

	// 指针为 nil 时是 null，不是零值日期。
	var nilDate *DateOnly
	raw, err = json.Marshal(nilDate)
	require.NoError(t, err)
	assert.Equal(t, "null", string(raw))

	var back DateOnly
	require.NoError(t, json.Unmarshal([]byte(`"2026-10-02"`), &back))
	assert.Equal(t, "2026-10-02", back.String())

	require.NoError(t, json.Unmarshal([]byte(`null`), &back))
	assert.True(t, back.IsZero())
}

// Scan 同时接受 Postgres 的 time.Time 与 SQLite 的字符串（含带时间的形态），
// 只保留日历日。
func TestDateOnlyScanAcceptsBothDriverShapes(t *testing.T) {
	cases := []struct {
		name  string
		value any
	}{
		{"postgres DATE", time.Date(2026, 10, 2, 0, 0, 0, 0, time.UTC)},
		{"sqlite text", "2026-10-02"},
		{"sqlite datetime", "2026-10-02 00:00:00+00:00"},
		{"bytes", []byte("2026-10-02")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var d DateOnly
			require.NoError(t, d.Scan(tc.value))
			assert.Equal(t, "2026-10-02", d.String())
		})
	}

	var d DateOnly
	require.Error(t, d.Scan(42), "未知类型必须报错，不能猜一个日期")
}

// Value 返回 time.Time 而不是字符串：pgx 的 DATE 参数是二进制编码，字符串编不出来。
// 同时验证取的是"这个值自己的日历日"，不受时区换算影响。
func TestDateOnlyValueKeepsTheCalendarDay(t *testing.T) {
	var zero DateOnly
	v, err := zero.Value()
	require.NoError(t, err)
	assert.Nil(t, v, "零值日期是 NULL")

	d, err := ParseDateOnly("2026-10-02")
	require.NoError(t, err)
	v, err = d.Value()
	require.NoError(t, err)
	asTime, ok := v.(time.Time)
	require.True(t, ok, "必须是 time.Time 才能被 pgx 的 DATE 编码器接受")
	y, m, day := asTime.Date()
	assert.Equal(t, 2026, y)
	assert.Equal(t, time.October, m)
	assert.Equal(t, 2, day)
}

// 全新条件的线上形状：last_satisfied 与 last_eval_date 都是 null，而不是 false / 零日期。
func TestStockWatchConditionJSONHasNullWatermarksWhenFresh(t *testing.T) {
	cond := StockWatchCondition{
		ID: "c1", THSCode: "600519.SH", Field: "price", Op: "below", Value: 1235,
	}
	raw, err := json.Marshal(cond)
	require.NoError(t, err)
	assert.Contains(t, string(raw), `"last_satisfied":null`)
	assert.Contains(t, string(raw), `"last_eval_date":null`)

	// 已判定后：布尔与日期都是字面值。
	cond.LastSatisfied = new(true)
	d, err := ParseDateOnly("2026-10-02")
	require.NoError(t, err)
	cond.LastEvalDate = &d
	raw, err = json.Marshal(cond)
	require.NoError(t, err)
	assert.Contains(t, string(raw), `"last_satisfied":true`)
	assert.Contains(t, string(raw), `"last_eval_date":"2026-10-02"`)
}
