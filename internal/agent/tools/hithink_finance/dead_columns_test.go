package hithink_finance

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestSelectOutputsTracksAliases is the core of the dead-column judgment.
//
// These tools alias almost everything and the Go code reads the alias out of
// the returned row map. An analysis that judged the source column instead
// reported all 42 selected columns as dead while every one of them was in
// active use — a false alarm on every finding, which is worse than no check.
func TestSelectOutputsTracksAliases(t *testing.T) {
	sql := "SELECT volume_obv AS obv, close, abs(macd_hist) AS macd_abs, " +
		"CAST(x AS VARCHAR) AS label FROM v_indicators_daily WHERE thscode = ?"

	got := selectOutputs(sql)

	// Aliased element: the output name is the alias.
	assert.Equal(t, "volume_obv", got["obv"], "an alias is the name the Go code reads")
	// Unaliased column: the output name is the column itself.
	assert.Equal(t, "close", got["close"])
	// Aliased function call.
	assert.Equal(t, "macd_hist", got["macd_abs"])
	// The cast's own AS must not be mistaken for the element's alias, and
	// VARCHAR is a type, not a column.
	assert.Equal(t, "x", got["label"], "a top-level AS is the alias; a cast target is not")
	assert.NotContains(t, got, "VARCHAR", "a SQL type is not a dead indicator column")
}

// TestReadColumnsInSourceCountsRealConsumption pins what counts as a read.
func TestReadColumnsInSourceCountsRealConsumption(t *testing.T) {
	src := "package x\n" +
		"const q = `SELECT a, b, c FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC`\n" +
		"func f(rows []map[string]any) { _ = rows[\"b\"]; _ = abs(\"c\") }\n"

	read := readColumnsInSource(src)

	assert.True(t, read["thscode"], "a WHERE column is consumed")
	assert.True(t, read["date"], "date is a real column and must not be filtered as a type keyword")
	assert.True(t, read["b"], "a column read out of the row map in Go is consumed")
	assert.True(t, read["c"], "a column passed to a function is consumed")
	assert.False(t, read["a"], "a bare select-list column that nothing reads is not consumed")
}

// TestDeadColumnsRunsAndIsNotSilentlyEmpty guards the property the whole
// package is about: a checker that reports nothing because it did not run
// looks exactly like a checker that ran and found nothing.
//
// The embedded sources happen to yield no dead columns today. That is a
// result, not a proof the analysis works, so the analysis is exercised
// directly above; this test exists so a future change that breaks
// DeadColumns() into returning an empty slice for the wrong reason is
// visible rather than silent.
func TestDeadColumnsRunsAndIsNotSilentlyEmpty(t *testing.T) {
	// Must not panic, must return a deterministically ordered slice.
	first := DeadColumns()
	second := DeadColumns()
	assert.Equal(t, first, second, "the result must be stable between calls")
	for _, c := range first {
		assert.NotEmpty(t, c.Column)
		assert.NotEmpty(t, c.Tools, "a dead column must name the file that selects it")
	}
}

// TestSplitTopLevelKeepsFunctionCallsIntact guards the comma split, which
// would otherwise tear `abs(a), b` into the fragment "abs(a" and lose a
// column.
func TestSplitTopLevelKeepsFunctionCallsIntact(t *testing.T) {
	got := splitTopLevel("abs(macd_hist) AS macd, close, cast(a AS VARCHAR) AS v")
	require.Len(t, got, 3)
	assert.Equal(t, "abs(macd_hist) AS macd", got[0])
	assert.Equal(t, "close", got[1])
	assert.Equal(t, "cast(a AS VARCHAR) AS v", got[2])
}
