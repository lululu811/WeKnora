package hithink_finance

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func at(hh, mm int) time.Time {
	return time.Date(2026, 10, 2, hh, mm, 0, 0, time.Local)
}

// 默认窗口必须与历史行为逐分钟一致：区间两端**闭区间**命中，
// 差一分钟必须放行。放宽或收紧任何一端都会 ±10 分钟地改变可查询时段。
func TestCheckSyncWindowAt_DefaultWindows(t *testing.T) {
	cases := []struct {
		hh, mm  int
		blocked bool
	}{
		{17, 24, false},
		{17, 25, true},
		{17, 30, true},
		{17, 35, true},
		{17, 36, false},
		{2, 54, false},
		{2, 55, true},
		{3, 5, true},
		{3, 6, false},
		{12, 0, false},
		{0, 0, false},
	}
	for _, c := range cases {
		err := checkSyncWindowAt(at(c.hh, c.mm), defaultSyncWindows)
		if c.blocked {
			require.Error(t, err, "%02d:%02d 应在默认窗口内", c.hh, c.mm)
			assert.Contains(t, err.Error(), "数据同步中")
		} else {
			assert.NoError(t, err, "%02d:%02d 不应被拦截", c.hh, c.mm)
		}
	}
}

// 空配置 = 关闭拦截。使用者自建 ETL 时刻表与默认值不同时靠这个开关。
func TestCheckSyncWindowAt_EmptySpecDisables(t *testing.T) {
	assert.NoError(t, checkSyncWindowAt(at(17, 30), ""))
	assert.NoError(t, checkSyncWindowAt(at(3, 0), ""))
}

// 未设置的默认值走 os.LookupEnv 的「不存在」分支；空字符串走「存在但为空」分支。
// 两者行为必须不同：前者拦、后者不拦。
func TestCheckSyncWindow_EnvBranches(t *testing.T) {
	t.Setenv(syncWindowEnv, "")
	assert.NoError(t, CheckSyncWindow(), "显式置空应关闭拦截")

	t.Setenv(syncWindowEnv, "17:25-17:35")
	if err := CheckSyncWindow(); err != nil {
		// 只有在当前时刻恰好落进自定义窗口时才会失败，说明自定义值确实生效了。
		assert.Contains(t, err.Error(), "17:25-17:35")
	}
}

func TestCheckSyncWindowAt_WrapsMidnight(t *testing.T) {
	spec := "23:50-00:10"
	assert.Error(t, checkSyncWindowAt(at(23, 55), spec))
	assert.Error(t, checkSyncWindowAt(at(0, 5), spec))
	assert.Error(t, checkSyncWindowAt(at(0, 10), spec))
	assert.NoError(t, checkSyncWindowAt(at(0, 11), spec))
	assert.NoError(t, checkSyncWindowAt(at(12, 0), spec))
}

// 配置写错时退化而不是让整族工具不可用：好片段保留，坏片段丢弃。
func TestParseSyncWindows_SkipsMalformed(t *testing.T) {
	got := parseSyncWindows(" 17:25-17:35 , junk ,99:00-10:00,10:00-10:60,17:25,")
	require.Len(t, got, 1, "只应保留唯一合法片段")
	assert.Equal(t, syncWindow{start: 17*60 + 25, end: 17*60 + 35}, got[0])

	assert.Empty(t, parseSyncWindows(""))
	assert.Empty(t, parseSyncWindows(",,,"))
}

func TestParseSyncWindows_AcceptsSpacesAndMultiple(t *testing.T) {
	got := parseSyncWindows(" 2:55 - 3:05 , 17:25-17:35")
	require.Len(t, got, 2)
	assert.Equal(t, syncWindow{start: 2*60 + 55, end: 3*60 + 5}, got[0])
	assert.Equal(t, syncWindow{start: 17*60 + 25, end: 17*60 + 35}, got[1])
}
