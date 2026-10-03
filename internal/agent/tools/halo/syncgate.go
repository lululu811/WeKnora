package halo

import (
	"context"
	"fmt"
	"strings"
	"sync"
)

// maxConcurrentSync 是**全局**同时同步的标的数上限。
//
// 取 2 而不是 1：同一只票的重复同步由 inflight 直接挡掉，而不同标的的同步之间
// 没有共享状态（各自下载各自的 PDF、落各自的表），串行化只是白等。给 2 是为了
// 在「用户在自选列表里连点两只没同步过的票」时不至于排成一条长队，同时又让
// 巨潮侧的并发保持在个位数。
//
// 这个值是**并发**上限，不是速率上限。真正的速率限制在 python-service 内部
// （巨潮检索默认 500ms 间隔），Go 侧不重复实现。
const maxConcurrentSync = 2

// syncGate 保护 /halo/sync 的并发。
//
// 为什么需要它：一次同步要下载 1–10 MB 的 PDF 并逐页解析，一份年报 1–3 分钟
// （见 SyncTool.Description）。而缓存键是 (thscode, report_type, year)、年报
// 一年只变一次，所以**重复触发几乎全是白等** —— 两次并发的结果一样，成本翻倍，
// 还多消耗一次巨潮的限流额度。
//
// 放在 HTTPClient 上而不是 HTTP handler 上：/halo/sync 有两条调用方（agent 的
// halo.filing.sync 工具、工作台的 /halo/sync 端点），闸门必须对两者都生效。
type syncGate struct {
	// sem 是全局并发闸门。取一个元素即占用一个名额。
	sem chan struct{}
	// mu 保护 inflight。
	mu sync.Mutex
	// inflight 记录正在同步的标的（已归一为大写、去空白）。
	inflight map[string]struct{}
}

func newSyncGate(n int) *syncGate {
	if n < 1 {
		n = 1
	}
	return &syncGate{
		sem:      make(chan struct{}, n),
		inflight: make(map[string]struct{}),
	}
}

// acquire 为 thscode 占一个同步位，返回的 release 必须被调用（defer）。
//
// 两种等待方式刻意不同：
//   - **同一标的已在同步** → 立刻报错，不排队。排队意味着这个 HTTP 请求要挂
//     最多 10 分钟（syncTimeout）才可能拿到一个「早被别人做完了」的结果，
//     而它的结果本来就已经在库里了。报错让调用方知道该等的是**上一次**。
//   - **不同标的、闸门满** → 排队等待，因为它们互相独立。等不到就随 ctx 取消
//     返回，调用方（浏览器）会自己超时。
func (g *syncGate) acquire(ctx context.Context, thscode string) (func(), error) {
	key := strings.ToUpper(strings.TrimSpace(thscode))
	if key == "" {
		return nil, fmt.Errorf("thscode 不能为空")
	}

	g.mu.Lock()
	if _, busy := g.inflight[key]; busy {
		g.mu.Unlock()
		return nil, fmt.Errorf(
			"%s 的年报正在同步中，请等它跑完再试（一次同步需要 1–3 分钟，重复触发不会更快）", thscode)
	}
	g.inflight[key] = struct{}{}
	g.mu.Unlock()

	select {
	case g.sem <- struct{}{}:
	case <-ctx.Done():
		// 排队期间被取消：必须把 inflight 标记还回去，否则这只票会被永久标记成
		// 「正在同步」，之后谁都同步不了它，且没有任何日志能解释为什么。
		g.mu.Lock()
		delete(g.inflight, key)
		g.mu.Unlock()
		return nil, ctx.Err()
	}

	// release 允许被重复调用而不破坏计数（defer + 提前 return 的组合会出现）。
	var once sync.Once
	return func() {
		once.Do(func() {
			<-g.sem
			g.mu.Lock()
			delete(g.inflight, key)
			g.mu.Unlock()
		})
	}, nil
}
