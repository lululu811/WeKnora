package interfaces

import (
	"context"

	"github.com/Tencent/WeKnora/internal/types"
)

// StockWatchRepository is the storage contract for a user's private
// tracking pool (the namesake watchlist). Implementations are tenant-aware:
// every query carries tenant_id, so switching tenants shows a different pool.
type StockWatchRepository interface {
	// List returns the user's rows ordered by sort_order, then insertion.
	List(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error)
	// Count returns how many symbols the user currently tracks in this tenant.
	Count(ctx context.Context, userID string, tenantID uint64) (int64, error)
	// Add inserts a row, or refreshes name/exchange when the symbol is already
	// tracked. Returns the stored row — which may differ from `item` when an
	// existing row kept its original sort_order — plus `created`, which lets
	// the UI say "已加入" instead of "已在池中".
	//
	// A newly created row also writes one `added` event, in the same
	// transaction: a pool entry that exists with no record of entering the
	// pool would make the event log a partial truth.
	Add(ctx context.Context, item *types.StockWatch) (row *types.StockWatch, created bool, err error)
	// Remove deletes one row; `removed` reports whether anything was deleted.
	//
	// Deliberately no event: removing is not a state ("dropped" is), and the
	// event table is the pool's memory of decisions, not a mirror of the
	// table's row set. The events of a removed symbol stay — they are the
	// reason the row existed.
	Remove(ctx context.Context, userID string, tenantID uint64, thscode string) (removed bool, err error)
	// Update patches one row and returns it, or (nil, nil) when it does not
	// exist. State changes must be legal transitions
	// (types.StockWatchStateCanTransition) or the call fails with
	// types.ErrStockWatchIllegalTransition; every state/note change writes its
	// event in the same transaction as the row update.
	Update(
		ctx context.Context, userID string, tenantID uint64, thscode string, patch StockWatchPatch,
	) (*types.StockWatch, error)
}

// StockWatchPatch carries the optional fields of a pool update.
// Pointers distinguish "leave unchanged" from "set to zero/empty".
type StockWatchPatch struct {
	Name      *string
	SortOrder *int
	// State must be one of the types.StockWatchState* values; the service
	// rejects anything else before the repository sees it.
	State *string
	// Note is the whole new note, not a fragment — "" clears it.
	Note *string
}

// StockWatchEventsRepository reads the pool's append-only event log.
//
// There is intentionally no write method: events are not independently
// creatable. A mutation and its event must land together, so the only writer
// is the transaction inside StockWatchRepository. Exposing an Insert here
// would invite a second, non-transactional write path that could produce an
// event for a row change that never happened.
type StockWatchEventsRepository interface {
	// List returns the pool's events, newest first. An empty thscode means
	// "the whole pool" (the pool-wide activity feed); a non-empty one narrows
	// to a single symbol. limit is clamped by the caller.
	List(
		ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
	) ([]*types.StockWatchEvent, error)
}

// StockWatchService wraps the repository with input normalisation (thscode
// shape + case, note length, known state values) and the per-user size cap,
// so the handler stays thin.
type StockWatchService interface {
	List(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error)
	// Add returns the stored row plus whether it was newly inserted, so the
	// UI can distinguish "已加入" from "已在池中".
	Add(
		ctx context.Context, userID string, tenantID uint64, thscode, name, exchange string,
	) (row *types.StockWatch, created bool, err error)
	Remove(ctx context.Context, userID string, tenantID uint64, thscode string) (bool, error)
	Update(
		ctx context.Context, userID string, tenantID uint64, thscode string, patch StockWatchPatch,
	) (*types.StockWatch, error)
	// ListEvents returns the pool's event history, newest first, optionally
	// narrowed to one symbol by thscode.
	ListEvents(
		ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
	) ([]*types.StockWatchEvent, error)
}
