package healthcheck

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// testDDL is the minimal schema the checks read. It mirrors the columns
// production uses, including the soft-delete markers, because "row is gone"
// and "row is soft-deleted" are different findings and a schema without
// deleted_at cannot tell them apart.
const testDDL = `
CREATE TABLE models (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT 0,
  deleted_at DATETIME
);
CREATE TABLE custom_agents (
  id TEXT NOT NULL,
  name TEXT NOT NULL,
  tenant_id INTEGER NOT NULL,
  config TEXT NOT NULL DEFAULT '{}',
  deleted_at DATETIME
);
CREATE TABLE tenants (
  id INTEGER PRIMARY KEY,
  memory_config TEXT,
  deleted_at DATETIME
);
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  rerank_model_id TEXT,
  summary_model_id TEXT,
  deleted_at DATETIME
);
CREATE TABLE task_dead_letters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT NOT NULL,
  last_error TEXT,
  fail_count INTEGER NOT NULL DEFAULT 0,
  failed_at DATETIME NOT NULL
);
CREATE TABLE memory_extraction_sessions (
  tenant_id INTEGER NOT NULL,
  subject_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  pending BOOLEAN NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  failure_code TEXT NOT NULL DEFAULT '',
  updated_at DATETIME NOT NULL
);
`

func newTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.Exec(testDDL).Error)
	return db
}

func seedModel(t *testing.T, db *gorm.DB, id, name, modelType string, deletedAt any) {
	t.Helper()
	require.NoError(t, db.Exec(
		"INSERT INTO models (id, name, type, deleted_at) VALUES (?, ?, ?, ?)",
		id, name, modelType, deletedAt,
	).Error)
}

func seedAgent(t *testing.T, db *gorm.DB, id, name, config string) {
	t.Helper()
	require.NoError(t, db.Exec(
		"INSERT INTO custom_agents (id, name, tenant_id, config) VALUES (?, ?, 1, ?)",
		id, name, config,
	).Error)
}

// TestDanglingReferenceFindsTheSmartReasoningBreakage is acceptance
// criterion 1.
//
// This is the one test that matters most in the package. It seeds the exact
// state that is sitting in the production database right now — the agent
// named "Smart Reasoning" whose config->>'model_id' is "builtin-llm-default",
// a model row that exists but is soft-deleted — and asserts the check
// reports it. If this test cannot detect that state, the package has failed
// regardless of how clean the rest of it is.
func TestDanglingReferenceFindsTheSmartReasoningBreakage(t *testing.T) {
	db := newTestDB(t)
	// The model row still exists; it is soft-deleted. This is the exact
	// shape of the production row (deleted_at 2026-09-25T08:03:10Z).
	seedModel(t, db, "builtin-llm-default", "qwen3.7-plus", "LLM",
		time.Date(2026, 9, 25, 8, 3, 10, 0, time.UTC))
	// A healthy model, so the check is proving something about the broken
	// reference and not about an empty models table.
	seedModel(t, db, "builtin-qwen-plus", "qwen-plus", "LLM", nil)

	seedAgent(t, db, "builtin-smart-reasoning", "Smart Reasoning",
		`{"model_id":"builtin-llm-default","thinking":false,"asr_model_id":"","vlm_model_id":"",`+
			`"allowed_tools":["knowledge_search","grep_chunks"]}`)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	require.Equal(t, StatusDegraded, report.Status)
	require.NotEmpty(t, report.Findings)

	var found *Finding
	for i := range report.Findings {
		if report.Findings[i].Check == CheckDanglingReference {
			found = &report.Findings[i]
			break
		}
	}
	require.NotNil(t, found, "the soft-deleted model reference must be reported")

	// Every field an operator needs to act, per the design constraint.
	assert.Equal(t, SeverityCritical, found.Severity)
	assert.Contains(t, found.Object, "Smart Reasoning", "must name the agent")
	assert.Contains(t, found.Object, "builtin-smart-reasoning", "must name the agent id")
	assert.Equal(t, "config->>'model_id'", found.Field, "must name the config path")
	assert.Equal(t, "builtin-llm-default", found.BadValue, "must name the bad value verbatim")
	assert.Contains(t, found.Detail, "soft-deleted", "must say what is wrong")
	assert.Contains(t, found.Detail, "2026-09-25T08:03:10Z", "must name when it was deleted")
	// "What it should be" — the swap is nameable because the deleted row
	// still knows its own type.
	assert.Contains(t, found.Remediation, "builtin-qwen-plus",
		"must name a live model to repoint at")
}

// TestDanglingReferenceDistinguishesMissingFromSoftDeleted pins the
// distinction the production query depends on: a row that is gone and a row
// that is soft-deleted produce different words, because only one of them
// can be fixed by restoring the model.
func TestDanglingReferenceDistinguishesMissingFromSoftDeleted(t *testing.T) {
	db := newTestDB(t)
	seedModel(t, db, "gone-somewhere", "some-model", "LLM",
		time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	seedAgent(t, db, "a1", "Soft Deleted Agent", `{"model_id":"gone-somewhere"}`)
	seedAgent(t, db, "a2", "Never Existed Agent", `{"model_id":"never-existed-at-all"}`)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	byObject := map[string]Finding{}
	for _, f := range report.Findings {
		if f.Check == CheckDanglingReference {
			byObject[f.Object] = f
		}
	}
	require.Contains(t, byObject, "custom_agents:Soft Deleted Agent (id=a1)")
	require.Contains(t, byObject, "custom_agents:Never Existed Agent (id=a2)")

	assert.Contains(t, byObject["custom_agents:Soft Deleted Agent (id=a1)"].Detail, "soft-deleted")
	assert.Contains(t, byObject["custom_agents:Never Existed Agent (id=a2)"].Detail,
		"no row with this id exists")
}

// TestDanglingReferenceIgnoresUnsetReferences is acceptance criterion 2: the
// false-positive test.
//
// Empty string, JSON null and an absent key all mean "this configuration
// does not name a model", and the runtime falls back to a default in all
// three cases. Reporting them would bury the one real defect under a column
// of noise and train operators to ignore the check — the exact failure mode
// the package exists to eliminate.
func TestDanglingReferenceIgnoresUnsetReferences(t *testing.T) {
	db := newTestDB(t)
	seedModel(t, db, "live-model", "live", "LLM", nil)

	// Every unset shape, across every source the check reads.
	seedAgent(t, db, "a1", "All Empty Strings", `{"model_id":"","rerank_model_id":"",`+
		`"asr_model_id":"","vlm_model_id":"","query_understand_model_id":""}`)
	seedAgent(t, db, "a2", "JSON Nulls", `{"model_id":null,"asr_model_id":null}`)
	seedAgent(t, db, "a3", "Keys Absent", `{"agent_mode":"smart-reasoning","temperature":0.7}`)
	seedAgent(t, db, "a4", "Whitespace Only", `{"model_id":"   ","vlm_model_id":"\t"}`)
	seedAgent(t, db, "a5", "Empty Config", `{}`)
	seedAgent(t, db, "a6", "Valid Reference", `{"model_id":"live-model"}`)

	require.NoError(t, db.Exec(
		"INSERT INTO tenants (id, memory_config) VALUES (7, ?)",
		`{"enabled":true,"extract_model_id":"","embedding_model_id":null}`,
	).Error)
	require.NoError(t, db.Exec(
		"INSERT INTO sessions (id, rerank_model_id, summary_model_id) VALUES ('s1', '', '')",
	).Error)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	for _, f := range report.Findings {
		if f.Check != CheckDanglingReference {
			continue
		}
		t.Errorf("unset reference reported as a defect: %s", f)
	}

	// And the check genuinely ran: it is not passing by not executing.
	assert.NotContains(t, report.CheckErrors, CheckDanglingReference,
		"the check must have run; 'no findings' only means something if it did")

	n := 0
	for _, f := range report.Findings {
		if f.Check == CheckDanglingReference {
			n++
		}
	}
	assert.Equal(t, 0, n, "a fully healthy configuration must produce zero findings")
}

// TestDanglingReferenceCoversEveryConfiguredSource proves the check is not
// pointed at just the one field that happened to break. Each source carries
// its own dangling reference and every one must be named.
func TestDanglingReferenceCoversEveryConfiguredSource(t *testing.T) {
	db := newTestDB(t)
	seedModel(t, db, "dead-llm", "dead", "LLM",
		time.Date(2026, 9, 25, 8, 3, 10, 0, time.UTC))

	seedAgent(t, db, "a1", "Agent With Everything Broken",
		`{"model_id":"dead-llm","rerank_model_id":"dead-llm","asr_model_id":"dead-llm",`+
			`"vlm_model_id":"dead-llm","query_understand_model_id":"dead-llm"}`)
	require.NoError(t, db.Exec(
		"INSERT INTO tenants (id, memory_config) VALUES (42, ?)",
		`{"extract_model_id":"dead-llm","embedding_model_id":"dead-llm"}`,
	).Error)
	require.NoError(t, db.Exec(
		"INSERT INTO sessions (id, rerank_model_id, summary_model_id) "+
			"VALUES ('s1', 'dead-llm', 'dead-llm')",
	).Error)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	seen := map[string]bool{}
	for _, f := range report.Findings {
		if f.Check == CheckDanglingReference {
			seen[f.Field] = true
		}
	}
	for _, field := range []string{
		"config->>'model_id'",
		"config->>'rerank_model_id'",
		"config->>'asr_model_id'",
		"config->>'vlm_model_id'",
		"config->>'query_understand_model_id'",
		"memory_config->>'extract_model_id'",
		"memory_config->>'embedding_model_id'",
		"rerank_model_id",
		"summary_model_id",
	} {
		assert.True(t, seen[field], "field %s must be covered by the dangling-reference check", field)
	}
}

// TestDanglingReferenceIgnoresSoftDeletedOwners keeps historical debris out
// of the report. A deleted agent's stale model id can never be used again,
// so reporting it is noise of the same kind as reporting an empty string.
func TestDanglingReferenceIgnoresSoftDeletedOwners(t *testing.T) {
	db := newTestDB(t)
	seedModel(t, db, "dead-llm", "dead", "LLM",
		time.Date(2026, 9, 25, 8, 3, 10, 0, time.UTC))
	require.NoError(t, db.Exec(
		"INSERT INTO custom_agents (id, name, tenant_id, config, deleted_at) "+
			"VALUES ('gone', 'Retired Agent', 1, ?, ?)",
		`{"model_id":"dead-llm"}`, time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	).Error)

	insp := New(Config{DB: db, LogFindings: false})
	report := insp.Run(context.Background())

	for _, f := range report.Findings {
		if f.Check == CheckDanglingReference {
			t.Errorf("soft-deleted agent reported: %s", f)
		}
	}
}
