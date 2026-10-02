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

// StockWatchDiaryNotificationKind labels the audit row written when a diary
// run fails, so that "there is no diary for this trading day" and "the job
// never ran, or ran and broke" are distinguishable from the data alone.
//
// The condition job keeps the same discipline for its own delivery attempts
// (see its recordNotifications). Without it, an outage that stopped the diary
// job would look exactly like a day on which the model had nothing to say —
// and the difference matters, because one is a bug to fix and the other is the
// system working.
const StockWatchDiaryNotificationKind = "diary_run_failed"

// RunDiaries writes one diary per tracked symbol, for every scope in the pool.
//
// It is called from the tail of the existing 08:30 condition run rather than
// from a second cron: the two jobs need the same quote batch, the same trading
// day and the same "only if the ETL actually landed" precondition, and a second
// schedule would have to re-derive all three to stay in step with the first.
//
// The error it returns is the aggregate of every scope's outcome, never an
// early return: one broken workspace must not stop the other twenty-nine from
// getting their diaries. The caller logs it and moves on.
func RunDiaries(
	ctx context.Context,
	diaries *StockWatchDiaryService,
	repo interfaces.StockWatchDiaryRepository,
	notifications interfaces.StockWatchNotificationRepository,
	quotes QuoteFetcher,
) error {
	if diaries == nil || repo == nil {
		return nil
	}
	scopes, err := repo.ListDiaryScopes(ctx)
	if err != nil {
		return fmt.Errorf("[WatchlistDiary] list scopes: %w", err)
	}
	if len(scopes) == 0 {
		return nil
	}

	// Collect the symbols across all scopes first and fetch one batch. The
	// quote client chunks at 200 symbols per request, so a large deployment
	// costs a few round trips rather than one per scope.
	symbolSet := make(map[string]bool)
	for _, scope := range scopes {
		watched, err := repo.ListWatchedByScope(ctx, scope.UserID, scope.TenantID)
		if err != nil {
			return fmt.Errorf("[WatchlistDiary] list watched for %s/%d: %w",
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
		// No readings means no decidable input for any symbol. Recording the
		// failure matters more than the absence of diaries: without it this
		// morning is indistinguishable from a morning where the model had
		// nothing to say.
		recordDiaryFailure(ctx, notifications, scopes,
			fmt.Errorf("quote fetch failed, run aborted before any write: %w", err))
		return fmt.Errorf("[WatchlistDiary] quote fetch failed: %w", err)
	}

	var failures []string
	for _, scope := range scopes {
		if err := runDiariesForScope(ctx, diaries, scope, readings); err != nil {
			failures = append(failures, fmt.Sprintf("%s/%d: %v", scope.UserID, scope.TenantID, err))
			recordDiaryFailure(ctx, notifications, []types.StockWatchScope{scope}, err)
		}
	}
	if len(failures) > 0 {
		return fmt.Errorf("[WatchlistDiary] %d scope(s) failed: %v", len(failures), failures[0])
	}
	logger.Infof(ctx, "[WatchlistDiary] wrote diaries for %d scope(s), %d symbol(s)",
		len(scopes), len(symbols))
	return nil
}

// runDiariesForScope is one workspace's run.
//
// The tenant context is injected HERE, and it is the reason this is a separate
// function rather than a loop body: modelService.ListModels calls
// types.MustTenantIDFromContext, which panics rather than returning an error,
// and the cron callback runs on context.Background() with no tenant on it.
// Building the context per scope is what lets a single background job serve
// many workspaces — the same pattern memory/extract.go and
// knowledge_process.go use for exactly this reason.
func runDiariesForScope(
	ctx context.Context, diaries *StockWatchDiaryService,
	scope types.StockWatchScope, readings map[string]watchcond.Reading,
) error {
	scopeCtx := context.WithValue(ctx, types.TenantIDContextKey, scope.TenantID)
	return diaries.RunOnceForScope(scopeCtx, scope, readings)
}

// recordDiaryFailure writes one audit row per affected scope.
//
// One row per scope, not one per failure mode, and a write failure here is
// logged rather than returned: the point of the row is to leave a trace, and a
// trace that turns into a second error does not make the first one better.
func recordDiaryFailure(
	ctx context.Context, notifications interfaces.StockWatchNotificationRepository,
	scopes []types.StockWatchScope, cause error,
) {
	if notifications == nil {
		return
	}
	errText := ""
	if cause != nil {
		errText = cause.Error()
	}
	for _, scope := range scopes {
		row := &types.StockWatchNotification{
			UserID:   scope.UserID,
			TenantID: scope.TenantID,
			Kind:     StockWatchDiaryNotificationKind,
			// The payload is the run-level explanation, identical for every
			// scope because the failure was above the per-scope level.
			Payload: "daily observation diary run failed",
			OK:      false,
			Error:   errText,
		}
		if err := notifications.Insert(ctx, row); err != nil {
			logger.Warnf(ctx, "[WatchlistDiary] failed to record run audit for %s/%d: %v",
				scope.UserID, scope.TenantID, err)
		}
	}
}
