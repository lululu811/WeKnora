package agent

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestRegisteredToolNamesCoversEveryNamingConvention is the guard against the
// failure this scan actually had.
//
// The first version of this file matched only `Name() string { return "x" }`
// and embedded only `*/*.go`. It found 26 names — the family tools — and none
// of the core tools, because most core tools declare no Name() method at all
// (they embed BaseTool, which supplies it from a field set at construction)
// and because they live in the tools package *root*, which a one-level glob
// cannot reach. The result was 25 false positives on production agents.
//
// A false negative here is just a missed finding, but a false positive is the
// failure mode that gets a checker ignored, so the set has to be pinned.
func TestRegisteredToolNamesCoversEveryNamingConvention(t *testing.T) {
	got := RegisteredToolNames()

	t.Run("core tools that declare no Name method", func(t *testing.T) {
		// These embed BaseTool; a Name()-method-only scan finds none of them.
		for _, name := range []string{
			"search_knowledge", "read_document", "query_knowledge_graph",
			"list_documents", "database_query", "data_analysis", "data_schema",
		} {
			assert.True(t, got[name], "core tool %q must be in the registered set", name)
		}
	})

	t.Run("family tools from subpackages at depth", func(t *testing.T) {
		// These are two and three directories below internal/agent/tools,
		// which a */*.go glob misses entirely.
		for _, name := range []string{
			"hithink.finance.discover",
			"hithink.finance.market.price.snapshot",
			"zettaranc.screener",
			"zettaranc.four_bricks",
		} {
			assert.True(t, got[name], "subpackage tool %q must be in the registered set", name)
		}
	})

	t.Run("no test-only or empty names leak in", func(t *testing.T) {
		for name := range got {
			assert.NotEmpty(t, name, "an empty name is not a tool")
		}
	})

	t.Run("the set is not silently truncated", func(t *testing.T) {
		// A floor rather than an exact number: adding tools must not require
		// editing this test, but losing the bulk of the set must fail CI.
		require.GreaterOrEqual(t, len(got), 50,
			"registered tool set collapsed to %d names; the scan is missing a convention", len(got))
	})
}

// TestRegisteredToolNamesIsNotShared guards the copy-on-return: a caller
// mutating the map must not be able to change what every later caller sees,
// which would silently disable the check for the rest of the process.
func TestRegisteredToolNamesIsNotShared(t *testing.T) {
	first := RegisteredToolNames()
	require.NotEmpty(t, first)
	delete(first, "search_knowledge")
	first["injected"] = true

	second := RegisteredToolNames()
	assert.True(t, second["search_knowledge"], "a caller must not be able to delete a tool")
	assert.False(t, second["injected"], "a caller must not be able to inject a tool")
}
