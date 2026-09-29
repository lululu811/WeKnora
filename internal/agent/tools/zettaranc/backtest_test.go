package zettaranc

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

// zettaranc.backtest 过去收了 thscode 与 days 两个参数却**完全没读**，
// 实际调用的是选股接口，把一张全市场候选票的列表当成"回测结果"返回，
// 只有 JSON 深处一句 note 写着"迁移中"。
//
// 描述里还承诺了「收益率、夏普比率、最大回撤、胜率、盈亏比、资金曲线」——
// 它一样都算不出来。这比没有这个工具更糟：模型会拿一个与问题无关的结果
// 去回答用户，而且看上去像是算出来的。
//
// 这组测试固定住「明确失败」这个行为。
func TestBacktestRefusesInsteadOfReturningScreenerResults(t *testing.T) {
	tool := NewBacktestTool(nil) // nil client：任何真实调用都会 panic，正好证明没在调

	args, _ := json.Marshal(map[string]interface{}{
		"strategy": "shaofu",
		"thscode":  "600519.SH",
		"days":     250,
	})
	res, err := tool.Execute(context.Background(), args)
	if err != nil {
		t.Fatalf("Execute 返回了 err：%v", err)
	}

	if res.Success {
		t.Fatalf("backtest 必须失败而不是返回替代数据，却返回了：%s", res.Output)
	}
	if res.Output != "" {
		t.Errorf("失败时不该还有 Output，模型会当数据用：%s", res.Output)
	}
	if !strings.Contains(res.Error, "screener") {
		t.Errorf("错误信息必须指出替代方案，否则模型只会说回测失败：%s", res.Error)
	}
}

func TestBacktestDescriptionDoesNotPromiseMetricsItCannotCompute(t *testing.T) {
	desc := NewBacktestTool(nil).Description()
	for _, claim := range []string{"夏普", "最大回撤", "资金曲线", "胜率"} {
		if strings.Contains(desc, claim) {
			t.Errorf("Description 仍承诺 %q，但它算不出来", claim)
		}
	}
	// 必须让模型知道这工具不能用，否则它仍会照着示例去调
	if !strings.Contains(desc, "未实现") {
		t.Errorf("Description 必须明说未实现：%s", desc)
	}
}

func TestBacktestParametersDoNotAdvertiseWorkingOptions(t *testing.T) {
	raw := NewBacktestTool(nil).Parameters()
	var schema struct {
		Required []string `json:"required"`
		Props    map[string]struct {
			Enum        []string `json:"enum"`
			Description string   `json:"description"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatalf("Parameters 不是合法 JSON：%v", err)
	}
	if len(schema.Required) > 0 {
		t.Errorf("未实现的工具不该把参数列为必填：%v", schema.Required)
	}
	if len(schema.Props["strategy"].Enum) > 0 {
		t.Errorf("strategy 不该再有 enum，那会让模型以为能选：%v",
			schema.Props["strategy"].Enum)
	}
}
