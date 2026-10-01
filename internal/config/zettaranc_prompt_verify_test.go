package config

import (
	"fmt"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

// The Zettaranc builtin agent runs in smart-reasoning mode, so
// ResolveCustomAgentPrompts looks the template up in the AgentSystemPrompt
// list. Its template used to live in system_prompt.yaml, where that lookup
// could never find it — the reference silently resolved to "" and the agent
// fell back to the default system prompt. These tests load the real config
// files so the regression cannot come back unnoticed.
func TestZettarancPromptResolvesInAgentMode(t *testing.T) {
	pt, err := loadPromptTemplates("../../config")
	require.NoError(t, err, "prompt templates must load")

	agent := &types.CustomAgent{Config: types.CustomAgentConfig{
		AgentMode:      "smart-reasoning",
		SystemPromptID: "zettaranc",
	}}
	require.True(t, agent.IsAgentMode(), "fixture must stay in agent mode")

	system, _ := (&Config{PromptTemplates: pt}).ResolveCustomAgentPrompts(agent)
	require.NotEmpty(t, system, "zettaranc template must resolve in agent mode")
	require.Contains(t, system, "你是 Z哥", "resolved prompt must be the Zettaranc prompt")
	require.Contains(t, system, "{{language}}", "language placeholder must survive the move")
}

func TestZettarancTemplateNotInNormalSystemPromptList(t *testing.T) {
	// Agent-mode lookup must not depend on system_prompt.yaml: keeping a second
	// copy there is what let the two lists drift apart unnoticed.
	pt, err := loadPromptTemplates("../../config")
	require.NoError(t, err)

	for _, tpl := range pt.SystemPrompt {
		require.NotEqual(t, "zettaranc", tpl.ID,
			"zettaranc must live only in agent_system_prompt.yaml")
	}

	var found bool
	for _, tpl := range pt.AgentSystemPrompt {
		if tpl.ID == "zettaranc" {
			found = true
		}
	}
	require.True(t, found, "zettaranc must be registered as an agent-mode template")
}

func TestZettarancPromptToolRoutingIsDocumented(t *testing.T) {
	pt, err := loadPromptTemplates("../../config")
	require.NoError(t, err)

	var content string
	for _, tpl := range pt.AgentSystemPrompt {
		if tpl.ID == "zettaranc" {
			content = tpl.Content
		}
	}
	require.NotEmpty(t, content)

	// The prompt must tell the model which layer of the tool surface to reach
	// for, and must not advertise a knowledge-base tool that does not exist.
	for _, want := range []string{
		"zettaranc.analyze",
		"zettaranc.screener",
		"hithink.finance.index.sector.membership",
		"hithink.finance.analysis.levels",
		"不是工具",
	} {
		require.True(t, strings.Contains(content, want),
			"prompt must mention %q so tool routing stays discoverable", want)
	}

	// Drift guard, same job as the loop above: the prompt must not tell the
	// model to avoid a tool that is no longer granted. `zettaranc.backtest`
	// used to be pinned here as "当前不支持，调用必然失败，不要尝试"; the tool
	// has since been dropped from builtin_agents.yaml and from the
	// registration switch, so its schema never reaches the model and a
	// "don't call it" line is pure per-turn context cost that will mislead
	// anyone who re-enables the tool later.
	require.NotContains(t, content, "zettaranc.backtest",
		"prompt must not reference zettaranc.backtest: it is no longer granted, "+
			"so the name cannot appear in any tool schema and warning about it is dead weight")
}

// TestZettarancPromptSignalCountMatchesImplementation pins the signal count the
// prompt quotes to what detectSignals actually declares.
//
// 它此前写的是「40+ 类」，而 pattern.declaredSignalNames 一直是 71 条 ——
// 差了近一半，且没有任何测试发现。prompt 里的数量对模型是承诺：它按这个数字
// 预期 scan 的信息量。两处各写各的就会一直漂，而漂的方向是**少报**，
// 模型因此低估 scan 的产出。
//
// 数字取自 pattern 包的 declaredSignalNames；该包已用
// signals_contract_test.go 的 TestDeclaredSignalCountPinned 守住自身条数，
// 所以这里只需保证 prompt 跟着变。
func TestZettarancPromptSignalCountMatchesImplementation(t *testing.T) {
	pt, err := loadPromptTemplates("../../config")
	require.NoError(t, err)

	var content string
	for _, tpl := range pt.AgentSystemPrompt {
		if tpl.ID == "zettaranc" {
			content = tpl.Content
		}
	}
	require.NotEmpty(t, content)

	// 只认"确切数字"这种写法；"40+ 类"这种模糊说法无法被验证，正是要消灭的。
	require.NotContains(t, content, "40+ 类",
		"prompt must not use a vague signal count: an unverifiable number is "+
			"exactly how it drifted to '40+' while the engine had 71")
	require.Contains(t, content, fmt.Sprintf("全部技术信号（%d 类", declaredSignalCountForPrompt),
		"prompt must quote the real signal count; update both this test and the "+
			"prompt when signals.go's declaredSignalNames changes")
}

// declaredSignalCountForPrompt mirrors pattern.declaredSignalNames 的条数。
// 跨包无法直接 import（会引入 tools→config 的依赖方向），所以这里显式写出
// 并由本测试与 prompt 互锁；signals_contract_test.go 另行钉住引擎侧条数。
// 两者不一致时，pattern 包的测试与本测试会同时报警。
const declaredSignalCountForPrompt = 71

// TestZettarancPromptDoesNotPromiseMissingAnalyseSections guards the zettaranc
// prompt against advertising analysis sections zettaranc.analyze never returns.
//
// python-service /zettaranc/analyze 的返回只有 trend / volume / chart_pattern
// / levels 四段（main.py:1136-1176）。prompt 此前写「一次调用即返回：…砖型图、
// 三波理论阶段、麒麟会、30+ 战法信号、综合评分」，这些**一项都不存在**，
// 每一项都会让模型向用户承诺一个拿不到的读数。
func TestZettarancPromptDoesNotPromiseMissingAnalyseSections(t *testing.T) {
	pt, err := loadPromptTemplates("../../config")
	require.NoError(t, err)

	var content string
	for _, tpl := range pt.AgentSystemPrompt {
		if tpl.ID == "zettaranc" {
			content = tpl.Content
		}
	}
	require.NotEmpty(t, content)

	// 只查「声称 analyze 会返回评分」的那一句，不全篇禁词。
	//
	// prompt 里另有一处"60+ 技术指标、30+ 战法识别"（:245/:249/:270），那是
	// **agent 自身能力**的描述——它确实能通过 screener/scan 做到，与 analyze
	// 的返回无关。全篇禁词会把正确的自我描述也砍掉。
	analyzeLine := ""
	for _, line := range strings.Split(content, "\n") {
		if strings.Contains(line, "zettaranc.analyze") && strings.Contains(line, "评分") {
			analyzeLine = line
		}
	}
	require.NotContains(t, analyzeLine, "综合评分（",
		"prompt must not claim zettaranc.analyze returns a composite score: the "+
			"endpoint returns only trend/volume/chart_pattern/levels and has no score field")
	require.NotContains(t, content, "一次调用即返回：KDJ",
		"prompt must not claim a single analyze call returns indicator values, "+
			"brick/brick-chart or 战法信号: analyze has no such section")
	// 三波理论/麒麟会/四块砖确实无本地实现，但**允许** prompt 提到它们 ——
	// 它需要告诉模型"用户问起这些就说算不出来，请去看工作台"（见同文件
	// 「本地算不出来的东西」一节）。
	require.Contains(t, content, "zettaranc.analyze",
		"prompt must still route to zettaranc.analyze for single-stock questions")
}
