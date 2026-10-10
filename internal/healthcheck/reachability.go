package healthcheck

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/types"
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

	// An agent with an empty allowlist is not a defect — the runtime
	// substitutes DefaultAllowedTools — but that substitution also means the
	// default names ARE reachable. Without this, search_conversations reads as
	// unreachable while every agent that never customised its allowlist can
	// call it (session_agent_qa.go / agent_service.go both fall back to
	// DefaultAllowedTools when AllowedTools is empty).
	for _, name := range tools.DefaultAllowedTools() {
		if _, ok := registered[name]; ok {
			claimed[name] = append(claimed[name], "tools.DefaultAllowedTools()")
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
			Remediation: "either add the tool to an agent's allowed_tools, or add it to " +
				"acceptedUnreachableTools in internal/healthcheck/reachability.go with a reason, " +
				"so the next reader does not rediscover it",
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

	// Built-in agents with no custom_agents row are invisible to the query
	// above, yet their tools are reachable by every tenant that has not
	// customised the agent. customAgentService.ListAgents merges the DB rows
	// with the YAML definitions (custom_agent.go, ListAgents), and a reachable
	// tool is one *any* agent names — so the union of both sources is the right
	// input here, not the DB rows alone.
	//
	// BuiltinAgentRegistry rather than GetBuiltinAgentIDs: the latter lists only
	// the user-facing agents and deliberately omits the internal wiki-fixer and
	// skill-installer, whose wiki_* / write_skill_file / edit_skill_file tools
	// would otherwise keep showing up as unreachable.
	//
	// The factory ignores tenantID for the allowlist (the YAML config is the
	// same for every tenant), so 0 is passed rather than inventing a tenant.
	builtinIDs := make([]string, 0, len(types.BuiltinAgentRegistry))
	for id := range types.BuiltinAgentRegistry {
		builtinIDs = append(builtinIDs, id)
	}
	sort.Strings(builtinIDs)
	for _, id := range builtinIDs {
		agent := types.GetBuiltinAgent(id, 0)
		if agent == nil {
			continue
		}
		agents = append(agents, allowlistedAgent{
			object:  fmt.Sprintf("builtin_agents.yaml:%s (id=%s)", agent.Name, agent.ID),
			allowed: agent.Config.AllowedTools,
		})
	}
	return agents, nil
}

// capabilityRegisteredTools are tool names registered from a capability
// switch rather than from an agent's allowed_tools. No allowlist will ever
// name them, and that absence is by design — the switch, not a checkbox, is
// what makes them available. The unreachable direction must therefore skip
// them, or it reports a finding that is always present and therefore always
// ignored.
//
// The reason strings are documentation for the next reader; the check only
// consults the keys. Each names the registration site, which is the source of
// truth.
var capabilityRegisteredTools = map[string]string{
	tools.ToolListSandboxFiles: "registerSandboxFileTools (agent_service.go): sandbox file primitive; " +
		"follows the sandbox session-files capability (definitions.go)",
	tools.ToolReadFile: "registerSandboxFileTools (agent_service.go): workspace reads follow the sandbox; " +
		"skill reads follow SkillsEnabled (definitions.go)",
	tools.ToolWriteSandboxFile: "registerSandboxFileTools (agent_service.go): sandbox-only writer; " +
		"absent from AvailableToolDefinitions (definitions.go)",
	tools.ToolEditSandboxFile: "registerSandboxFileTools (agent_service.go): sandbox-only patcher; " +
		"absent from AvailableToolDefinitions (definitions.go)",
	tools.ToolShellExec: "registerSandboxShellTool via registerSandboxShellIfAllowed (agent_service.go): " +
		"remote shell follows SkillsEnabled, install mode or an explicit shell_exec entry in AllowedTools; " +
		"a workspace with script execution off gets a session-scoped local shell under the same entitlement (definitions.go)",
	tools.ToolWriteSkillFile: "registerSkillFileTools (agent_service.go): install-mode only; " +
		"absent from AvailableToolDefinitions (definitions.go)",
	tools.ToolEditSkillFile: "registerSkillFileTools (agent_service.go): install-mode only; " +
		"absent from AvailableToolDefinitions (definitions.go)",
	tools.ToolWebSearch: "registerTools appends it when WebSearchEnabled (agent_service.go); " +
		"not in AvailableToolDefinitions (definitions.go)",
	tools.ToolWebFetch: "registerTools appends it when WebSearchEnabled (agent_service.go); " +
		"not in AvailableToolDefinitions (definitions.go)",
	tools.ToolSearchMemory: "registerTools injects it when MemoryEnabled (agent_service.go); " +
		"not in AvailableToolDefinitions (definitions.go)",
	tools.ToolCallMCPTool: "registerMCPTools (agent_service.go) / tools.RegisterMCPTools: " +
		"capability-scoped, not a tenant-selectable builtin (definitions.go)",
	tools.ToolDiscoverMCPTools: "registerMCPTools (agent_service.go) / tools.RegisterMCPTools: " +
		"capability-scoped, not a tenant-selectable builtin (definitions.go)",
}

// acceptedUnreachableTools are registered tools that are deliberately offered
// to no agent, so the unreachable direction must not report them — but the
// acceptance has to stay visible and bounded, or this table becomes a dustbin
// for anything nobody wanted to delete. Each entry carries its reason, and
// acceptedUnreachableToolsLimit caps the list, mirroring the
// deliberatelyNotWhitelisted table in internal/agent/tools/zettaranc.
//
// Two kinds live here, and nothing else may be added without saying why:
//   - a stub whose Execute always fails, which is a net loss to hand the model;
//   - a tool that is genuinely selectable (listed in AvailableToolDefinitions)
//     but that no agent currently ticks, which is a configuration choice rather
//     than a defect.
var acceptedUnreachableTools = map[string]string{
	"zettaranc.backtest": "回测桩，Execute 恒返回 Success:false（zettaranc/backtest.go）；" +
		"真回测落地后连同本条目一起删除",
	tools.ToolThinking: "AvailableToolDefinitions 里的可选工具，当前无 agent 勾选；" +
		"勾上任一 agent 即被认领",
	tools.ToolTodoWrite: "AvailableToolDefinitions 里的可选工具，agent_service.go 会按需禁用它；" +
		"当前无 agent 勾选",
	tools.ToolDatabaseQuery: "AvailableToolDefinitions 里的可选工具（Label「查询数据库」），" +
		"且不在 DefaultAllowedTools 里 —— 当前无 agent 勾选，属于配置取舍而非缺陷",
}

// acceptedUnreachableToolsLimit is deliberately small. Raising it requires
// editing this number and therefore answering, for the new entry, why the
// model should not be given the tool and why the tool should still exist.
const acceptedUnreachableToolsLimit = 4

// unreachableTools returns registered tools claimed by no agent, minus the
// capability-registered and accepted-unreachable sets, sorted so the finding
// is stable between runs.
//
// A capability tool omitted here is not "unreachable": the runtime registers
// it from a switch, so the model can call it. An accepted tool is unreachable
// on purpose and documented as such in acceptedUnreachableTools. Only what
// remains is a defect worth reporting.
func unreachableTools(registered map[string]bool, claimed map[string][]string) []string {
	out := make([]string, 0, len(registered))
	for name := range registered {
		if len(claimed[name]) > 0 {
			continue
		}
		if _, ok := capabilityRegisteredTools[name]; ok {
			continue
		}
		if _, ok := acceptedUnreachableTools[name]; ok {
			continue
		}
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
