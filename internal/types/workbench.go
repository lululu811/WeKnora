package types

import "sort"

// WorkbenchDef describes a workbench ("工作台") — a business domain that groups
// agents and the UI components their chats render.
//
// Two-layer vocabulary (do NOT collapse these into one namespace):
//   - Workbench (this file): the business domain, e.g. "finance". Stored on the
//     agent as `config.workbench`.
//   - Component: a UI implementation registered in the frontend registry, e.g.
//     "kline". Referenced by Components below.
//
// The backend is the single source of truth for this vocabulary: the frontend
// keeps only component *implementations* and fetches the mapping from
// `GET /system/workspaces`. That makes Go/TS vocabulary drift structurally
// impossible, at the cost of requiring a release to add a workbench — which is
// honest, because the components themselves are code anyway.
type WorkbenchDef struct {
	// ID is the stable identifier stored on `agent.config.workbench`.
	ID string `json:"id"`
	// DisplayName is an i18n key, not a literal label.
	DisplayName string `json:"display_name"`
	// Description is an optional i18n key explaining the workbench's purpose.
	Description string `json:"description,omitempty"`
	// Components are frontend registry keys this workbench may render.
	Components []string `json:"components"`
	// AllowedTools is the tool allowlist for this domain. Empty means "no
	// workbench-level restriction on top of the agent's own AllowedTools".
	//
	// NOTE: this cannot gate sandbox/shell tools. Those are registered from
	// backend capability and deliberately bypass the per-agent allowlist
	// (internal/agent/tools/definitions.go). A per-agent execution switch is
	// still missing — see the workbench design doc, risk R3.
	AllowedTools []string `json:"allowed_tools"`
}

// WorkbenchShared marks an agent that is available in every workbench.
//
// Such an agent can never own a session: a session binds to exactly one agent
// (its first message's agent), and that binding is what the workbench is
// derived from. A session started on a shared agent would have no workbench and
// therefore no panels. The rule is enforced in the UI by excluding shared
// agents from the "new session" agent picker.
const WorkbenchShared = "shared"

// NoWorkbench is the normalised id for "this agent belongs to no workbench".
// Agents that predate the workbench field carry an empty tag and normalise to
// this value, so they keep rendering exactly as they did before.
const NoWorkbench = ""

// workbenchRegistry is the authoritative workbench vocabulary.
//
// To add a workbench: register it here AND implement every component key in
// frontend/src/components/workspace/registry.ts. A component key with no
// implementation renders an empty dock — do not register one you have not built.
var workbenchRegistry = map[string]WorkbenchDef{
	"finance": {
		ID:           "finance",
		DisplayName:  "workbench.finance.name",
		Description:  "workbench.finance.description",
		Components:   []string{"kline"},
		AllowedTools: nil, // no workbench-level restriction yet
	},
}

// ListWorkbenches returns every registered workbench, sorted by ID for a stable
// response order.
func ListWorkbenches() []WorkbenchDef {
	ids := make([]string, 0, len(workbenchRegistry))
	for id := range workbenchRegistry {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	out := make([]WorkbenchDef, 0, len(ids))
	for _, id := range ids {
		def := workbenchRegistry[id]
		// Copy the slices so callers cannot mutate the registry.
		def.Components = append([]string(nil), def.Components...)
		def.AllowedTools = append([]string(nil), def.AllowedTools...)
		out = append(out, def)
	}
	return out
}

// GetWorkbench looks a workbench up by ID.
func GetWorkbench(id string) (WorkbenchDef, bool) {
	def, ok := workbenchRegistry[id]
	if !ok {
		return WorkbenchDef{}, false
	}
	def.Components = append([]string(nil), def.Components...)
	def.AllowedTools = append([]string(nil), def.AllowedTools...)
	return def, true
}

// IsSharedAgent reports whether the given raw workbench tag marks a shared agent.
func IsSharedAgent(rawWorkbench string) bool {
	return rawWorkbench == WorkbenchShared
}

// ResolveWorkbench normalises a workbench tag read from `agent.config.workbench`.
//
// Unknown values degrade to NoWorkbench instead of being passed through. This
// matters because the tag lives in a JSONB column: it can be hand-edited, set
// through a raw API call, or simply mistyped, and a value the registry does not
// know about would otherwise render a permanently blank dock with no diagnostic
// anywhere.
//
// The second return value reports whether a downgrade happened so the caller can
// log it. Logging is left to the caller on purpose: this package stays free of a
// logger dependency.
func ResolveWorkbench(rawWorkbench string) (id string, downgraded bool) {
	if rawWorkbench == "" || rawWorkbench == WorkbenchShared {
		return rawWorkbench, false
	}
	if _, ok := workbenchRegistry[rawWorkbench]; ok {
		return rawWorkbench, false
	}
	return NoWorkbench, true
}

// FilterToolsByWorkbench intersects an agent's own tool allowlist with the
// workbench's domain allowlist.
//
// An empty workbench allowlist means the workbench imposes no extra restriction,
// so the agent's own list passes through untouched. A nil/empty agent list is
// also treated as "unrestricted by the agent" and returned as-is, leaving the
// caller's default-tool resolution in charge.
//
// TODO(workbench): with only one workbench registered this is an identity
// function and is therefore NOT exercised by any test. It must be covered by an
// integration test once a second workbench (e.g. "code") is registered — do not
// read a passing build as evidence that isolation works.
func FilterToolsByWorkbench(agentTools []string, workbenchID string) []string {
	def, ok := GetWorkbench(workbenchID)
	if !ok || len(def.AllowedTools) == 0 {
		return agentTools
	}
	if len(agentTools) == 0 {
		return agentTools
	}

	allowed := make(map[string]struct{}, len(def.AllowedTools))
	for _, t := range def.AllowedTools {
		allowed[t] = struct{}{}
	}

	out := make([]string, 0, len(agentTools))
	for _, t := range agentTools {
		if _, ok := allowed[t]; ok {
			out = append(out, t)
		}
	}
	return out
}
