package interfaces

import (
	"context"

	"github.com/Tencent/WeKnora/internal/types"
)

// StockWatchDiaryRepository reads and writes the daily observation diary.
//
// Kept separate from StockWatchRepository even though both touch
// stock_watches: that repository's Update is where the human's state machine
// lives, and a diary write has no business being one of its methods.
type StockWatchDiaryRepository interface {
	// ListDiaryScopes returns every (user, tenant) with at least one symbol
	// in observing or holding.
	//
	// It must read stock_watches, not stock_watch_conditions — see the
	// implementation for why reusing the condition table's ListScopes would
	// skip most of every real pool.
	ListDiaryScopes(ctx context.Context) ([]types.StockWatchScope, error)

	// ListWatchedByScope returns the eligible symbols in one scope, in a
	// stable order so the prompt is reproducible.
	ListWatchedByScope(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error)

	// ListBySymbol returns one symbol's diaries, newest trading day first.
	ListBySymbol(ctx context.Context, userID string, tenantID uint64, thscode string, limit int) (
		[]*types.StockWatchDiary, error)

	// RecentBefore returns up to limit diaries strictly older than `before`,
	// used as the model's own recent stance. Strictly older so the row being
	// written is not fed back to the model as its own history.
	RecentBefore(ctx context.Context, userID string, tenantID uint64, thscode string,
		before types.DateOnly, limit int) ([]*types.StockWatchDiary, error)

	// Get returns one diary, or (nil, nil) when none exists for that day.
	// "No diary for this day" is an ordinary answer (the regenerate action
	// starts from exactly that state), not an error.
	Get(ctx context.Context, userID string, tenantID uint64, thscode string,
		tradeDate types.DateOnly) (*types.StockWatchDiary, error)

	// UpsertMany writes a run's diaries in a single transaction, overwriting
	// any existing row for the same (symbol, trading day).
	//
	// One transaction for the whole run on purpose: a partially written run
	// would leave some symbols with today's diary and some without, with
	// nothing recording that the run was incomplete.
	UpsertMany(ctx context.Context, diaries []*types.StockWatchDiary) error
}
