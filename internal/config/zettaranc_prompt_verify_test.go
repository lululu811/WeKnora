package config

import (
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
