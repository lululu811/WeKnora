package interfaces

import (
	"context"

	"github.com/Tencent/WeKnora/internal/types"
)

// StockWatchRepository is the storage contract for a user's private
// watchlist. Implementations are tenant-aware: every query carries tenant_id,
// so switching tenants shows a different list (see migration 000115 for why).
type StockWatchRepository interface {
	// List returns the user's rows ordered by sort_order, then insertion.
	List(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error)
	// Count returns how many symbols the user currently watches in this tenant.
	Count(ctx context.Context, userID string, tenantID uint64) (int64, error)
	// Add inserts a row, or refreshes name/exchange when the symbol is already
	// watched. Returns the stored row — which may differ from `item` when an
	// existing row kept its original sort_order — plus `created`, which lets
	// the UI say "已加入" instead of "已在自选中".
	Add(ctx context.Context, item *types.StockWatch) (row *types.StockWatch, created bool, err error)
	// Remove deletes one row; `removed` reports whether anything was deleted.
	Remove(ctx context.Context, userID string, tenantID uint64, thscode string) (removed bool, err error)
	// Update patches one row and returns it, or (nil, nil) when it does not exist.
	Update(
		ctx context.Context, userID string, tenantID uint64, thscode string, patch StockWatchPatch,
	) (*types.StockWatch, error)
}

// StockWatchPatch carries the optional fields of a watchlist update.
// Pointers distinguish "leave unchanged" from "set to zero/empty".
type StockWatchPatch struct {
	Name      *string
	SortOrder *int
}

// StockWatchService wraps the repository with input normalisation (thscode
// shape + case) and the per-user size cap, so the handler stays thin.
type StockWatchService interface {
	List(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error)
	// Add returns the stored row plus whether it was newly inserted, so the
	// UI can distinguish "已加入自选" from "已在自选中".
	Add(
		ctx context.Context, userID string, tenantID uint64, thscode, name, exchange string,
	) (row *types.StockWatch, created bool, err error)
	Remove(ctx context.Context, userID string, tenantID uint64, thscode string) (bool, error)
	Update(
		ctx context.Context, userID string, tenantID uint64, thscode string, patch StockWatchPatch,
	) (*types.StockWatch, error)
}
