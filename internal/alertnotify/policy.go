package alertnotify

import (
	"fmt"
	"sync"
	"time"
)

// Policy 决定「这一次推送该不该发」。
//
// 规则集中在这里，而不是散在各消费者里。三个消费者 —— 条件作业、观察日记、
// 公众号稿 —— 面对的是同一类问题：什么值得打扰用户一次。各写一套的代价是
// 频率上限、去重、静默时段这些规则会各自漂移，而且漂移的方向通常是
// 「越来越爱推」：每接一个新消费者，就多一条不受管的路。
//
// 四条规则：
//
//	渠道      每个消费者一个渠道。日记的档位是**关**，不是「还没接」——
//	          两者在代码里长得一样，但含义完全不同：关是有理由的决定，
//	          没接是还没来得及做。
//	频率上限  同一渠道每天最多几条（0 = 不推）。
//	去重      同一条内容在这个窗口内不重复推。
//	静默时段  落在这个时段里的不推。
//
// 判定与投递是分开的：Policy 回答「该不该」，notifier 只管「发不发得出去」。
// 投递失败是网络问题，判定不通过是策略问题，两者在日志和审计行里必须
// 说得清区别 —— 否则「按策略跳过」会被读成「推送失败」。
type Policy struct {
	// Channel 是渠道名。写进判定理由里，让「为什么没发」在日志里自解释。
	Channel string
	// MaxPerDay 是同一渠道每天的推送上限。0 表示该渠道不推。
	MaxPerDay int
	// DedupeWindow 是同一条内容的去重窗口。0 表示不去重。
	DedupeWindow time.Duration
	// Quiet 是静默时段。落在这个时段里的推送被跳过。
	Quiet QuietHours
}

// QuietHours 是静默时段，按给定时区判断。
//
// 用时间点而不是日期区间：静默是「每天 23:00 到 08:00」这种循环，
// 跨午夜也要成立。跨午夜的区间（Start > End）按「夜里」处理。
type QuietHours struct {
	// Enabled 为假时整条规则不生效。
	Enabled bool
	// Start 与 End 是距当地零点的时长。跨午夜时 Start > End。
	Start time.Duration
	End   time.Duration
	// Loc 是判断时区。nil 用 UTC —— 没指定时区就用明确的默认，
	// 不要隐式依赖容器时区（默认镜像是 UTC，会把 08:30 变成 16:30）。
	Loc *time.Location
}

// Contains reports whether t falls inside the quiet window.
func (q QuietHours) Contains(t time.Time) bool {
	if !q.Enabled {
		return false
	}
	loc := q.Loc
	if loc == nil {
		loc = time.UTC
	}
	local := t.In(loc)
	minute := time.Duration(local.Hour())*time.Hour +
		time.Duration(local.Minute())*time.Minute
	if q.Start <= q.End {
		return minute >= q.Start && minute < q.End
	}
	// 跨午夜：23:00-08:00 这种。
	return minute >= q.Start || minute < q.End
}

// Decision 是一次推送的判定结果。
type Decision struct {
	// Allow 为真表示该推。
	Allow bool
	// Reason 是判定理由。Allow 为假时必填，且要说清是哪条规则拦的。
	Reason string
}

// Allow 返回一条允许推送的判定。
func allow() Decision { return Decision{Allow: true} }

// Deny 返回一条拒绝推送的判定。
func deny(format string, args ...any) Decision {
	return Decision{Allow: false, Reason: fmt.Sprintf(format, args...)}
}

// Gate 是 Policy 的有状态执行器：记住今天推过几条、上次推某个 key 是什么时候。
//
// 它只记「推送」这一侧的账，不记事件本身 —— 事件在消费者那边落库，
// 推送失败不影响事件（见包注释：the page is the primary surface）。
type Gate struct {
	mu     sync.Mutex
	policy Policy

	// day 是当前计数所属的自然日（按 policy 的时区）。
	day string
	// counts 是各渠道今天已推的条数。
	counts map[string]int
	// lastSent 是各 key 上次推送的时刻。
	lastSent map[string]time.Time
}

// NewGate builds a gate for one policy.
func NewGate(policy Policy) *Gate {
	return &Gate{
		policy:   policy,
		counts:   map[string]int{},
		lastSent: map[string]time.Time{},
	}
}

// Policy returns the gate's policy.
func (g *Gate) Policy() Policy {
	if g == nil {
		return Policy{}
	}
	return g.policy
}

// Decide 回答「这一次该不该推」。
//
// key 是去重键：同一件事在去重窗口内只推一次。调用方应当用**内容的
// 身份**做键（条件作业用条件行的 id 集合），不要用时间戳 —— 时间戳做键
// 等于没有去重。
func (g *Gate) Decide(now time.Time, key string) Decision {
	if g == nil {
		return allow()
	}
	if g.policy.MaxPerDay <= 0 {
		return deny("渠道 %s 的推送档位是关（MaxPerDay=0）", g.policy.Channel)
	}
	if g.policy.Quiet.Contains(now) {
		return deny("落在静默时段内（%s–%s）", g.policy.Quiet.Start, g.policy.Quiet.End)
	}

	g.mu.Lock()
	defer g.mu.Unlock()

	today := now.In(g.loc()).Format("2006-01-02")
	if today != g.day {
		// 跨日：计数清零。去重窗口独立于日界，单独算。
		g.day = today
		g.counts = map[string]int{}
	}

	if sent, ok := g.lastSent[key]; ok && g.policy.DedupeWindow > 0 &&
		now.Sub(sent) < g.policy.DedupeWindow {
		return deny("与 %s 的上一次推送相隔 %s，未超过去重窗口 %s",
			key, now.Sub(sent).Round(time.Second), g.policy.DedupeWindow)
	}

	if g.counts[g.policy.Channel] >= g.policy.MaxPerDay {
		return deny("渠道 %s 今天已推 %d 条，达到上限 %d",
			g.policy.Channel, g.counts[g.policy.Channel], g.policy.MaxPerDay)
	}

	// 通过：不占额度。额度由 Record 在实际投递成功后记 —— 投递失败的消息
	// 不该吃掉当天的额度（它根本没送到用户眼前）。
	// 去重窗口同理：没送出去就不算推过。
	return allow()
}

// Record 记一次已推送（调用方在判定通过、实际投递之后调用）。
//
// 判定与投递之间可能隔着一次失败：投递失败的消息不该占掉当天的额度。
func (g *Gate) Record(now time.Time, key string) {
	if g == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	today := now.In(g.loc()).Format("2006-01-02")
	if today != g.day {
		g.day = today
		g.counts = map[string]int{}
	}
	g.counts[g.policy.Channel]++
	if g.policy.DedupeWindow > 0 {
		g.lastSent[key] = now
	}
}

// loc 返回策略的时区，未指定时用 UTC。
func (g *Gate) loc() *time.Location {
	if g.policy.Quiet.Loc != nil {
		return g.policy.Quiet.Loc
	}
	return time.UTC
}

// 渠道档位。
//
// 日记的档位是**关**：Q17 选的是「不推送，只落库 + events，UI 是唯一出口」
// （见 docs/watchlist-diary-decisions.md）。写在这里而不是散在日记的代码里，
// 是为了让「日记不推」是一条能被 grep 到的策略，而不是一处「恰好没接线」。
const (
	// ChannelCondition 是条件作业的渠道：每次运行一条汇总。
	ChannelCondition = "condition"
	// ChannelDiary 是观察日记的渠道：档位关。
	ChannelDiary = "diary"
	// ChannelArticle 是公众号稿的渠道：档位关（对外发布走它自己的流水线）。
	ChannelArticle = "article"
)

// ConditionPolicy 是条件作业的推送策略。
//
// 每天一条：作业每天跑一次，一次运行发一条汇总消息（作业自己聚合）。
// 手动重跑不会多推 —— 这是「频率上限」在现实里唯一真正拦住的一次。
func ConditionPolicy() Policy {
	return Policy{
		Channel:   ChannelCondition,
		MaxPerDay: 1,
	}
}

// DiaryPolicy 是观察日记的推送策略：关。
//
// 理由：日记是每票一篇、每篇几百字，推出去是垃圾；而且飞书只能推纯文本，
// verdict 的结构化标签和颜色全丢。
func DiaryPolicy() Policy {
	return Policy{
		Channel:   ChannelDiary,
		MaxPerDay: 0,
	}
}

// ArticlePolicy 是公众号稿的推送策略：关。
//
// 公众号稿走它自己的发布流水线（外部项目），不走工作台的推送通道。
func ArticlePolicy() Policy {
	return Policy{
		Channel:   ChannelArticle,
		MaxPerDay: 0,
	}
}
