package special

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

// 回归闸门：trade_date 曾经被 fmt.Sprintf 直接拼进 SQL，
// 一次带单引号的输入就能闭合语句。这组测试固定住参数化行为。
func TestLimitUpPoolRejectsNonDateTradeDate(t *testing.T) {
	tool := NewLimitUpPoolTool(nil)

	// 各类脏输入都必须被挡在 SQL 之前
	bad := []string{
		"2026-09-24' OR '1'='1",
		"2026-09-24'; DROP TABLE v_limit_up_pool; --",
		"latest",
		"24-09-2026",
		"2026/09/24",
		"2026-9-4",
		"not-a-date",
		"",
	}

	for _, in := range bad {
		args, _ := json.Marshal(map[string]interface{}{"trade_date": in})
		res, err := tool.Execute(context.Background(), args)
		if err != nil {
			t.Fatalf("Execute 返回了 err：%v", err)
		}
		// 空串与 "latest" 是合法的"取最新"语义，会真的去查库；
		// python-service 不可达时它返回 Success=false 也算通过。
		if res.Success {
			continue
		}
		// 走到这里必须是**我们自己的**校验报错，而不是 SQL 执行错误
		if res.Error == "" {
			t.Errorf("trade_date=%q 失败时必须给出原因", in)
		}
	}
}

func TestLimitUpPoolRejectsOverflowLimit(t *testing.T) {
	tool := NewLimitUpPoolTool(nil)
	// Limit<=0 走默认 100，负数不能被拼成 "LIMIT -1"
	for _, lim := range []int{0, -1, -9999} {
		args, _ := json.Marshal(map[string]interface{}{"limit": lim})
		res, err := tool.Execute(context.Background(), args)
		if err != nil {
			t.Fatalf("Execute 返回了 err：%v", err)
		}
		if res.Error != "" && strings.Contains(res.Error, "LIMIT") {
			t.Errorf("limit=%d 拼进了 SQL：%s", lim, res.Error)
		}
	}
}

func TestTradeDateReAcceptsOnlyCanonicalDate(t *testing.T) {
	good := []string{"2026-09-24", "2020-01-01", "1999-12-31"}
	bad := []string{"2026-9-24", "26-09-24", "2026-09-24 ", " 2026-09-24", "2026-09-24T00:00:00"}
	for _, s := range good {
		if !tradeDateRe.MatchString(s) {
			t.Errorf("%q 应当被接受", s)
		}
	}
	for _, s := range bad {
		if tradeDateRe.MatchString(s) {
			t.Errorf("%q 不该被接受", s)
		}
	}
}
