package repository

import (
	"context"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (r *memoryRepository) withSubject(ctx context.Context, scope interfaces.MemoryScope, fn func(*gorm.DB, *types.MemorySubject) error) error {
	return r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var subject types.MemorySubject
		if err := tx.Where("tenant_id = ? AND subject_id = ?", scope.TenantID, scope.SubjectID).
			Clauses(forUpdateClause()).First(&subject).Error; err != nil {
			return err
		}
		return fn(tx, &subject)
	})
}

func saveExtractionState(tx *gorm.DB, subject *types.MemorySubject) error {
	return tx.Model(subject).Updates(map[string]interface{}{
		"extraction_state": subject.ExtractionState, "pending_sessions": subject.PendingSessions,
		"extract_scheduled_at": subject.ExtractScheduledAt, "updated_at": time.Now(),
	}).Error
}

func extractionRows(tx *gorm.DB, scope interfaces.MemoryScope) *gorm.DB {
	return tx.Model(&types.MemoryExtractionSession{}).
		Where("tenant_id = ? AND subject_id = ?", scope.TenantID, scope.SubjectID)
}

func enqueueExtractionSession(tx *gorm.DB, subject *types.MemorySubject, id string, bump bool) error {
	if id == "" {
		return nil
	}
	row := types.MemoryExtractionSession{
		TenantID: subject.TenantID, SubjectID: subject.SubjectID, SessionID: id, Revision: 1, Pending: true,
	}
	// Preserve the pre-upgrade boundary instead of replaying every historical
	// conversation. This value is frozen; only per-session cursors advance now.
	if subject.ExtractCursor != nil {
		row.Cursor.At = *subject.ExtractCursor
	}
	conflict := clause.OnConflict{
		Columns:   []clause.Column{{Name: "tenant_id"}, {Name: "subject_id"}, {Name: "session_id"}},
		DoNothing: true,
	}
	if bump {
		conflict.DoNothing = false
		conflict.DoUpdates = clause.Assignments(map[string]interface{}{
			"revision": gorm.Expr("memory_extraction_sessions.revision + 1"), "pending": true, "updated_at": time.Now(),
		})
	}
	return tx.Clauses(conflict).Create(&row).Error
}

func importLegacySessions(tx *gorm.DB, subject *types.MemorySubject) error {
	for _, id := range subject.PendingSessions {
		if err := enqueueExtractionSession(tx, subject, id, false); err != nil {
			return err
		}
	}
	subject.PendingSessions = types.MemoryPendingSessions{}
	return nil
}

func hasPendingExtraction(tx *gorm.DB, scope interfaces.MemoryScope) (bool, error) {
	var rows []types.MemoryExtractionSession
	result := extractionRows(tx, scope).Select("session_id").Where("pending = ?", true).Limit(1).Find(&rows)
	return len(rows) > 0, result.Error
}

func (r *memoryRepository) HasPendingExtraction(ctx context.Context, scope interfaces.MemoryScope) (bool, error) {
	return hasPendingExtraction(r.db.WithContext(ctx), scope)
}

func (r *memoryRepository) EnqueuePendingSession(ctx context.Context, scope interfaces.MemoryScope, sessionID string, timeout time.Duration) (*types.MemorySubject, bool, error) {
	var snapshot types.MemorySubject
	shouldSend := false
	err := r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		snapshot = *subject
		if err := importLegacySessions(tx, subject); err != nil {
			return err
		}
		if err := enqueueExtractionSession(tx, subject, sessionID, true); err != nil {
			return err
		}
		now := time.Now()
		running := subject.ExtractionState.LeaseID != "" && subject.ExtractionState.LeaseUntil.After(now)
		queued := subject.ExtractScheduledAt != nil && now.Sub(*subject.ExtractScheduledAt) < timeout
		if !running && !queued {
			pending, err := hasPendingExtraction(tx, scope)
			if err != nil {
				return err
			}
			if pending {
				subject.ExtractScheduledAt = &now
				shouldSend = true
			}
		}
		return saveExtractionState(tx, subject)
	})
	return &snapshot, shouldSend, err
}

func (r *memoryRepository) ClaimPendingSessions(ctx context.Context, scope interfaces.MemoryScope, fallbackSession, leaseID string, ttl time.Duration) (*types.MemoryExtractionBatch, error) {
	var batch *types.MemoryExtractionBatch
	err := r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		now := time.Now()
		if subject.ExtractionState.LeaseID != "" && subject.ExtractionState.LeaseUntil.After(now) {
			batch = &types.MemoryExtractionBatch{RetryAt: subject.ExtractionState.LeaseUntil}
			return nil
		}
		if err := importLegacySessions(tx, subject); err != nil {
			return err
		}
		// Bootstrap legacy payloads once; a duplicate must not reactivate a drained row.
		if err := enqueueExtractionSession(tx, subject, fallbackSession, false); err != nil {
			return err
		}
		var sessions []types.MemoryExtractionSession
		if err := extractionRows(tx, scope).Where("pending = ?", true).
			Order("updated_at ASC, session_id ASC").
			Limit(types.MaxMemoryPendingSessions).Find(&sessions).Error; err != nil {
			return err
		}
		if len(sessions) == 0 {
			return saveExtractionState(tx, subject)
		}
		batch = &types.MemoryExtractionBatch{Sessions: sessions}
		subject.ExtractionState.LeaseID = leaseID
		subject.ExtractionState.LeaseUntil = now.Add(ttl)
		return saveExtractionState(tx, subject)
	})
	return batch, err
}

func validExtractionLease(subject *types.MemorySubject, leaseID string) bool {
	return subject.ExtractionState.LeaseID == leaseID && subject.ExtractionState.LeaseUntil.After(time.Now())
}

// RunFailureBudget bounds how often a run-level failure may leave a session
// pending before the row is closed for good. Without a ceiling a permanently
// unresolvable model keeps a row at pending=true indefinitely, and a row that
// is pending forever is indistinguishable from a row that is merely slow —
// which is the state this whole path exists to eliminate.
//
// Exported so the test that pins the terminal transition and anyone reasoning
// about it are working from the same number rather than a copy of it.
const RunFailureBudget = 5

// claimRunFailure checks that the caller is allowed to stamp a failure onto
// this scope's pending rows.
//
// A non-empty leaseID must match a live lease: the lease is the proof that
// this caller is the run holding the subject. An empty leaseID means the
// failure happened before the claim, or outside a worker entirely (the
// enqueue path, the reconciler). Those callers get the same protection in the
// other direction — they may only write while nothing else holds the subject,
// so a stale repair pass can never stomp a run that is genuinely in flight.
func claimRunFailure(subject *types.MemorySubject, leaseID string) error {
	if leaseID != "" {
		if !validExtractionLease(subject, leaseID) {
			return types.ErrMemoryExtractionLeaseLost
		}
		return nil
	}
	if subject.ExtractionState.LeaseID != "" && subject.ExtractionState.LeaseUntil.After(time.Now()) {
		return types.ErrMemoryExtractionLeaseLost
	}
	return nil
}

// MarkExtractionRunFailure records a failure that killed a whole run before
// any segment could be checkpointed — a model that will not resolve, a subject
// that will not load, an enqueue that never landed.
//
// It is deliberately NOT the segment-level RecordExtractionFailure. That call
// describes a bad *range* of transcript and needs a cursor, a lease and a
// segment to attach itself to; a run-level failure has none of them. Reaching
// for the segment-level path there is what left production holding rows at
// pending=true with failure_count=0 and failure_code=”: a state that reads as
// "in flight" no matter how long it stays that way. The 27 dead-lettered
// memory:extract tasks from 2026-09-25 are exactly those rows.
//
// After this call the scope's pending rows are distinguishable by
// failure_count/failure_code alone, whether they stay retryable (pending=true,
// counter advanced, failed_at still NULL) or are closed as permanently failed
// (pending=false, failed_at set) once the budget is spent.
func (r *memoryRepository) MarkExtractionRunFailure(
	ctx context.Context, scope interfaces.MemoryScope, leaseID, code string,
) error {
	return r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if err := claimRunFailure(subject, leaseID); err != nil {
			return err
		}
		var rows []types.MemoryExtractionSession
		if err := extractionRows(tx, scope).Where("pending = ?", true).Find(&rows).Error; err != nil {
			return err
		}
		now := time.Now()
		for _, row := range rows {
			attempts := row.FailureCount + 1
			updates := map[string]interface{}{
				"failure_count": attempts, "failure_code": code,
				"updated_at": now, "failed_at": nil,
			}
			if attempts >= RunFailureBudget {
				updates["pending"] = false
				updates["failed_at"] = now
			}
			if err := extractionRows(tx, scope).
				Where("session_id = ?", row.SessionID).Updates(updates).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

// ClosePendingExtraction takes a scope's pending rows out of the queue because
// the work should not run at all, without advancing their cursor.
//
// The cursor is the point. A skip must be *deferred*, not *consumed*: the rows
// stop claiming to be in flight, but the watermark stays where it was, so the
// next turn after the switch is turned back on re-queues the same session from
// the same position and the skipped turns are extracted after all. Moving the
// cursor here instead would be the original bug wearing a different hat —
// messages silently consumed by a run that deliberately did nothing.
//
// The resulting row reads pending=false with a code in failure_code and
// failed_at still NULL, which is what separates "deferred on purpose" from
// "abandoned after failing" (pending=false, failed_at set) and from "in
// flight" (pending=true, failure_count=0, no code). A later successful
// checkpoint clears the marker, because CheckpointExtraction resets the
// counters whenever failed_at is NULL.
func (r *memoryRepository) ClosePendingExtraction(
	ctx context.Context, scope interfaces.MemoryScope, leaseID, code string,
) (int64, error) {
	var closed int64
	err := r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if err := claimRunFailure(subject, leaseID); err != nil {
			return err
		}
		result := extractionRows(tx, scope).Where("pending = ?", true).Updates(map[string]interface{}{
			"pending": false, "failure_code": code, "failed_at": nil, "updated_at": time.Now(),
		})
		closed = result.RowsAffected
		return result.Error
	})
	return closed, err
}

// defaultStuckExtractionLimit bounds one sweep. The reconciler runs on a
// fixed cadence and returns for the remainder of the backlog on the next tick,
// so a small page is enough and keeps a pathological backlog from turning into
// one enormous transaction.
const defaultStuckExtractionLimit = 200

// ListStuckExtractions returns the pending rows nobody is working on, oldest
// first: pending=true and untouched since staleBefore. It is both the
// reconciler's input and the operator's window onto the queue, so a subject
// that has been stuck for a week is one query away instead of a log dive.
//
// Sibling state lives in the same columns and is worth knowing about when
// reading these results: a row that is NOT returned here because it is not
// pending may instead be permanently failed, which is pending=false with a
// non-NULL failed_at. That is the terminal state a run-level failure reaches
// after its budget, and it is the answer to "why did this subject learn
// nothing" — the 17 production rows sitting at pending=true with
// failure_count=0 predate this marker and are distinguishable by that counter
// alone.
//
// The predicate leads with pending and not tenant_id, so the existing
// idx_memory_extraction_pending cannot serve it as a prefix. That is
// deliberate: the table holds one small row per conversation, the sweep is
// bounded, and it runs once every few minutes. Add a (pending, updated_at)
// index only if this query ever shows up in a slow-query log.
func (r *memoryRepository) ListStuckExtractions(
	ctx context.Context, staleBefore time.Time, limit int,
) ([]types.MemoryExtractionSession, error) {
	if limit <= 0 {
		limit = defaultStuckExtractionLimit
	}
	var rows []types.MemoryExtractionSession
	err := r.db.WithContext(ctx).Model(&types.MemoryExtractionSession{}).
		Where("pending = ? AND updated_at < ?", true, staleBefore).
		Order("updated_at ASC, tenant_id ASC, subject_id ASC, session_id ASC").
		Limit(limit).
		Find(&rows).Error
	return rows, err
}

func (r *memoryRepository) CheckpointExtraction(ctx context.Context, scope interfaces.MemoryScope, leaseID string, session types.MemoryExtractionSession, cursor types.MemoryMessageCursor, drained bool) error {
	return r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if !validExtractionLease(subject, leaseID) {
			return types.ErrMemoryExtractionLeaseLost
		}
		var progress types.MemoryExtractionSession
		if err := extractionRows(tx, scope).Where("session_id = ?", session.SessionID).
			First(&progress).Error; err != nil {
			return err
		}
		if cursor.After(progress.Cursor) {
			progress.Cursor = cursor
		}
		// Updating just this row rotates unfinished work without rewriting the
		// subject's entire history. Completed cursors remain small indexed records.
		updates := map[string]interface{}{
			"cursor_at": progress.Cursor.At, "cursor_id": progress.Cursor.ID,
			"pending": !drained || progress.Revision != session.Revision, "updated_at": time.Now(),
		}
		if progress.FailedAt == nil {
			updates["failure_count"] = 0
			updates["failure_code"] = ""
		}
		return extractionRows(tx, scope).Where("session_id = ?", session.SessionID).Updates(updates).Error
	})
}

func (r *memoryRepository) RecordExtractionFailure(
	ctx context.Context, scope interfaces.MemoryScope, leaseID string, failure interfaces.MemoryExtractionFailure,
) (bool, error) {
	skip := false
	err := r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if !validExtractionLease(subject, leaseID) {
			return types.ErrMemoryExtractionLeaseLost
		}
		var progress types.MemoryExtractionSession
		if err := extractionRows(tx, scope).Where("session_id = ?", failure.Session.SessionID).
			First(&progress).Error; err != nil {
			return err
		}
		attempts := 1
		if progress.FailedFrom.At.Equal(progress.Cursor.At) && progress.FailedFrom.ID == progress.Cursor.ID {
			attempts += progress.FailureCount
		}
		skip = attempts >= 3
		updates := map[string]interface{}{
			"failure_count": attempts, "failure_code": failure.Code, "updated_at": time.Now(),
			"failed_at":      nil,
			"failed_from_at": progress.Cursor.At, "failed_from_id": progress.Cursor.ID,
			"failed_to_at": failure.End.At, "failed_to_id": failure.End.ID,
		}
		if skip {
			updates["failed_at"] = time.Now()
		}
		return extractionRows(tx, scope).Where("session_id = ?", failure.Session.SessionID).Updates(updates).Error
	})
	return skip, err
}

func (r *memoryRepository) FinishExtraction(ctx context.Context, scope interfaces.MemoryScope, leaseID string) error {
	return r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if subject.ExtractionState.LeaseID != leaseID {
			return types.ErrMemoryExtractionLeaseLost
		}
		subject.ExtractionState.LeaseID = ""
		subject.ExtractionState.LeaseUntil = time.Time{}
		subject.ExtractScheduledAt = nil
		if err := saveExtractionState(tx, subject); err != nil {
			return err
		}
		return tx.Model(subject).Update("last_extracted_at", time.Now()).Error
	})
}

func (r *memoryRepository) ReleaseExtractionSlot(ctx context.Context, scope interfaces.MemoryScope, leaseID string) error {
	return r.withSubject(ctx, scope, func(tx *gorm.DB, subject *types.MemorySubject) error {
		if subject.ExtractionState.LeaseID != leaseID {
			return nil
		}
		subject.ExtractionState.LeaseID = ""
		subject.ExtractionState.LeaseUntil = time.Time{}
		subject.ExtractScheduledAt = nil
		return saveExtractionState(tx, subject)
	})
}
