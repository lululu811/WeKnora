package zettaranc_test

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"github.com/Tencent/WeKnora/internal/agent/tools/zettaranc"
)

// 端到端：Go 工具 → python-service。
//
// 之前所有验证都停在 HTTP 层（curl 直接打 python-service），Go 的
// HTTPClient 与参数序列化从没被真正跑过 —— 选项函数、指针省略、
// 字段名对不上这类问题都只会在这一层暴露。
//
// 需要 python-service 在跑；不可达就跳过，不让 CI 变红。

func liveClient() *zettaranc.HTTPClient {
	url := os.Getenv("PYTHON_SERVICE_URL")
	if url == "" {
		url = "http://127.0.0.1:50052"
	}
	return zettaranc.NewHTTPClient(url)
}

// requireLive 拿最便宜的一次调用当探针：服务不可达就跳过，
// 不让没有 python-service 的 CI 变红。
func requireLive(t *testing.T) *zettaranc.HTTPClient {
	t.Helper()
	c := liveClient()
	if _, err := c.Screen(context.Background(), "oversold_combo", 1); err != nil {
		t.Skipf("python-service 不可达（%v），跳过端到端测试", err)
	}
	return c
}

func TestScreenSendsStrategyAndLimit(t *testing.T) {
	c := requireLive(t)

	res, err := c.Screen(context.Background(), "oversold_combo", 5)
	if err != nil {
		t.Fatalf("Screen 失败：%v", err)
	}
	if res["success"] != true {
		t.Fatalf("success != true：%v", res)
	}
	if _, ok := res["stocks"]; !ok {
		t.Fatalf("返回里没有 stocks：%v", res)
	}
}

func TestScreenSectorOptionReachesPython(t *testing.T) {
	c := requireLive(t)

	// 精确匹配：行业「银行」应远少于全市场
	res, err := c.Screen(context.Background(), "oversold_combo", 5, zettaranc.WithSector("银行"))
	if err != nil {
		t.Fatalf("Screen 失败：%v", err)
	}
	sf, ok := res["sector_filter"].(map[string]interface{})
	if !ok {
		t.Fatalf("sector_filter 没有透传到 python：%v", res)
	}
	if sf["name"] != "银行" {
		t.Errorf("sector name = %v，期望 银行", sf["name"])
	}
	if sf["match_mode"] != "exact" {
		t.Errorf("match_mode = %v，精确匹配应返回 exact", sf["match_mode"])
	}
	// 实测行业「银行」42 只；如果变成 200+ 说明又退回了模糊匹配
	if n, ok := sf["constituents"].(float64); ok && n > 100 {
		t.Errorf("银行板块 %v 只，明显偏大 —— 概念板块被混进来了", n)
	}
}

func TestScreenRiskOptionsReachPython(t *testing.T) {
	c := requireLive(t)

	res, err := c.Screen(context.Background(), "oversold_combo", 5,
		zettaranc.WithMaxDebtRatio(0.5),
		zettaranc.WithMinCurrentRatio(1.0),
		zettaranc.WithRequireProfit(),
		zettaranc.WithExcludeST(),
	)
	if err != nil {
		t.Fatalf("Screen 失败：%v", err)
	}
	rf, ok := res["risk_filter"].(map[string]interface{})
	if !ok {
		t.Fatalf("risk_filter 没有透传到 python：%v", res)
	}
	if rf["max_debt_ratio"] != 0.5 {
		t.Errorf("max_debt_ratio = %v，期望 0.5", rf["max_debt_ratio"])
	}
	// 通过的每只票都必须真的带风险数值，且负债率不超过阈值
	stocks, _ := res["stocks"].([]interface{})
	for _, s := range stocks {
		m, ok := s.(map[string]interface{})
		if !ok {
			continue
		}
		risk, ok := m["risk"].(map[string]interface{})
		if !ok {
			t.Errorf("%v 通过了风险筛选却没有 risk 字段，用户无法复核", m["thscode"])
			continue
		}
		if dr, ok := risk["debt_ratio"].(float64); ok && dr > 0.5 {
			t.Errorf("%v 负债率 %v 却通过了 0.5 的阈值", m["thscode"], dr)
		}
		if st, ok := m["is_st"].(bool); ok && st {
			t.Errorf("%v 是 ST 却通过了 exclude_st", m["thscode"])
		}
	}
}

func TestScreenWithoutFiltersLeavesThemNull(t *testing.T) {
	c := requireLive(t)

	// 不传筛选 → 服务端不该去查 financials，risk_filter 应为 nil
	res, err := c.Screen(context.Background(), "oversold_combo", 3)
	if err != nil {
		t.Fatalf("Screen 失败：%v", err)
	}
	if res["risk_filter"] != nil {
		t.Errorf("没传筛选时 risk_filter 应为 nil，实际 %v", res["risk_filter"])
	}
	if res["sector_filter"] != nil {
		t.Errorf("没传板块时 sector_filter 应为 nil，实际 %v", res["sector_filter"])
	}
}

// 选股结果必须能追回到源表与截止日。工具层每一行结果都带 `_source`，
// 选股器是对齐的那个：模型据此回答"凭什么说它超卖"时要有可引用的表名。
func TestScreenProvenanceReachesTheCaller(t *testing.T) {
	c := requireLive(t)

	res, err := c.Screen(context.Background(), "oversold_combo", 3)
	if err != nil {
		t.Fatalf("Screen 失败：%v", err)
	}
	src, ok := res["sources"].(map[string]interface{})
	if !ok {
		t.Fatalf("返回里没有 sources，模型无从引用出处：%v", res)
	}
	signals, _ := src["signals"].([]interface{})
	if len(signals) == 0 {
		t.Fatal("sources.signals 为空")
	}
	found := false
	for _, s := range signals {
		if s == "indicators.v_indicators_daily" {
			found = true
		}
	}
	if !found {
		t.Errorf("sources.signals 里没有指标源表：%v", signals)
	}
	if src["indicator_as_of"] == nil || src["indicator_as_of"] == "" {
		t.Errorf("没报指标截止日：%v", src)
	}
}

func TestScreenRequestOmitsUnsetPointers(t *testing.T) {
	// 不打网络，只验序列化：未设置的阈值必须整个字段消失，
	// 而不是序列化成 null 或 0 —— 0 会被服务端当成"负债率上限 0"，
	// 把所有票都筛掉。
	b, err := json.Marshal(zettaranc.ScreenRequest{Strategy: "oversold_combo", Limit: 5})
	if err != nil {
		t.Fatalf("序列化失败：%v", err)
	}
	var m map[string]interface{}
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("反序列化失败：%v", err)
	}
	for _, k := range []string{
		"max_debt_ratio", "min_current_ratio", "max_receivable_ratio",
		"sector", "require_profit", "exclude_st",
	} {
		if _, present := m[k]; present {
			t.Errorf("未设置的 %q 不该出现在请求体里：%s", k, b)
		}
	}
}

func TestScreenOptionZeroIsSentNotDropped(t *testing.T) {
	// 0 是合法阈值（"负债率不超过 0"），不能因为是零值就被 omitempty 吞掉。
	// 这就是用 *float64 而不是 float64 的原因。
	b, _ := json.Marshal(zettaranc.ScreenRequest{
		Strategy:     "oversold_combo",
		MaxDebtRatio: floatPtr(0),
	})
	var m map[string]interface{}
	_ = json.Unmarshal(b, &m)
	if v, present := m["max_debt_ratio"]; !present {
		t.Errorf("显式设的 0 阈值被 omitempty 吞掉了：%s", b)
	} else if v.(float64) != 0 {
		t.Errorf("max_debt_ratio = %v，期望 0", v)
	}
}

func floatPtr(f float64) *float64 { return &f }
