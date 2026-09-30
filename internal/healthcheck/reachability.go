package healthcheck

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools"
)

// checkToolReachability is check 5, in both directions.
//
// One direction is an error at call time: a name in an agent's
// allowed_tools that no registered tool provides. The other is invisible
// work: a registered tool no agent's allowlist names, which the model can
// therefore never call.
//
// The runtime definition of "allowed" is tools.NormalizeAllowedTools, not
// string equality. Retired retrieval tool names (knowledge_search,
// grep_chunks, list_knowledge_chunks, get_document_info, ...) are still
// present in stored agent configs and are rewritten to their successors at
// registration time. Comparing raw strings would have reported four false
// positives on the single agent this check most needs to get right, and a
// check that cries wolf on a known-good agent gets ignored.
func (i *Inspector) checkToolReachability(ctx context.Context) ([]Finding, error) {
	if i.cfg.DB == nil {
		return nil, fmt.Errorf("no database handle configured")
	}

	agents, err := i.loadAgentAllowlists(ctx)
	if err != nil {
		return nil, err
	}

	registered := registeredToolNames()
	if len(registered) == 0 {
		return nil, fmt.Errorf("registered tool set is empty; the tool name scan found nothing, " +
			"so agent allowlists cannot be checked")
	}

	claimed := make(map[string][]string, len(registered))
	findings := make([]Finding, 0, len(agents))

	for _, a := range agents {
		// An empty allowlist is not a defect: the runtime substitutes
		// DefaultAllowedTools, so the agent still gets a working tool set.
		if len(a.allowed) == 0 {
			continue
		}
		for _, name := range tools.NormalizeAllowedTools(a.allowed) {
			if _, ok := registered[name]; ok {
				claimed[name] = append(claimed[name], a.object)
				continue
			}
			findings = append(findings, Finding{
				Check:    CheckToolReachability,
				Severity: SeverityWarning,
				Object:   a.object,
				Field:    "config->>'allowed_tools'",
				BadValue: name,
				Detail: "is in this agent's allowlist but no registered tool has this name; " +
					"a model that picks it gets a call-time error",
				Remediation: "remove it from allowed_tools, or register a tool with that name",
			})
		}
	}

	// The invisible direction. Report it as a single grouped finding rather
	// than one per tool: an operator needs the list, not N copies of the
	// same sentence, and the per-tool detail is noise when the tool is
	// simply not offered to any agent.
	if unreachable := unreachableTools(registered, claimed); len(unreachable) > 0 {
		findings = append(findings, Finding{
			Check:    CheckToolReachability,
			Severity: SeverityInfo,
			Object:   "tools.ToolRegistry",
			BadValue: fmt.Sprintf("%d tool(s)", len(unreachable)),
			Detail: fmt.Sprintf("%d registered tool(s) are in no agent's allowed_tools, so no "+
				"model can ever select them: %s",
				len(unreachable), strings.Join(unreachable, ", ")),
			Remediation: "either add the tool to an agent's allowed_tools, or accept that it is " +
				"unreachable and say so here so the next reader does not rediscover it",
		})
	}
	return findings, nil
}

type allowlistedAgent struct {
	object  string
	allowed []string
}

func (i *Inspector) loadAgentAllowlists(ctx context.Context) ([]allowlistedAgent, error) {
	// The config blob is selected whole and decoded here rather than
	// projected in SQL. `config->'allowed_tools'` is Postgres-only syntax;
	// SQLite needs json_extract(config,'$.allowed_tools'). Reading the blob
	// is the one formulation correct on both, and it keeps this check
	// testable against the repo's sqlite test driver — the same reason
	// check 1 avoids the ->> operator.
	var raw []struct {
		ID     string `gorm:"column:id"`
		Name   string `gorm:"column:name"`
		Config []byte `gorm:"column:config"`
	}
	if err := i.cfg.DB.WithContext(ctx).Raw(
		"SELECT id, name, config FROM custom_agents WHERE deleted_at IS NULL",
	).Scan(&raw).Error; err != nil {
		return nil, err
	}

	agents := make([]allowlistedAgent, 0, len(raw))
	for _, r := range raw {
		var cfg struct {
			AllowedTools []string `json:"allowed_tools"`
		}
		// A config blob that does not parse is the agent's own problem, not
		// a dangling reference; skipping it keeps this check to its subject.
		if len(r.Config) > 0 {
			if err := json.Unmarshal(r.Config, &cfg); err != nil {
				continue
			}
		}
		agents = append(agents, allowlistedAgent{
			object:  fmt.Sprintf("custom_agents:%s (id=%s)", r.Name, r.ID),
			allowed: cfg.AllowedTools,
		})
	}
	return agents, nil
}

// unreachableTools returns registered tools claimed by no agent, sorted so
// the finding is stable between runs.
func unreachableTools(registered map[string]bool, claimed map[string][]string) []string {
	out := make([]string, 0, len(registered))
	for name := range registered {
		if len(claimed[name]) == 0 {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}
