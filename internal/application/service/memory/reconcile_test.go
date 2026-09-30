package memory

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

// This file covers the failure that stayed invisible: a durable queue that
// nothing ever compared against the work queue, so every way a promise could
// be broken looked identical to a promise being kept.
//
// Each test below pins one of those ways. The state they all assert against is
// the same table, so the four cases are deliberately written to be read
// together: pending+no failures means in flight, pending+failures means a run
// failed and will be retried, closed+code+no failed_at means deferred on
// purpose, closed+failed_at means abandoned.

// reconcileHarness wires the extraction path and also hands back the database,
// so a test can age a row the way the passage of time would.
func reconcileHarness(t *testing.T) (
	*Service, *gorm.DB, *stubTenantRepo, *stubMessageRepo, *stubModelService, *stubEnqueuer,
) {
	t.Helper()
	svc, db, tenantRepo := newMemoryHarness(t)
	messages := &stubMessageRepo{}
	models := &stubModelService{}
	enqueuer := &stubEnqueuer{}
	svc.messageRepo = messages
	svc.modelService = models
	svc.enqueuer = enqueuer
	return svc, db, tenantRepo, messages, models, enqueuer
}

const testTenant = uint64(7)

func testScope() interfaces.MemoryScope {
	return interfaces.MemoryScope{TenantID: testTenant, SubjectID: "web_user:alice"}
}

// sessionRows is the durable queue as the database sees it.
func sessionRows(t *testing.T, db *gorm.DB) []types.MemoryExtractionSession {
	t.Helper()
	var rows []types.MemoryExtractionSession
	require.NoError(t, db.Where("tenant_id = ? AND subject_id = ?", testTenant, "web_user:alice").
		Order("session_id ASC").Find(&rows).Error)
	return rows
}

// ageRow backdates everything the reconciler reads, which is what the passage
// of time actually does. The subject's extract_scheduled_at matters as much as
// the row's updated_at: a task that was lost still leaves the subject inside
// the in-flight window for extractInFlightGrace, and the reconciler is right
// to wait that out rather than double the run.
func ageRow(t *testing.T, db *gorm.DB, by time.Duration) {
	t.Helper()
	old := time.Now().Add(-by)
	require.NoError(t, db.Model(&types.MemoryExtractionSession{}).
		Where("tenant_id = ?", testTenant).Update("updated_at", old).Error)
	require.NoError(t, db.Model(&types.MemorySubject{}).
		Where("tenant_id = ?", testTenant).Update("extract_scheduled_at", old).Error)
}

// queuePendingSession writes the row a finished turn would leave behind, the
// way ScheduleExtraction does before it enqueues.
func queuePendingSession(t *testing.T, svc *Service, tenantRepo *stubTenantRepo) {
	t.Helper()
	ctx := enabledCtx(t, tenantRepo, testTenant, "alice")
	// The subject row is what serializes queue mutations, so it exists before
	// the first session row is recorded — same order ScheduleExtraction uses.
	_, err := svc.repo.EnsureSubject(ctx, testScope())
	require.NoError(t, err)
	_, shouldEnqueue, err := svc.repo.EnqueuePendingSession(ctx, testScope(), "session-1", time.Minute)
	require.NoError(t, err)
	require.True(t, shouldEnqueue, "the harness must produce a row that is actually pending")
}

func runExtract(t *testing.T, svc *Service) error {
	t.Helper()
	return svc.Handle(context.Background(), extractTask(t, types.MemoryExtractPayload{
		TenantID: testTenant, SubjectID: "web_user:alice",
		SessionID: "session-1", MessageID: "message-1", ChatModelID: "m1",
	}))
}

// TestRunLevelFailureMarksTheRow is F1. A model id that no longer resolves
// kills the run before any segment exists, so there is no range for the
// segment-level failure recorder to attach to. It used to return the error
// and nothing else, which left the row pending=true with failure_count=0 —
// byte-identical to a row being worked on right now, which is what left 17
// production rows looking healthy while nothing had touched them in days.
func TestRunLevelFailureMarksTheRow(t *testing.T) {
	svc, db, tenantRepo, messages, models, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	messages.messages = []*types.Message{{ID: "msg-1", Role: "user", Content: "我们用 Go"}}
	// Exactly the production failure: the model is configured but will not
	// resolve.
	models.chatModelErr = errors.New("model not found")
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil

	err := runExtract(t, svc)
	require.Error(t, err, "the error must still reach asynq so the task is retried and dead-lettered")
	require.ErrorIs(t, err, errExtractionModelUnavailable)

	rows := sessionRows(t, db)
	require.Len(t, rows, 1)
	require.Equal(t, 1, rows[0].FailureCount,
		"a run that died must leave a counter, or the row is indistinguishable from one in flight")
	require.Equal(t, failureCodeModelUnavailable, rows[0].FailureCode)
	require.True(t, rows[0].Pending, "the turns are still recoverable, so the row stays queued")
	require.Nil(t, rows[0].FailedAt, "a retryable failure is not a terminal one")
}

// TestRunLevelFailureEventuallyClosesTheRow completes the invariant. Without
// a budget, a permanently unresolvable model keeps the row pending forever and
// the reconciler re-queues it on every sweep for the life of the deployment.
func TestRunLevelFailureEventuallyClosesTheRow(t *testing.T) {
	svc, db, tenantRepo, messages, models, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	messages.messages = []*types.Message{{ID: "msg-1", Role: "user", Content: "我们用 Go"}}
	models.chatModelErr = errors.New("model not found")
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil

	for i := 0; i < repository.RunFailureBudget; i++ {
		require.Error(t, runExtract(t, svc))
	}

	rows := sessionRows(t, db)
	require.Len(t, rows, 1)
	require.False(t, rows[0].Pending,
		"a row that has exhausted its budget must stop advertising work nobody will do")
	require.NotNil(t, rows[0].FailedAt, "a terminal failure has to be terminal in the data, not just in a log")
	require.Equal(t, failureCodeModelUnavailable, rows[0].FailureCode)
}

// TestRunFailureAccountingIsDistinctFromInFlight states the invariant the
// production rows violate: the two states must not be able to look alike.
func TestRunFailureAccountingIsDistinctFromInFlight(t *testing.T) {
	svc, db, tenantRepo, messages, models, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	messages.messages = []*types.Message{{ID: "msg-1", Role: "user", Content: "我们用 Go"}}
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil

	inFlight := sessionRows(t, db)[0]
	models.chatModelErr = errors.New("model not found")
	require.Error(t, runExtract(t, svc))
	failed := sessionRows(t, db)[0]

	require.True(t, inFlight.Pending && failed.Pending, "both are still queued, so pending cannot be the signal")
	require.NotEqual(t, inFlight.FailureCount, failed.FailureCount)
	require.NotEqual(t, inFlight.FailureCode, failed.FailureCode)
}

// TestDisabledRunLeavesTheRowInATruthfulState is F2. The handler returns nil
// because a disabled workspace is not a task failure — but returning nil was
// the *only* thing that happened, so asynq marked the task COMPLETED and
// deleted it while the row it had claimed stayed pending=true forever. No dead
// letter, no error, no log line.
func TestDisabledRunLeavesTheRowInATruthfulState(t *testing.T) {
	svc, db, tenantRepo, _, models, _ := reconcileHarness(t)
	// Enabled when the task was queued, switched off before it ran.
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteExplicitOnly})

	require.NoError(t, runExtract(t, svc), "a disabled workspace is not a task failure")
	require.Zero(t, models.callCount(), "a disabled workspace must not pay for a model call")

	rows := sessionRows(t, db)
	require.Len(t, rows, 1)
	require.False(t, rows[0].Pending,
		"an acknowledged task must not leave a row claiming to be in flight; "+
			"pending=true with failure_count=0 is the ambiguous state this closes")
	require.Equal(t, failureCodeSkipAutoExtract, rows[0].FailureCode)
	require.Nil(t, rows[0].FailedAt, "deferred work is not failed work, and must stay resumable")
	require.Zero(t, rows[0].FailureCount)
}

// TestDisabledRunDefersRatherThanConsumes proves the skip is a deferral. The
// cursor is the only record of which turns are unprocessed, so if a skip moved
// it the turns would be gone — silently consumed by a run that deliberately did
// nothing, which is the original bug in a different costume.
func TestDisabledRunDefersRatherThanConsumes(t *testing.T) {
	svc, db, tenantRepo, messages, models, _ := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	ctx := enabledCtx(t, tenantRepo, testTenant, "alice")
	queuePendingSession(t, svc, tenantRepo)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteExplicitOnly})
	require.NoError(t, runExtract(t, svc))

	skipped := sessionRows(t, db)[0]
	require.True(t, skipped.Cursor.At.IsZero(), "a skip must not advance the watermark")

	// Turn the switch back on and let a later turn re-queue the same session.
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	svc.ScheduleExtraction(enabledCtx(t, tenantRepo, testTenant, "alice"), "session-1", "message-1", "m1")
	messages.messages = []*types.Message{{ID: "msg-1", Role: "user", Content: "我们用 Go"}}
	models.response = `{"memories":[{"action":"add","kind":"fact","topic":"语言","content":"用 Go"}]}`
	require.NoError(t, runExtract(t, svc))

	_, total, err := svc.ListItems(ctx, types.MemoryStatusActive, 10, 0)
	require.NoError(t, err)
	require.Equal(t, int64(1), total, "the deferred turn must still be extracted once the switch is back on")
}

// TestSubjectOptOutClosesTheRow covers the second configuration skip, which
// had the same shape: the user's own opt-out left the row pending forever.
func TestSubjectOptOutClosesTheRow(t *testing.T) {
	svc, db, tenantRepo, _, _, _ := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	ctx := enabledCtx(t, tenantRepo, testTenant, "alice")
	queuePendingSession(t, svc, tenantRepo)
	require.NoError(t, svc.SetEnabled(ctx, false))

	require.NoError(t, runExtract(t, svc))

	rows := sessionRows(t, db)
	require.Len(t, rows, 1)
	require.False(t, rows[0].Pending)
	require.Equal(t, failureCodeSkipSubjectOff, rows[0].FailureCode)
	require.Nil(t, rows[0].FailedAt)
}

// TestFailedEnqueueLeavesAMarkedPendingRow is F4. The row is written before
// the task is pushed, so an enqueue that fails leaves durable work with no
// task and nothing to retry it. The old code ignored the returned error, which
// is the only thing that made this invisible: no log line, no counter, no
// reconciler able to tell the row was ever promised anything.
func TestFailedEnqueueLeavesAMarkedPendingRow(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	enqueuer.failWith = errors.New("redis: connection refused")

	svc.ScheduleExtraction(enabledCtx(t, tenantRepo, testTenant, "alice"), "session-1", "message-1", "m1")

	rows := sessionRows(t, db)
	require.Len(t, rows, 1)
	require.True(t, rows[0].Pending, "the turn is still unprocessed, so the row stays queued for a later pass")
	require.Equal(t, 1, rows[0].FailureCount)
	require.Equal(t, failureCodeEnqueueFailed, rows[0].FailureCode,
		"a pending row with no task must say why, or nothing will ever come back for it")
}

// TestReconcileRequeuesAStaleRow is F3. Nothing compared what the table said
// was pending against what the queue was doing, so a row whose task was lost
// stayed pending forever. The repair pass is that missing comparison.
func TestReconcileRequeuesAStaleRow(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil
	ageRow(t, db, 2*time.Hour)
	require.Zero(t, enqueuer.count(), "the task that was supposed to serve this row is gone")

	require.NoError(t, svc.ReconcileExtractions(context.Background()))

	require.Equal(t, 1, enqueuer.count(), "a stale pending row with no task must be requeued")
	require.Equal(t, types.TypeMemoryExtract, enqueuer.tasks[0].Type())
}

// TestReconcileLeavesAFreshInFlightRowAlone is the other half of F3 and the
// test that keeps the repair pass from becoming the bug: requeueing work that
// is already running would double every run the workspace makes.
func TestReconcileLeavesAFreshInFlightRowAlone(t *testing.T) {
	svc, _, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil

	require.NoError(t, svc.ReconcileExtractions(context.Background()))
	require.Zero(t, enqueuer.count(), "a row touched moments ago is being worked on, not stranded")
}

// TestReconcileRespectsTheDebounce is the case that makes updated_at unsafe
// on its own. A run may be scheduled hours out, so its row legitimately looks
// old; the subject's scheduled marker is what distinguishes "waiting for a
// debounce to elapse" from "stranded".
func TestReconcileRespectsTheDebounce(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil
	ageRow(t, db, 2*time.Hour)
	// A run that is debounced into the future owns these rows.
	require.NoError(t, db.Model(&types.MemorySubject{}).
		Where("tenant_id = ?", testTenant).
		Update("extract_scheduled_at", time.Now().Add(45*time.Minute)).Error)

	require.NoError(t, svc.ReconcileExtractions(context.Background()))
	require.Zero(t, enqueuer.count(), "a run that is already scheduled must not be duplicated")
}

// TestReconcileRespectsALiveLease covers the in-flight half of the same
// guard: a worker inside its own model calls holds a lease that outlives the
// staleness window, and the pass must read it rather than guess.
func TestReconcileRespectsALiveLease(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil
	ageRow(t, db, 2*time.Hour)
	require.NoError(t, db.Model(&types.MemorySubject{}).
		Where("tenant_id = ?", testTenant).
		Update("extraction_state", `{"lease_id":"worker-1","lease_until":"`+
			time.Now().Add(5*time.Minute).Format(time.RFC3339Nano)+`"}`).Error)

	require.NoError(t, svc.ReconcileExtractions(context.Background()))
	require.Zero(t, enqueuer.count(), "a leased subject is being worked on right now")
}

// TestReconcileIsSafeUnderConcurrentSweeps runs the repair pass the way a
// multi-replica deployment does. Two passes must not produce two tasks for one
// subject: the subject row serializes the claim, so whichever loses sees the
// slot already taken and does nothing. Run under -race in CI.
func TestReconcileIsSafeUnderConcurrentSweeps(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil
	ageRow(t, db, 2*time.Hour)

	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			// Errors are logged, not returned into the assertion: this test is
			// about the count of tasks produced, and a contended claim is a
			// legitimate outcome for three of these four.
			_ = svc.ReconcileExtractions(context.Background())
		}()
	}
	wg.Wait()

	require.Equal(t, 1, enqueuer.count(),
		"the subject row serializes the claim, so concurrent sweeps produce one task")
}

// TestStuckExtractionsSurfacesTheQueue is the row-visibility half of F3. The
// original failure was never a crash; it was nobody being able to see work
// quietly going nowhere.
func TestStuckExtractionsSurfacesTheQueue(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil

	stuck, err := svc.StuckExtractions(context.Background(), 10)
	require.NoError(t, err)
	require.Empty(t, stuck, "a row claimed seconds ago is not stuck")

	ageRow(t, db, 2*time.Hour)
	stuck, err = svc.StuckExtractions(context.Background(), 10)
	require.NoError(t, err)
	require.Len(t, stuck, 1)
	require.Equal(t, "session-1", stuck[0].SessionID)
	require.Equal(t, testTenant, stuck[0].TenantID)
}

// TestReconcileTaskHandlerRunsThePass covers the shape both task runtimes
// register: a payload-free task whose scope comes from the database.
func TestReconcileTaskHandlerRunsThePass(t *testing.T) {
	svc, db, tenantRepo, _, _, enqueuer := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	queuePendingSession(t, svc, tenantRepo)
	enqueuer.tasks = nil
	ageRow(t, db, 2*time.Hour)

	require.NoError(t, svc.HandleReconcile(context.Background(), nil))
	require.Equal(t, 1, enqueuer.count())
}

// TestReconcileFailsLoudlyWithoutRepositorySupport is the guard on the narrow
// interface. A reconciler that cannot ask the table anything must report that,
// not return success for a repair it never performed.
func TestReconcileFailsLoudlyWithoutRepositorySupport(t *testing.T) {
	svc, _, tenantRepo, _, _, _ := reconcileHarness(t)
	tenantRepo.set(testTenant, &types.MemoryConfig{Enabled: true, WriteMode: types.MemoryWriteAuto})
	svc.repo = bareMemoryRepo{svc.repo}

	require.Error(t, svc.ReconcileExtractions(context.Background()))
	_, err := svc.StuckExtractions(context.Background(), 10)
	require.Error(t, err, "an operator asking what is stuck must be told, not shown an empty list")
}

// bareMemoryRepo hides the failure-accounting capability behind the wide
// read/write interface, standing in for a repository build that predates it.
type bareMemoryRepo struct {
	interfaces.MemoryRepository
}
