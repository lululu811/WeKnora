package memory

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/hibiken/asynq"
)

// This file is the answer to a failure that stayed invisible.
//
// memory_extraction_sessions is a durable queue: a row written pending=true
// is a promise that something will read it. Until now the only reader was
// ClaimPendingSessions, called from exactly one place — the worker handler
// itself — so the table and the task queue were never compared. Every way a
// promise could be broken (a run that dies before its first checkpoint, a
// disabled workspace that ACKs the task, an enqueue that fails after the row
// is written) left the row pending=true forever with failure_count=0, which is
// byte-for-byte what a row that is genuinely mid-run looks like. Production
// ended up with 27 dead-lettered memory:extract tasks and 17 rows that had
// produced nothing and claimed to be working on it.
//
// The fix has two halves and they only work together: a failure has to leave a
// mark (see MarkExtractionRunFailure's contract in the repository), and
// something has to come back and look (see ReconcileExtractions below).

// TypeReconcile is the periodic repair pass. It carries no payload and takes
// no scope: the reconciler finds its own work by asking the table what has
// gone stale, which is the only place the truth lives.
const TypeReconcile = "memory:reconcile"

// ReconcileInterval is how often the repair pass runs.
//
// It is deliberately slower than the debounce that produces work
// (types.DefaultMemoryExtractDelaySeconds is 90s): a pass that ran at the
// same cadence as normal traffic would have nothing to do. The cost of a
// slower pass is bounded — stale work is delayed, never lost — so this is
// traded for the reconciler never competing with the work it repairs.
const ReconcileInterval = 5 * time.Minute

const (
	// reconcileStaleAfter is how long a pending row may sit untouched before
	// the reconciler assumes the queue and the table have diverged.
	//
	// It must exceed extractInFlightGrace, which is the longest a worker may
	// legitimately hold a claim (the lease TTL), or the pass would declare a
	// worker that is still inside its own model call to have abandoned its
	// work. Doubling it leaves room for a slow final segment on top of that.
	//
	// It must NOT be raised to cover the scheduling debounce. A run may be
	// queued up to MaxMemoryExtractDelaySeconds out, so a row's updated_at
	// can legitimately be hours old. Those are not stale, and the reconciler
	// does not guess: it reads the subject's extract_scheduled_at and skips
	// any scope with a run still scheduled to arrive.
	reconcileStaleAfter = 2 * extractInFlightGrace
	// reconcileSubjectLimit bounds one pass to the subjects that have been
	// waiting longest. The remainder is picked up by the next tick rather
	// than being swept all at once, so repairing a large backlog never turns
	// into one long task holding a worker.
	reconcileSubjectLimit = 20
	// reconcileInFlightTimeout is the same guard EnqueuePendingSession uses
	// on the normal scheduling path: a run already claimed or already queued
	// for this subject must not be duplicated by the repair pass.
	reconcileInFlightTimeout = extractInFlightGrace
)

// Failure codes written to memory_extraction_sessions.failure_code.
//
// They are short, stable tokens rather than error text on purpose. The column
// is VARCHAR(64) and its only job is to be GROUP BY-ed by whoever is triaging;
// the wrapped message ("get extraction model: model not found") both overflows
// that and changes whenever the underlying provider rewords its error, which
// would make the one field meant for triage useless exactly when it is needed.
const (
	failureCodeRunFailed        = "run_failed"
	failureCodeModelUnavailable = "model_unavailable"
	failureCodeLeaseLost        = "lease_lost"
	failureCodeEnqueueFailed    = "enqueue_failed"
	// failureCodeSkipAutoExtract and failureCodeSkipSubjectOff mark work that
	// was deliberately not run because a switch was off. They read as skips
	// precisely because they are not failures: failure_count stays 0 and
	// failed_at stays NULL, so the deferred turns can be resumed later.
	failureCodeSkipAutoExtract = "skipped_auto_extract_off"
	failureCodeSkipSubjectOff  = "skipped_subject_disabled"
)

// Sentinels that give a run-level error a stable identity. The code is chosen
// from these, not from the message, so triage survives a provider rewording
// its errors.
var (
	// errExtractionModelUnavailable covers both "no model is configured" and
	// "the configured model does not resolve". Production hit the second one:
	// 27 tasks dead-lettered with "model not found" while every row they had
	// claimed stayed pending=true with failure_count=0.
	errExtractionModelUnavailable = errors.New("memory extraction model unavailable")
	errEnqueueExtraction          = errors.New("enqueue memory extraction")
)

// runFailureCode maps an error to the token stored in failure_code.
func runFailureCode(err error) string {
	switch {
	case errors.Is(err, errExtractionModelUnavailable):
		return failureCodeModelUnavailable
	case errors.Is(err, errEnqueueExtraction):
		return failureCodeEnqueueFailed
	case errors.Is(err, types.ErrMemoryExtractionLeaseLost):
		return failureCodeLeaseLost
	default:
		return failureCodeRunFailed
	}
}

// extractionMaintenance is the slice of the repository that only failure
// accounting and the reconciler need.
//
// It is a separate interface rather than three more methods on
// interfaces.MemoryRepository so that the read paths — recall, search, the
// memory manager — cannot reach a write that closes someone's queue. Every
// call site checks the assertion and reports loudly when it fails; none of
// them treats a missing implementation as "nothing to do", because a silent
// no-op here is the exact failure mode this file was written to end.
type extractionMaintenance interface {
	// MarkExtractionRunFailure stamps a failure that killed a run before any
	// segment could be checkpointed. An empty leaseID is allowed for callers
	// outside a worker, and the repository refuses them while another run
	// holds the subject.
	MarkExtractionRunFailure(ctx context.Context, scope interfaces.MemoryScope, leaseID, code string) error
	// ClosePendingExtraction takes pending rows out of the queue without
	// advancing their cursor, so the turns are deferred rather than dropped.
	ClosePendingExtraction(ctx context.Context, scope interfaces.MemoryScope, leaseID, code string) (int64, error)
	// ListStuckExtractions returns pending rows untouched since a cutoff.
	ListStuckExtractions(ctx context.Context, staleBefore time.Time, limit int) ([]types.MemoryExtractionSession, error)
}

// runState is what one extraction attempt owns, kept where the failure path
// can see it. The lease id is empty until the claim succeeds, which is what
// lets a pre-claim failure be recorded without pretending to hold the subject.
type runState struct {
	scope   interfaces.MemoryScope
	leaseID string
	// marked records that this run's failure has already been stamped, so the
	// outer unwind and the lease-holding unwind cannot both write.
	marked bool
}

// maintenance resolves the optional repository capability, or explains in the
// log exactly which invariant is now unprotected. It never returns nil
// quietly: callers must handle the error, because a silent skip here is the
// difference between "this row says what went wrong" and "this row says
// nothing ever happened", which is the state production spent months in.
func (s *Service) maintenance() (extractionMaintenance, error) {
	if m, ok := s.repo.(extractionMaintenance); ok {
		return m, nil
	}
	return nil, fmt.Errorf(
		"memory repository %T does not implement extraction failure accounting; "+
			"rows would stay pending=true with failure_count=0 and no reconciler could tell "+
			"a stuck subject from one that is working", s.repo,
	)
}

// markRunFailure stamps a run-level failure onto the scope's pending rows.
//
// The original error is deliberately not touched: the caller still returns it,
// so asynq still retries and still dead-letters, and this marker only adds the
// durable record the task queue does not keep. A run that loses its lease
// stamps nothing — another worker owns the subject now, and its state is the
// truthful one.
func (s *Service) markRunFailure(ctx context.Context, rs *runState, cause error) {
	if rs == nil || cause == nil {
		return
	}
	maint, err := s.maintenance()
	if err != nil {
		logger.Errorf(ctx, "memory: cannot record extraction run failure for subject %s: %v",
			rs.scope.SubjectID, err)
		return
	}
	code := runFailureCode(cause)
	if err := maint.MarkExtractionRunFailure(ctx, rs.scope, rs.leaseID, code); err != nil {
		if errors.Is(err, types.ErrMemoryExtractionLeaseLost) {
			logger.Infof(ctx,
				"memory: extraction lease for subject %s moved on before the run failure could be recorded",
				rs.scope.SubjectID)
			return
		}
		logger.Errorf(ctx, "memory: recording extraction run failure (%s) for subject %s failed: %v",
			code, rs.scope.SubjectID, err)
		return
	}
	rs.marked = true
	logger.Warnf(ctx, "memory: extraction run for subject %s failed at the run level (%s); %v",
		rs.scope.SubjectID, code, cause)
}

// closePendingWork takes a scope's pending rows out of the queue without
// moving the cursor, so the turns are deferred and can resume later.
//
// It is used when a switch is off. Returning nil from the handler in that case
// is correct — a disabled workspace is not a task failure — but it used to be
// *only* that, which acknowledged the task while leaving the row pending=true
// forever: no dead letter, no error, no log line, and a subject whose queue
// claimed work that nothing would ever come back for.
func (s *Service) closePendingWork(ctx context.Context, scope interfaces.MemoryScope, code string) {
	maint, err := s.maintenance()
	if err != nil {
		logger.Errorf(ctx, "memory: cannot close deferred extraction work for subject %s: %v",
			scope.SubjectID, err)
		return
	}
	closed, err := maint.ClosePendingExtraction(ctx, scope, "", code)
	if err != nil {
		if errors.Is(err, types.ErrMemoryExtractionLeaseLost) {
			return // A run is live; it owns the truth about these rows.
		}
		logger.Errorf(ctx, "memory: closing deferred extraction work for subject %s failed: %v",
			scope.SubjectID, err)
		return
	}
	if closed > 0 {
		logger.Infof(ctx,
			"memory: deferred %d pending extraction row(s) for subject %s (%s); "+
				"cursors are untouched, so the turns are re-queued when the switch is turned back on",
			closed, scope.SubjectID, code)
	}
}

// ReconcileExtractions re-queues work the queue has lost.
//
// memory_extraction_sessions is the durable queue, but it has no reader other
// than the worker handler, so nothing ever noticed the 17 rows that the 27
// dead-lettered tasks left behind. This pass is that missing reader: it asks
// the table which pending rows have not been touched in a while, and asks the
// subject whether anything is still scheduled to come for them. A row that
// fails both tests has no task and no worker, and is re-queued here.
//
// It returns an error when the repository cannot support it, rather than
// reporting success for a repair that did not happen — a reconciler that
// fails quietly is the same silent failure one layer up.
func (s *Service) ReconcileExtractions(ctx context.Context) error {
	maint, err := s.maintenance()
	if err != nil {
		return err
	}
	rows, err := maint.ListStuckExtractions(ctx, time.Now().Add(-reconcileStaleAfter), reconcileSubjectLimit)
	if err != nil {
		return fmt.Errorf("list stuck memory extraction rows: %w", err)
	}
	if len(rows) == 0 {
		return nil
	}

	// One pass visits each subject once, however many of its sessions are
	// stuck: a single task drains the whole batch, so re-queueing per row
	// would only multiply duplicate work.
	type target struct {
		scope     interfaces.MemoryScope
		sessionID string
	}
	seen := make(map[interfaces.MemoryScope]struct{}, len(rows))
	targets := make([]target, 0, len(rows))
	for _, row := range rows {
		scope := interfaces.MemoryScope{TenantID: row.TenantID, SubjectID: row.SubjectID}
		if !scope.Valid() {
			continue
		}
		if _, dup := seen[scope]; dup {
			continue
		}
		seen[scope] = struct{}{}
		targets = append(targets, target{scope: scope, sessionID: row.SessionID})
	}

	requeued, busy := 0, 0
	for _, t := range targets {
		subject, err := s.repo.GetSubject(ctx, t.scope)
		if err != nil {
			logger.Warnf(ctx, "memory: reconcile could not load subject %s: %v", t.scope.SubjectID, err)
			continue
		}
		if subject == nil {
			continue
		}
		// A live lease means a worker is inside its own model calls right
		// now. A scheduled run in the future means the task exists and simply
		// has not been picked up yet — a run may be debounced hours out, and
		// re-queueing it here would double every run the workspace makes.
		// Neither is stale, however old updated_at looks.
		now := time.Now()
		if subject.ExtractionState.LeaseID != "" && subject.ExtractionState.LeaseUntil.After(now) {
			busy++
			continue
		}
		if subject.ExtractScheduledAt != nil && subject.ExtractScheduledAt.After(now) {
			busy++
			continue
		}
		if !subject.Enabled {
			// Opted out entirely. The rows keep their cursor and are closed
			// the same way a disabled workspace closes them, so this pass does
			// not leave the same pending=true lie behind that it exists to fix.
			s.closePendingWork(ctx, t.scope, failureCodeSkipSubjectOff)
			continue
		}

		// The subject row serializes this against a concurrent scheduler and
		// against a second reconciler replica: both take the same row lock, and
		// whichever arrives second sees the slot already claimed and does
		// nothing. That is why the repair pass reuses EnqueuePendingSession
		// instead of enqueueing directly.
		_, shouldEnqueue, err := s.repo.EnqueuePendingSession(ctx, t.scope, "", reconcileInFlightTimeout)
		if err != nil {
			logger.Warnf(ctx, "memory: reconcile could not requeue subject %s: %v", t.scope.SubjectID, err)
			continue
		}
		if !shouldEnqueue {
			busy++
			continue
		}
		if err := s.enqueueExtraction(ctx, t.scope, t.sessionID, "", "", 0); err != nil {
			// Same contract as the scheduling path: the row is already
			// pending=true, so a lost enqueue has to leave a mark or the work
			// is invisible to this very pass.
			s.markRunFailure(ctx, &runState{scope: t.scope}, fmt.Errorf("%w: %v", errEnqueueExtraction, err))
			continue
		}
		requeued++
		logger.Warnf(ctx,
			"memory: reconcile requeued stuck extraction for subject %s (session %s) — "+
				"the table said pending and no task existed for it",
			t.scope.SubjectID, t.sessionID)
	}
	if requeued > 0 || busy > 0 {
		logger.Infof(ctx, "memory: reconcile swept %d stuck subject(s): %d requeued, %d legitimately busy",
			len(targets), requeued, busy)
	}
	return nil
}

// HandleReconcile is the task-handler shape the reconciler needs to run on a
// worker. It takes no payload, so the scope comes from the database and not
// from the task.
func (s *Service) HandleReconcile(ctx context.Context, _ *asynq.Task) error {
	return s.ReconcileExtractions(ctx)
}

// StuckExtractions is the operator's window onto the queue: pending rows that
// have not moved in a while, oldest first.
//
// It exists because the failure this subsystem had was not a crash — it was
// nobody being able to see the work quietly going nowhere. Nothing needs it
// to run; it is here so the question "is this subject still learning?" has an
// answer that is not a log dive.
//
// It lives on the concrete type rather than interfaces.MemoryService because
// the current consumer is an operator running a query or a sweep, not a
// request handler, and adding a read path to the service interface for it
// would widen the surface every memory caller depends on.
func (s *Service) StuckExtractions(ctx context.Context, limit int) ([]types.MemoryExtractionSession, error) {
	maint, err := s.maintenance()
	if err != nil {
		return nil, err
	}
	return maint.ListStuckExtractions(ctx, time.Now().Add(-reconcileStaleAfter), limit)
}
