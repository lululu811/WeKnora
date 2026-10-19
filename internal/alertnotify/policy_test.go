package alertnotify

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func shanghai(t *testing.T) *time.Location {
	t.Helper()
	loc, err := time.LoadLocation("Asia/Shanghai")
	require.NoError(t, err)
	return loc
}

// TestPolicyChannelsAreOffUnlessEnabled pins the channel roster.
//
// 档位是「关」的渠道必须真的推不出去：MaxPerDay=0 是一道硬闸，
// 不是建议值。忘了改的后果是每天往用户手机上推几百条垃圾。
func TestPolicyChannelsAreOffUnlessEnabled(t *testing.T) {
	for _, p := range []Policy{DiaryPolicy(), ArticlePolicy()} {
		g := NewGate(p)
		d := g.Decide(time.Now(), "whatever")
		assert.False(t, d.Allow, "渠道 %s 的档位是关，不该允许推送", p.Channel)
		assert.NotEmpty(t, d.Reason, "拒绝必须说清理由")
	}
	assert.Equal(t, 1, ConditionPolicy().MaxPerDay, "条件作业每天一条汇总")
}

// TestDecideCountsOnlyRecordedDeliveries pins the difference between
// "判定通过" and "推到了用户眼前".
//
// 投递失败不该吃掉当天的额度：额度是给用户看的消息数的，不是给尝试次数的。
func TestDecideCountsOnlyRecordedDeliveries(t *testing.T) {
	g := NewGate(Policy{Channel: "t", MaxPerDay: 1})

	assert.True(t, g.Decide(time.Now(), "k1").Allow, "第一次应当允许")
	// 判定通过但没 Record（投递失败）→ 额度没被吃掉
	assert.True(t, g.Decide(time.Now(), "k2").Allow, "投递失败不该占额度")

	g.Record(time.Now(), "k2")
	d := g.Decide(time.Now(), "k3")
	assert.False(t, d.Allow, "已推过一条后应当被上限拦住")
	assert.Contains(t, d.Reason, "上限")
}

// TestDedupeWindowSuppressesTheSameKey pins dedup.
//
// 键是内容的身份，不是时间戳 —— 否则等于没去重。
func TestDedupeWindowSuppressesTheSameKey(t *testing.T) {
	base := time.Date(2026, 10, 19, 9, 0, 0, 0, time.UTC)
	g := NewGate(Policy{Channel: "t", MaxPerDay: 10, DedupeWindow: time.Hour})

	assert.True(t, g.Decide(base, "same").Allow)
	g.Record(base, "same")

	d := g.Decide(base.Add(30*time.Minute), "same")
	assert.False(t, d.Allow, "窗口内的同一条不该再推")
	assert.Contains(t, d.Reason, "去重窗口")

	assert.True(t, g.Decide(base.Add(90*time.Minute), "same").Allow, "窗口过了就该能推")
	assert.True(t, g.Decide(base, "other").Allow, "不同的键不受影响")
}

// TestQuietHoursSuppressesAcrossMidnight pins the quiet window.
//
// 静默时段是「每天 23:00 到次日 08:00」这种循环，跨午夜必须成立 ——
// 只判「同一自然日内的区间」会让夜里的后半天照推不误。
func TestQuietHoursSuppressesAcrossMidnight(t *testing.T) {
	loc := shanghai(t)
	quiet := QuietHours{
		Enabled: true,
		Start:   23 * time.Hour,
		End:     8 * time.Hour,
		Loc:     loc,
	}

	assert.True(t, quiet.Contains(time.Date(2026, 10, 19, 23, 30, 0, 0, loc)), "23:30 在静默内")
	assert.True(t, quiet.Contains(time.Date(2026, 10, 19, 7, 59, 0, 0, loc)), "次日 07:59 仍在静默内")
	assert.False(t, quiet.Contains(time.Date(2026, 10, 19, 8, 0, 0, 0, loc)), "08:00 静默结束")
	assert.False(t, quiet.Contains(time.Date(2026, 10, 19, 12, 0, 0, 0, loc)), "中午不在静默内")
	assert.True(t, quiet.Contains(time.Date(2026, 10, 19, 3, 0, 0, 0, loc)), "凌晨也在静默内")
}

// TestQuietHoursDisabledIsInert pins the off switch.
func TestQuietHoursDisabledIsInert(t *testing.T) {
	q := QuietHours{Start: 23 * time.Hour, End: 8 * time.Hour}
	assert.False(t, q.Contains(time.Date(2026, 10, 19, 23, 30, 0, 0, time.UTC)))
}

// TestDailyCapResetsOnTheNextDay pins the day rollover.
//
// 频率上限是「每天」，不是「永久」。跨日之后额度要回来，否则第二天起
// 条件作业就再也推不出东西了。
func TestDailyCapResetsOnTheNextDay(t *testing.T) {
	g := NewGate(Policy{Channel: "t", MaxPerDay: 1})
	day1 := time.Date(2026, 10, 19, 9, 0, 0, 0, time.UTC)

	assert.True(t, g.Decide(day1, "k1").Allow)
	g.Record(day1, "k1")
	assert.False(t, g.Decide(day1.Add(time.Hour), "k2").Allow, "当天第二条应被拦")

	assert.True(t, g.Decide(day1.AddDate(0, 0, 1), "k3").Allow, "次日额度应当回来")
}

// TestGateIsConcurrencySafe pins the lock: 作业与手动触发可能并发。
func TestGateIsConcurrencySafe(t *testing.T) {
	g := NewGate(Policy{Channel: "t", MaxPerDay: 1})
	now := time.Now()

	done := make(chan struct{})
	for i := 0; i < 8; i++ {
		go func() {
			defer func() { done <- struct{}{} }()
			if g.Decide(now, "k").Allow {
				g.Record(now, "k")
			}
		}()
	}
	for i := 0; i < 8; i++ {
		<-done
	}
	// 8 个并发里最多 1 个拿到额度。
	d := g.Decide(now, "k2")
	assert.False(t, d.Allow, "并发下额度不能被超发")
}
