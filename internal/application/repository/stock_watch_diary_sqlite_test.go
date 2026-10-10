package repository_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/types"
)

func TestStockWatchDiaryRepository_TopRankedFallback(t *testing.T) {
	db := setupStockWatchTestDB(t)
	repo := repository.NewStockWatchDiaryRepository(db)
	ctx := context.Background()

	const user = "user-ranking"
	const tenant uint64 = 88

	score80 := 80.0
	score95 := 95.5
	score70 := 70.0
	rank1 := 1
	rank2 := 2

	d1, err := types.ParseDateOnly("2026-09-28")
	require.NoError(t, err)
	d2, err := types.ParseDateOnly("2026-09-30")
	require.NoError(t, err)

	// Insert earlier date diaries
	err = repo.UpsertMany(ctx, []*types.StockWatchDiary{
		{
			UserID:     user,
			TenantID:   tenant,
			THSCode:    "600519.SH",
			TradeDate:  d1,
			Verdict:    "buy",
			FinalScore: &score70,
		},
	})
	require.NoError(t, err)

	// Insert latest date diaries (2026-09-30)
	err = repo.UpsertMany(ctx, []*types.StockWatchDiary{
		{
			UserID:     user,
			TenantID:   tenant,
			THSCode:    "002594.SZ",
			TradeDate:  d2,
			Verdict:    "buy",
			FinalScore: &score95,
			Rank:       &rank1,
		},
		{
			UserID:     user,
			TenantID:   tenant,
			THSCode:    "600487.SH",
			TradeDate:  d2,
			Verdict:    "hold",
			FinalScore: &score80,
			Rank:       &rank2,
		},
		{
			UserID:    user,
			TenantID:  tenant,
			THSCode:   "000001.SZ",
			TradeDate: d2,
			Verdict:   "none",
			// No final_score
		},
	})
	require.NoError(t, err)

	// 1. Explicit tradeDate d2 query
	explicit, err := repo.TopRanked(ctx, user, tenant, d2, 10)
	require.NoError(t, err)
	require.Len(t, explicit, 2)
	assert.Equal(t, "002594.SZ", explicit[0].THSCode)
	assert.Equal(t, "600487.SH", explicit[1].THSCode)

	// 2. Zero tradeDate query -> should fallback to d2 (2026-09-30)
	fallback, err := repo.TopRanked(ctx, user, tenant, types.DateOnly{}, 10)
	require.NoError(t, err)
	require.Len(t, fallback, 2, "zero date must fallback to latest scored trade date")
	assert.Equal(t, "002594.SZ", fallback[0].THSCode)
	assert.Equal(t, "600487.SH", fallback[1].THSCode)
	assert.Equal(t, d2, fallback[0].TradeDate)
}
