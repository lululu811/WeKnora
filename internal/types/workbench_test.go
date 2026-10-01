package types

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestResolveWorkbench(t *testing.T) {
	t.Run("registered id passes through", func(t *testing.T) {
		id, downgraded := ResolveWorkbench("finance")
		assert.Equal(t, "finance", id)
		assert.False(t, downgraded)
	})

	t.Run("empty normalises to no workbench", func(t *testing.T) {
		id, downgraded := ResolveWorkbench("")
		assert.Equal(t, NoWorkbench, id)
		assert.False(t, downgraded, "an empty tag is the normal pre-workbench case, not a downgrade")
	})

	t.Run("shared is preserved", func(t *testing.T) {
		id, downgraded := ResolveWorkbench(WorkbenchShared)
		assert.Equal(t, WorkbenchShared, id)
		assert.False(t, downgraded)
	})

	t.Run("unknown degrades instead of passing through", func(t *testing.T) {
		// This is the invariant that keeps a mistyped JSONB tag from rendering a
		// permanently blank dock with no diagnostic.
		id, downgraded := ResolveWorkbench("financ") // typo
		assert.Equal(t, NoWorkbench, id)
		assert.True(t, downgraded, "caller needs the flag so it can log the downgrade")
	})
}

func TestIsSharedAgent(t *testing.T) {
	assert.True(t, IsSharedAgent(WorkbenchShared))
	assert.False(t, IsSharedAgent("finance"))
	assert.False(t, IsSharedAgent(""))
}

func TestListWorkbenchesIsStableAndImmutable(t *testing.T) {
	first := ListWorkbenches()
	second := ListWorkbenches()
	require.NotEmpty(t, first)
	assert.Equal(t, first, second, "ordering must be deterministic across calls")

	// Mutating a returned slice must not corrupt the registry.
	first[0].Components = append(first[0].Components, "injected")
	assert.NotContains(t, ListWorkbenches()[0].Components, "injected")
}

func TestFilterToolsByWorkbench(t *testing.T) {
	t.Run("workbench without an allowlist is a pass-through", func(t *testing.T) {
		// finance ships with AllowedTools == nil by design.
		agentTools := []string{"kb_search", "zettaranc.screener"}
		assert.Equal(t, agentTools, FilterToolsByWorkbench(agentTools, "finance"))
	})

	t.Run("unknown workbench is a pass-through, not a wipe", func(t *testing.T) {
		agentTools := []string{"kb_search"}
		assert.Equal(t, agentTools, FilterToolsByWorkbench(agentTools, "nope"))
	})

	// A second workbench has to be injected to exercise the intersection: the
	// shipped registry only contains "finance", whose allowlist is nil, which
	// makes the filter an identity function in production. See R2 in the
	// workbench design doc.
	t.Run("intersects against a restricted workbench", func(t *testing.T) {
		restore := installTestWorkbench(t, WorkbenchDef{
			ID:           "code",
			DisplayName:  "workbench.code.name",
			Components:   []string{"editor"},
			AllowedTools: []string{"code_exec", "fs_read"},
		})
		defer restore()

		got := FilterToolsByWorkbench([]string{"code_exec", "kb_search"}, "code")
		assert.Equal(t, []string{"code_exec"}, got, "kb_search is outside the code workbench")

		assert.Equal(t, []string{"fs_read"}, FilterToolsByWorkbench([]string{"fs_read"}, "code"))
		assert.Empty(t, FilterToolsByWorkbench([]string{"kb_search"}, "code"))
	})
}

// installTestWorkbench registers an extra workbench for the duration of one
// test and returns a function that restores the previous registry.
func installTestWorkbench(t *testing.T, def WorkbenchDef) func() {
	t.Helper()
	previous, existed := workbenchRegistry[def.ID]
	workbenchRegistry[def.ID] = def
	return func() {
		if existed {
			workbenchRegistry[def.ID] = previous
			return
		}
		delete(workbenchRegistry, def.ID)
	}
}
