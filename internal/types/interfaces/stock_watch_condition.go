package interfaces

import (
	"context"

	"github.com/Tencent/WeKnora/internal/types"
)

// StockWatchConditionRepository is the storage contract for user-authored
// numeric conditions and their evaluation watermark.
//
// It owns the write transaction for an evaluation run: a condition's new
// last_satisfied/last_eval_date and the condition_triggered event that explains
// a crossing must land together, so (like StockWatchRepository) the only writer
// of stock_watch_events for this feature is the transaction here.
type StockWatchConditionRepository interface {
	// List returns one symbol's conditions, oldest first (the order a user
	// added them, which is the order they expect to read them).
	List(
		ctx context.Context, userID string, tenantID uint64, thscode string,
	) ([]*types.StockWatchCondition, error)
	// Create inserts a condition, or returns the existing row when the same
	// (thscode, field, op, value) is already present. `created` distinguishes
	// the two so a double-click is idempotent and silent instead of a 409 the
	// UI would have to explain.
	Create(
		ctx context.Context, cond *types.StockWatchCondition,
	) (row *types.StockWatchCondition, created bool, err error)
	// Remove deletes one condition scoped to its owning symbol and caller.
	// `removed` reports whether anything was deleted; a repeated delete is not
	// an error.
	Remove(ctx context.Context, userID string, tenantID uint64, thscode, id string) (removed bool, err error)
	// ListScopes returns every (user, tenant) pair that owns at least one
	// condition — the daily job's work list.
	ListScopes(ctx context.Context) ([]types.StockWatchScope, error)
	// ListByScope returns every condition of one (user, tenant) pair, across all
	// symbols, so a run evaluates a user's whole set against one quote batch.
	ListByScope(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatchCondition, error)
	// ApplyEvaluations records a run's decisions in ONE transaction per scope:
	// every transition's new state and watermark, plus a condition_triggered
	// event for each crossing. Either the whole scope's decisions land or none
	// do — a half-applied run would advance some watermarks past a day whose
	// other crossings were never recorded.
	ApplyEvaluations(
		ctx context.Context, userID string, tenantID uint64,
		transitions []types.StockWatchConditionTransition,
	) error
}

// StockWatchNotificationRepository records delivery attempts.
//
// Deliberately write-only for now: the audit column exists to answer "did it
// try to tell me?", which is a write-side fact; no page reads it yet, and a
// speculative read method would be an untested guess at a UI that does not
// exist.
type StockWatchNotificationRepository interface {
	Insert(ctx context.Context, notification *types.StockWatchNotification) error
}

// StockWatchConditionService wraps the repository with input validation
// (thscode shape, field/op allowlist, finite value) so the handler stays thin.
type StockWatchConditionService interface {
	List(
		ctx context.Context, userID string, tenantID uint64, thscode string,
	) ([]*types.StockWatchCondition, error)
	// Create validates and stores a condition. Adding the exact same condition
	// twice returns the existing row rather than a duplicate or a conflict.
	Create(
		ctx context.Context, userID string, tenantID uint64, thscode, field, op string, value float64,
	) (row *types.StockWatchCondition, created bool, err error)
	Remove(ctx context.Context, userID string, tenantID uint64, thscode, id string) (bool, error)
}
