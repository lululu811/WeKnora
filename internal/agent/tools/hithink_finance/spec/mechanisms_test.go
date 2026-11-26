package spec_test

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec/fakeservice"
)

// 这三条测的是冻结快照**按设计绕开**的那几条路。
//
// 快照管的是"给定参数时模型看到什么、工具发出去什么"；它刻意不收那些
// **参数没给**的路径——那些路径的输出跟着日历或环境走，快照不该跟着它们漂
// （快照一漂，它就不再是"迁移前那一刻的字节"了）。
//
// 于是那些路径各自在这里钉住：改坏了这里，红的是这一条，而不是快照。

// TestRuntimeDefaultFollowsTheCalendar 钉住「运行时缺省」这条机制。
//
// 分红日历不给 start_date 时，缺省是**当年年初**——它跟着日历走，不是写死的
// 某个年份。把它写死成 2026 的话，跨年之后它会静默地筛起去年：模型问"今年
// 的分红"，工具答的是去年的，而且答得理直气壮。
//
// 同一处还钉住 end_date 的缺省（2099-12-31）——它写死在声明里，但同样不该
// 出现在 schema 的 default 上（迁移前就没有）。
func TestRuntimeDefaultFollowsTheCalendar(t *testing.T) {
	srv := fakeservice.New()
	defer srv.Close()
	cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}

	tool, ok := toolByName("hithink.finance.market.dividend.calendar", cfg)
	if !ok {
		t.Fatal("找不到分红日历工具")
	}
	res, err := tool.Execute(context.Background(), json.RawMessage(`{}`))
	if err != nil {
		t.Fatalf("Execute 返回 error：%v", err)
	}
	if res == nil || !res.Success {
		t.Fatalf("分红日历没成功：%v", res)
	}
	if len(srv.Requests) != 1 {
		t.Fatalf("发了 %d 条查询，期望 1 条", len(srv.Requests))
	}
	params := srv.Requests[0].Params
	if len(params) != 5 {
		t.Fatalf("绑定值有 %d 个，期望 5 个（start/end/min_dividend/min_bonus/limit）", len(params))
	}

	wantStart := fmt.Sprintf("%d-01-01", time.Now().Year())
	if params[0] != wantStart {
		t.Errorf("起始日期缺省是 %q，期望 %q（当年年初）", params[0], wantStart)
	}
	if params[1] != "2099-12-31" {
		t.Errorf("截止日期缺省是 %q，期望 %q", params[1], "2099-12-31")
	}

	// schema 上不该出现缺省：迁移前就没有，模型看到的就是"没写缺省"。
	var schema struct {
		Properties map[string]struct {
			Default any `json:"default"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(tool.Parameters(), &schema); err != nil {
		t.Fatalf("schema 不是合法 JSON：%v", err)
	}
	for _, name := range []string{"start_date", "end_date"} {
		if _, ok := schema.Properties[name]; !ok {
			t.Errorf("schema 里少了 %s", name)
			continue
		}
		if schema.Properties[name].Default != nil {
			t.Errorf("schema 的 %s 出现了缺省 %v——迁移前没有，改了就是模型看到的东西变了",
				name, schema.Properties[name].Default)
		}
	}
}

// TestBooleanParamReachesTheHook 钉住布尔参数这条机制。
//
// include_noisy 决定"高频状态类信号"是折叠还是返回。它要是没传到位，两次调用
// 会给出同一份输出——而模型会以为"没有 CCI超卖"是事实。
func TestBooleanParamReachesTheHook(t *testing.T) {
	names := func(t *testing.T, args string) (got []string, suppressed []string) {
		t.Helper()
		srv := fakeservice.New()
		defer srv.Close()
		cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}
		tool, ok := toolByName("hithink.finance.pattern.scan", cfg)
		if !ok {
			t.Fatal("找不到形态扫描工具")
		}
		res, err := tool.Execute(context.Background(), json.RawMessage(args))
		if err != nil {
			t.Fatalf("Execute 返回 error：%v", err)
		}
		if res == nil || !res.Success {
			t.Fatalf("形态扫描没成功：%v", res)
		}
		var out struct {
			Signals []struct {
				Name string `json:"name"`
			} `json:"signals"`
			Suppressed *struct {
				Count int      `json:"count"`
				Names []string `json:"names"`
			} `json:"suppressed"`
		}
		if err := json.Unmarshal([]byte(res.Output), &out); err != nil {
			t.Fatalf("输出不是合法 JSON：%v", err)
		}
		for _, s := range out.Signals {
			got = append(got, s.Name)
		}
		if out.Suppressed != nil {
			suppressed = out.Suppressed.Names
		}
		return got, suppressed
	}

	quiet, suppressed := names(t, `{"thscode":"600519.SH"}`)
	noisy, _ := names(t, `{"thscode":"600519.SH","include_noisy":true}`)

	if len(suppressed) == 0 {
		t.Fatal("假数据里一条状态类信号都没折叠——这条测试的前提不成立")
	}
	quietSet := map[string]bool{}
	for _, n := range quiet {
		quietSet[n] = true
	}
	noisySet := map[string]bool{}
	for _, n := range noisy {
		noisySet[n] = true
	}
	for _, n := range suppressed {
		if quietSet[n] {
			t.Errorf("%q 既被折叠又在默认输出里——折叠没生效", n)
		}
		if !noisySet[n] {
			t.Errorf("%q 被折叠了，但 include_noisy=true 时没回来——布尔参数没传到钩子", n)
		}
	}
}

// TestFreeSQLIsGuarded 钉住自由 SQL 的那道护栏。
//
// 语句是模型自己写的，所以"只放 SELECT"不是可选的礼貌，是唯一挡在写操作
// 前面的东西。迁移前这道检查在工具里，迁移后它在声明里——护栏没被顺手丢掉，
// 这条测试就是证据。
func TestFreeSQLIsGuarded(t *testing.T) {
	srv := fakeservice.New()
	defer srv.Close()
	cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}

	tool, ok := toolByName("hithink.finance.query.sql", cfg)
	if !ok {
		t.Fatal("找不到自由 SQL 工具")
	}

	for _, c := range []struct {
		name string
		sql  string
		want string
	}{
		{"不是 SELECT", "DROP TABLE v_daily_qfq", "只允许 SELECT"},
		{"SELECT 里的写操作", "SELECT 1; DELETE FROM v_daily_qfq", "禁止 DELETE"},
	} {
		t.Run(c.name, func(t *testing.T) {
			res, err := tool.Execute(context.Background(),
				json.RawMessage(fmt.Sprintf(`{"sql":%q,"db":"market"}`, c.sql)))
			if err != nil {
				t.Fatalf("Execute 返回 error：%v", err)
			}
			if res == nil || res.Success {
				t.Fatalf("这条语句本该被拦下来，却成功了：%v", res)
			}
			if !strings.Contains(res.Error, c.want) {
				t.Errorf("错误是 %q，期望提到 %q", res.Error, c.want)
			}
			if len(srv.Requests) != 0 {
				t.Errorf("被拦下的语句还是发出去了（%d 条请求）", len(srv.Requests))
			}
		})
	}
}

// TestDiscoverIgnoresTheSyncWindow 钉住「不受同步窗口拦截」这条机制。
//
// discover 查的是工具清单与列目录，不是行情数据，同步窗口跟它无关。迁移前它
// 没有这道检查，迁移后也不该有——否则每天 ETL 那十分钟里，模型连"有哪些工具"
// 都问不出来。
func TestDiscoverIgnoresTheSyncWindow(t *testing.T) {
	// 把窗口设成全天：任何时刻都在窗口里。
	t.Setenv("HITHINK_SYNC_WINDOWS", "00:00-23:59")

	srv := fakeservice.New()
	defer srv.Close()
	cfg := &hithink_finance.Config{ServiceURL: srv.URL, Timeout: 5 * time.Second}

	// 先证明窗口确实是关着的：别的工具被拦住了。
	other, ok := toolByName("hithink.finance.market.price.snapshot", cfg)
	if !ok {
		t.Fatal("找不到行情快照工具")
	}
	res, err := other.Execute(context.Background(), json.RawMessage(`{"thscode":"600519.SH"}`))
	if err != nil {
		t.Fatalf("Execute 返回 error：%v", err)
	}
	if res == nil || res.Success || res.Error == "" {
		t.Fatalf("同步窗口关着的时候行情快照居然成功了——这条测试的前提不成立")
	}

	// discover 不受这道拦截。
	disc, ok := toolByName("hithink.finance.discover", cfg)
	if !ok {
		t.Fatal("找不到发现工具")
	}
	res, err = disc.Execute(context.Background(), json.RawMessage(`{"kind":"columns"}`))
	if err != nil {
		t.Fatalf("Execute 返回 error：%v", err)
	}
	if res == nil || !res.Success {
		t.Fatalf("同步窗口关着的时候 discover 失败了：%v", res)
	}
}
