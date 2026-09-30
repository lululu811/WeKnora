package healthcheck

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/types"
)

// stubMemory stands in for the memory service. It exists to prove check 3
// calls the reconciler's StuckExtractions rather than re-deriving its own
// staleness cutoff: the stub decides what "stuck" means, and the test only
// decides what the checker does with the answer.
type stubMemory struct {
	rows []types.MemoryExtractionSession
	err  error
}

func (s *stubMemory) StuckExtractions(_ context.Context, limit int) ([]types.MemoryExtractionSession, error) {
	if s.err != nil {
		return nil, s.err
	}
	if limit > 0 && len(s.rows) > limit {
		return s.rows[:limit], nil
	}
	return s.rows, nil
}

// TestDeadLetterBacklogSeparatesActiveFromHistorical is the reason check 2
// exists. 782 rows from a DNS incident five days ago and 27 rows from
// last night are the same shape in the database and completely different
// problems: one is a cleanup ticket, the other is an outage. A single
// undifferentiated count forces the reader to make that call, which is the
// call that stops anyone from looking at all.
func TestDeadLetterBacklogSeparatesActiveFromHistorical(t *testing.T) {
	db := newTestDB(t)
	now := time.Date(2026, 9, 30, 13, 0, 0, 0, time.UTC)

	// One row per tier, so all three responses are pinned: outage, recently
	// stopped, and long-dead debris.
	seedDeadLetters(t, db, "memory:extract", 1,
		now.Add(-1*time.Hour), now.Add(-1*time.Hour), "resolve model builtin-llm-default: not found")
	seedDeadLetters(t, db, "summary:generation", 3,
		now.Add(-5*24*time.Hour), now.Add(-5*24*time.Hour), "dial tcp: lookup host failed")
	seedDeadLetters(t, db, "wiki:ingest", 2,
		now.Add(-30*24*time.Hour), now.Add(-30*24*time.Hour), "storage backend unreachable")

	insp := NewWithClock(Config{DB: db, LogFindings: false}, func() time.Time { return now })
	report := insp.Run(context.Background())

	byType := map[string]Finding{}
	for _, f := range report.Findings {
		if f.Check == CheckDeadLetterBacklog {
			byType[f.BadValue] = f
		}
	}
	require.Len(t, byType, 3)

	active := byType["1 rows"]
	assert.Equal(t, SeverityCritical, active.Severity, "a failure an hour old is an outage")
	assert.Contains(t, active.Detail, "CURRENTLY FAILING")
	assert.Contains(t, active.Detail, "resolve model builtin-llm-default",
		"the most recent error is the only thing that turns a count into a diagnosis")

	recent := byType["3 rows"]
	assert.Equal(t, SeverityWarning, recent.Severity)
	assert.Contains(t, recent.Detail, "stopped failing")
	assert.Contains(t, recent.Detail, "dial tcp: lookup host failed")

	debris := byType["2 rows"]
	assert.Equal(t, SeverityInfo, debris.Severity, "a 30-day-old group is debris, not an outage")
	assert.Contains(t, debris.Detail, "historical debris")
	assert.Contains(t, debris.Remediation, "prune or archive")

	// Each finding names its own task type, so a reader can tell which
	// backlog they are looking at without opening the database.
	assert.Contains(t, active.Object, "memory:extract")
	assert.Contains(t, recent.Object, "summary:generation")
	assert.Contains(t, debris.Object, "wiki:ingest")
}

func seedDeadLetters(t *testing.T, db *gorm.DB,
	taskType string, n int, oldest, newest time.Time, lastErr string,
) {
	t.Helper()
	for i := 0; i < n; i++ {
		ts := oldest
		if i == n-1 {
			ts = newest
		}
		require.NoError(t, db.Exec(
			"INSERT INTO task_dead_letters (task_type, last_error, failed_at) VALUES (?, ?, ?)",
			taskType, lastErr, ts,
		).Error)
	}
}

// TestExtractionBacklogUsesTheReconcilersDefinition is check 3's contract.
//
// The stub decides which rows are stuck. The checker must report the
// reconciler's answer and nothing else: if this package carried its own
// staleness cutoff, the number an operator reads and the number the repair
// pass acts on would drift, which is the drift class this package exists to
// catch.
func TestExtractionBacklogUsesTheReconcilersDefinition(t *testing.T) {
	db := newTestDB(t)
	now := time.Date(2026, 9, 30, 13, 0, 0, 0, time.UTC)
	require.NoError(t, db.Exec(
		"INSERT INTO memory_extraction_sessions "+
			"(tenant_id, subject_id, session_id, pending, failure_count, failure_code, updated_at) "+
			"VALUES (1, 'subj-1', 'sess-1', 1, 2, 'lease_lost', ?)",
		now.Add(-3*time.Hour),
	).Error)

	// The reconciler says this row is stuck.
	stuck := []types.MemoryExtractionSession{{
		TenantID: 1, SubjectID: "subj-1", SessionID: "sess-1",
		Pending: true, FailureCount: 2, FailureCode: "lease_lost",
		UpdatedAt: now.Add(-3 * time.Hour),
	}}
	insp := NewWithClock(
		Config{DB: db, MemoryService: &stubMemory{rows: stuck}, LogFindings: false},
		func() time.Time { return now },
	)
	report := insp.Run(context.Background())

	var found *Finding
	for i := range report.Findings {
		if report.Findings[i].Check == CheckExtractionBacklog {
			found = &report.Findings[i]
		}
	}
	require.NotNil(t, found, "a wedged queue must be reported")
	assert.Contains(t, found.Detail, "reconcileStaleAfter",
		"the finding must say which definition of stale it used")
	assert.Contains(t, found.Detail, "subj-1", "must name the affected subject")
	assert.Contains(t, found.Detail, "lease_lost", "must name the failure code")
	assert.Equal(t, "1 pending", found.BadValue)
}

// TestExtractionBacklogIsQuietWhenTheQueueIsDraining guards the other
// direction: a reconciler that reports rows still in flight is a false
// alarm, and a checker that cries wolf is a checker that gets ignored.
func TestExtractionBacklogIsQuietWhenTheQueueIsDraining(t *testing.T) {
	db := newTestDB(t)
	// Rows exist and are pending, but none are stale: the reconciler, which
	// owns the definition, returns nothing.
	require.NoError(t, db.Exec(
		"INSERT INTO memory_extraction_sessions "+
			"(tenant_id, subject_id, session_id, pending, updated_at) "+
			"VALUES (1, 'subj-1', 'sess-1', 1, ?)",
		time.Now(),
	).Error)

	insp := New(Config{DB: db, MemoryService: &stubMemory{}, LogFindings: false})
	report := insp.Run(context.Background())

	for _, f := range report.Findings {
		if f.Check == CheckExtractionBacklog {
			t.Errorf("in-flight row reported as wedged: %s", f)
		}
	}
}

// TestEmptyIsDistinguishableFromDidNotRun is acceptance criterion 5.
//
// A checker that reports nothing when it never ran is the same bug one layer
// up: the original incident produced no error, no alert and no failing test
// while the subsystem was producing nothing. These cases must be
// distinguishable in the report, in the status, and in the log line.
func TestEmptyIsDistinguishableFromDidNotRun(t *testing.T) {
	t.Run("never ran has no report at all", func(t *testing.T) {
		// No pass has completed, so there is no snapshot. A consumer must
		// be able to tell this apart from "ran, found nothing" — the whole
		// difference is that here there is nothing to read.
		insp := New(Config{DB: newTestDB(t)})
		assert.Nil(t, insp.Snapshot(),
			"an inspection that has not run must not hand back a clean-looking report")
	})

	t.Run("a completed pass records that every check ran", func(t *testing.T) {
		db := newTestDB(t)
		insp := New(Config{DB: db, MemoryService: &stubMemory{}, LogFindings: false})
		report := insp.Run(context.Background())
		assert.Equal(t, AllChecks(), report.ChecksRun)
		assert.Empty(t, report.CheckErrors,
			"every check ran, so the empty result means something")
		assert.False(t, report.Incomplete)
		require.NotNil(t, insp.Snapshot(), "a pass stores its verdict")
		assert.Equal(t, report.Status, insp.Snapshot().Status)
	})

	t.Run("a failed check is named, not silently dropped", func(t *testing.T) {
		db := newTestDB(t)
		// Drop the table check 2 reads: that check now knows nothing.
		require.NoError(t, db.Exec("DROP TABLE task_dead_letters").Error)
		report := New(Config{DB: db, MemoryService: &stubMemory{}, LogFindings: false}).
			Run(context.Background())

		assert.Contains(t, report.CheckErrors, CheckDeadLetterBacklog,
			"a check that did not run must be named")
		assert.NotContains(t, report.CheckErrors, CheckDanglingReference)
		assert.True(t, report.Incomplete)
		assert.NotEqual(t, StatusOK, report.Status,
			"an incomplete pass must never be reported as a clean bill of health")
		assert.Contains(t, report.Summary(), "INCOMPLETE")
		assert.Contains(t, report.Summary(), CheckDeadLetterBacklog)
	})
}

// TestReportStatusDerivation pins the status contract directly, so it does
// not depend on what any individual check happens to find.
func TestReportStatusDerivation(t *testing.T) {
	t.Run("complete pass with no findings is ok", func(t *testing.T) {
		r := newReport(time.Now())
		r.finalize()
		assert.Equal(t, StatusOK, r.Status)
		assert.False(t, r.Incomplete)
	})

	t.Run("any finding degrades", func(t *testing.T) {
		r := newReport(time.Now())
		r.Findings = append(r.Findings, Finding{Check: CheckDeadColumns, Severity: SeverityInfo})
		r.finalize()
		assert.Equal(t, StatusDegraded, r.Status)
	})

	t.Run("a check that did not run is unknown, never ok", func(t *testing.T) {
		r := newReport(time.Now())
		r.CheckErrors[CheckDeadLetterBacklog] = "no such table"
		r.finalize()
		assert.Equal(t, StatusUnknown, r.Status)
		assert.True(t, r.Incomplete)
		assert.Equal(t, 0, r.Info, "severity counts stay zero when there are no findings")
	})

	t.Run("severity counts add up", func(t *testing.T) {
		r := newReport(time.Now())
		r.Findings = append(r.Findings,
			Finding{Severity: SeverityCritical},
			Finding{Severity: SeverityCritical},
			Finding{Severity: SeverityWarning},
			Finding{Severity: SeverityInfo},
		)
		r.finalize()
		assert.Equal(t, 2, r.Critical)
		assert.Equal(t, 1, r.Warning)
		assert.Equal(t, 1, r.Info)
		assert.Equal(t, len(r.Findings), r.Critical+r.Warning+r.Info)
	})
}

// TestRunNeverPanics is the failure-posture test.
//
// A bug in the checker must not become the outage it was written to
// prevent, so a panicking check is contained, recorded as "did not run", and
// the pass still reports everything else it found.
func TestRunNeverPanics(t *testing.T) {
	db := newTestDB(t)
	seedModel(t, db, "dead", "dead", "LLM", time.Now())
	seedAgent(t, db, "a1", "Broken Agent", `{"model_id":"dead"}`)

	insp := New(Config{DB: db, LogFindings: false})
	insp.cfg.MaxFindingsPerCheck = 50
	// Force one check to explode.
	stuckLister := &stubMemory{err: errors.New("boom")}
	insp.cfg.MemoryService = stuckLister

	report := insp.Run(context.Background())

	// The failing check is recorded, the rest still reported.
	assert.Contains(t, report.CheckErrors, CheckExtractionBacklog)
	assert.Contains(t, report.CheckErrors[CheckExtractionBacklog], "boom")
	assert.NotEmpty(t, report.Findings, "a broken sibling check must not suppress the rest")
	assert.NotNil(t, insp.Snapshot())
}
