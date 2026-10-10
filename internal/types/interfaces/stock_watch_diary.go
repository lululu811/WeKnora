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

	// ListByTradeDate returns every diary for one (user, tenant, trade_date).
	// Used by the grading job to load the observation job's output before
	// scoring. Returns an empty slice, not an error, when no diaries exist.
	ListByTradeDate(ctx context.Context, userID string, tenantID uint64,
		tradeDate types.DateOnly) ([]*types.StockWatchDiary, error)

	// UpdateScores writes the grading job's output (final_score, rank, scores)
	// onto existing diary rows in a single transaction. It is an UPDATE, not
	// an UPSERT: the rows must already exist (written by the diary job). A
	// missing row is skipped silently rather than erroring, because the
	// grading job runs after the diary job and a diary that was not written
	// cannot be scored.
	UpdateScores(ctx context.Context, diaries []*types.StockWatchDiary) error

	// TopRanked returns the top N diaries for one (user, tenant) on a given
	// trade_date, ordered by final_score DESC. Only rows with a non-null
	// final_score are returned. The grading job writes scores; this query
	// reads them for the ranking page.
	TopRanked(ctx context.Context, userID string, tenantID uint64,
		tradeDate types.DateOnly, limit int) ([]*types.StockWatchDiary, error)
}
