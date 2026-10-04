package healthcheck

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/types"
)

// loadBuiltinAgents populates types.BuiltinAgentRegistry from the repository's
// config/builtin_agents.yaml. The registry is process-global and LoadBuiltin...
// is guarded by a sync.Once, so this is safe to call from every test that needs
// it. A missing file leaves the registry empty; requiring it non-empty turns
// that into a loud failure instead of a silently weaker test.
func loadBuiltinAgents(t *testing.T) {
	t.Helper()
	wd, err := os.Getwd()
	require.NoError(t, err)
	root := filepath.Join(wd, "..", "..")
	require.FileExists(t, filepath.Join(root, "config", "builtin_agents.yaml"),
		"test must run from internal/healthcheck so the repo config is reachable")
	require.NoError(t, types.LoadBuiltinAgentsConfig(filepath.Join(root, "config")))
	require.NotEmpty(t, types.BuiltinAgentRegistry,
		"builtin agents must be loaded before exercising the reachability check")
}

// TestToolReachabilityClaimsYAMLBuiltinAgentTools is acceptance criterion 1 for
// the unreachable direction.
//
// A built-in agent that no tenant has customised exists only in
// config/builtin_agents.yaml, so the custom_agents query never sees it. Its
// tools are still reachable — every uncustomised tenant gets the agent — so
// treating it as invisible reports a false positive on a healthy deployment.
// The tools below appear in no DB row yet in the YAML; each must be claimed.
func TestToolReachabilityClaimsYAMLBuiltinAgentTools(t *testing.T) {
	loadBuiltinAgents(t)
	db := newTestDB(t) // no custom_agents rows at all

	insp := New(Config{DB: db, LogFindings: false})
	agents, err := insp.loadAgentAllowlists(context.Background())
	require.NoError(t, err)

	claimed := map[string]bool{}
	for _, a := range agents {
		for _, name := range a.allowed {
			claimed[name] = true
		}
	}

	// data_analysis/data_schema: builtin-data-analyst. wiki_*: wiki-researcher
	// and the internal wiki-fixer. write_skill_file/edit_skill_file and
	// shell_exec: the internal skill-installer. halo.* / zettaranc.*: the two
	// finance agents.
	for _, name := range []string{
		"data_schema", "data_analysis",
		"wiki_write_page", "wiki_replace_text", "wiki_read_issue", "wiki_flag_issue",
		"write_skill_file", "edit_skill_file", "shell_exec",
		"halo.analyze", "zettaranc.screener",
	} {
		assert.True(t, claimed[name], "%s is only in builtin_agents.yaml and must be claimed", name)
	}
}

// TestUnreachableToolsExcludesCapabilityAndAccepted pins the two exclusion
// sets: capability-registered tools can never be named by an allowlist, and
// accepted tools are unreachable on purpose. Neither is a defect, so only a
// genuinely unclaimed tool may survive into the finding.
func TestUnreachableToolsExcludesCapabilityAndAccepted(t *testing.T) {
	registered := map[string]bool{"genuinely_unclaimed": true}
	for name := range capabilityRegisteredTools {
		registered[name] = true
	}
	for name := range acceptedUnreachableTools {
		registered[name] = true
	}

	got := unreachableTools(registered, map[string][]string{})
	assert.Equal(t, []string{"genuinely_unclaimed"}, got)
}

// TestUnreachableToolsKeepsClaimedToolsSilent guards the other half of the
// predicate: a claimed tool is not reported even if it is capability-gated.
func TestUnreachableToolsKeepsClaimedToolsSilent(t *testing.T) {
	registered := map[string]bool{"claimed_tool": true}
	got := unreachableTools(registered, map[string][]string{"claimed_tool": {"agent-x"}})
	assert.Empty(t, got)
}

// TestAcceptedUnreachableToolsAreBoundedAndJustified is acceptance criterion 3
// for the accepted table, in the spirit of
// internal/agent/tools/zettaranc/whitelist_test.go: the table must not grow
// silently, and every entry must name a real registered tool and say why it is
// accepted rather than fixed.
func TestAcceptedUnreachableToolsAreBoundedAndJustified(t *testing.T) {
	require.LessOrEqual(t, len(acceptedUnreachableTools), acceptedUnreachableToolsLimit,
		"accepted-unreachable table exceeded its cap; a new entry must justify itself and raise the cap")

	registered := registeredToolNames()
	require.NotEmpty(t, registered, "the tool-name scan must find something")

	names := make([]string, 0, len(acceptedUnreachableTools))
	for name, reason := range acceptedUnreachableTools {
		names = append(names, name)
		assert.True(t, registered[name],
			"%s is accepted as unreachable but no registered tool answers to that name", name)
		assert.NotEmpty(t, strings.TrimSpace(reason), "%s must carry a reason", name)
	}
	sort.Strings(names)
	assert.NotEmpty(t, names)
}

// TestCapabilityRegisteredToolsAreRealAndJustified keeps the capability table
// from decaying into a list of names that no longer exist or that lost their
// explanation.
func TestCapabilityRegisteredToolsAreRealAndJustified(t *testing.T) {
	registered := registeredToolNames()
	require.NotEmpty(t, registered, "the tool-name scan must find something")

	for name, reason := range capabilityRegisteredTools {
		assert.True(t, registered[name],
			"%s is excluded from the unreachable direction but no registered tool answers to that name", name)
		assert.NotEmpty(t, strings.TrimSpace(reason), "%s must document its registration site", name)
	}
}

// TestToolReachabilityCreditsTheDefaultToolSet locks the third blind spot: an
// agent with an empty allowlist is skipped as "not a defect", but the runtime
// substitutes DefaultAllowedTools for it, so those names are reachable too.
// Without the credit, search_conversations reads as unreachable on a
// deployment where every uncustomised agent can in fact call it.
func TestToolReachabilityCreditsTheDefaultToolSet(t *testing.T) {
	loadBuiltinAgents(t)
	db := newTestDB(t) // no custom_agents rows at all

	insp := New(Config{DB: db, LogFindings: false})
	findings, err := insp.checkToolReachability(context.Background())
	require.NoError(t, err)

	for _, f := range findings {
		assert.NotContains(t, f.Detail, tools.ToolSearchConversations,
			"search_conversations is in DefaultAllowedTools, so an empty allowlist reaches it")
	}
	// With no customised agents the whole unreachable direction must be silent:
	// every remaining name is YAML-claimed, capability-registered, in the
	// default set, or explicitly accepted.
	assert.Empty(t, findings, "a deployment with no customised agents must not report unreachable tools")
}

// TestToolReachabilityHasNoFalsePositive is the end-to-end assertion: with the
// YAML built-ins loaded and one ordinary agent naming the remaining selectable
// tools, every registered tool is either claimed, capability-registered, or
// accepted — so the unreachable finding must not appear at all.
func TestToolReachabilityHasNoFalsePositive(t *testing.T) {
	loadBuiltinAgents(t)
	db := newTestDB(t)
	seedAgent(t, db, "a1", "Ordinary Agent", `{"allowed_tools":[`+
		`"search_knowledge","read_document","list_documents","search_conversations",`+
		`"database_query","query_knowledge_graph"]}`)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	assert.NotContains(t, report.CheckErrors, CheckToolReachability, "the check must have run")
	for _, f := range report.Findings {
		if f.Check == CheckToolReachability {
			t.Errorf("unreachable direction still reports a false positive: %s", f.Detail)
		}
	}
}
