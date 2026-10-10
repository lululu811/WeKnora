package service

import (
	"context"
	"fmt"
	"sort"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// RunGrading scores every diary written for today across all scopes.
//
// It is called from the tail of the existing 08:30 condition run, AFTER the
// diary step. The diary writes verdicts and readings; this function reads
// those rows back, scores Q1-Q18, computes final_score, ranks, and updates
// the same rows. The two steps never write the same column.
//
// The error is the aggregate of every scope's outcome: one broken workspace
// must not stop the others from getting scored.
func RunGrading(
	ctx context.Context,
	grading *StockWatchGradingService,
	repo interfaces.StockWatchDiaryRepository,
	quotes QuoteFetcher,
) error {
	if grading == nil || repo == nil {
		return nil
	}
	scopes, err := repo.ListDiaryScopes(ctx)
	if err != nil {
		return fmt.Errorf("[WatchlistGrading] list scopes: %w", err)
	}
	if len(scopes) == 0 {
		return nil
	}

	// Collect symbols across scopes and fetch one batch, same as RunDiaries.
	symbolSet := make(map[string]bool)
	for _, scope := range scopes {
		watched, err := repo.ListWatchedByScope(ctx, scope.UserID, scope.TenantID)
		if err != nil {
			return fmt.Errorf("[WatchlistGrading] list watched for %s/%d: %w",
				scope.UserID, scope.TenantID, err)
		}
		for _, w := range watched {
			if w != nil {
				symbolSet[w.THSCode] = true
			}
		}
	}
	if len(symbolSet) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(symbolSet))
	for code := range symbolSet {
		symbols = append(symbols, code)
	}
	sort.Strings(symbols)

	readings, err := quotes.Fetch(ctx, symbols)
	if err != nil {
		return fmt.Errorf("[WatchlistGrading] quote fetch failed: %w", err)
	}

	// Determine the trade date from the freshest reading available.
	tradeDate := newestReadingDate(readings)
	if tradeDate.IsZero() {
		logger.Warnf(ctx, "[WatchlistGrading] no decidable reading date found, skipping")
		return nil
	}

	var failures []string
	for _, scope := range scopes {
		if err := runGradingForScope(ctx, grading, scope, readings, tradeDate); err != nil {
			failures = append(failures, fmt.Sprintf("%s/%d: %v",
				scope.UserID, scope.TenantID, err))
		}
	}
	if len(failures) > 0 {
		return fmt.Errorf("[WatchlistGrading] %d scope(s) failed: %v",
			len(failures), failures[0])
	}
	logger.Infof(ctx, "[WatchlistGrading] scored diaries for %d scope(s), %d symbol(s)",
		len(scopes), len(symbols))
	return nil
}

// runGradingForScope runs grading for one workspace. The tenant context is
// injected per scope, same pattern as runDiariesForScope.
func runGradingForScope(
	ctx context.Context, grading *StockWatchGradingService,
	scope types.StockWatchScope, readings map[string]watchcond.Reading,
	tradeDate types.DateOnly,
) error {
	scopeCtx := context.WithValue(ctx, types.TenantIDContextKey, scope.TenantID)
	return grading.RunGrading(scopeCtx, scope, readings, tradeDate)
}

// newestReadingDate returns the freshest trading day across all readings.
// All readings from one quote fetch should share the same date, but if a
// suspended symbol carries a stale date the newest is the run's actual
// freshness — same logic as alertDate in the condition job.
func newestReadingDate(readings map[string]watchcond.Reading) types.DateOnly {
	newest := ""
	for _, r := range readings {
		if r.Date > newest {
			newest = r.Date
		}
	}
	if newest == "" {
		return types.DateOnly{}
	}
	d, _ := types.ParseDateOnly(newest)
	return d
}
