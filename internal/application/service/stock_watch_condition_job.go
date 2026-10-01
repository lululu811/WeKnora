package service

import (
	"context"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/robfig/cron/v3"

	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/runtime"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/Tencent/WeKnora/internal/watchcond"
)

// StockWatchConditionSchedule is the daily evaluation time: 08:30 Asia/Shanghai.
//
// Why 08:30 and not "just after the ETL": the ETL lands the previous day's bars
// around 19:00, so by 08:30 the data is fresh AND the message arrives when the
// user actually reads it. A 19:05 push would be technically equal and
// practically forgotten by morning.
const StockWatchConditionSchedule = "0 30 8 * * *"

// shanghaiFallback is used only if the tzdata for Asia/Shanghai is missing
// (e.g. a scratch container without /usr/share/zoneinfo). It is a fixed +08:00
// zone: mainland China has had no DST since 1991, so a fixed offset is exactly
// correct here and far better than silently trusting the container's TZ (which
// is UTC in the default image, turning 08:30 into 16:30 local).
var shanghaiFallback = time.FixedZone("CST", 8*60*60)

// QuoteFetcher is the read side of the notifier: one batch call per run.
type QuoteFetcher interface {
	Fetch(ctx context.Context, symbols []string) (map[string]watchcond.Reading, error)
}

// AlertNotifier is the delivery side. Enabled() lets the job log the honest
// reason when a run crosses but nothing can be pushed.
type AlertNotifier interface {
	Enabled() bool
	Send(ctx context.Context, message string) (attempts int, err error)
}

// StockWatchConditionJob runs the daily condition evaluation.
//
// It is deliberately a "report the conditions YOU set" job: it never decides
// what to buy, never ranks, never scores. It reads what the user wrote, checks
// it against the market's readings, records crossings, and — if configured —
// tells the user once per run.
type StockWatchConditionJob struct {
	conditions    interfaces.StockWatchConditionRepository
	notifications interfaces.StockWatchNotificationRepository
	quotes        QuoteFetcher
	notifier      AlertNotifier

	cron    *cron.Cron
	mu      sync.Mutex
	started bool
}

// NewStockWatchConditionJob constructs the job. It does NOT start the cron —
// call Start from the container bootstrap so a bad schedule cannot prevent the
// rest of the system from coming up (same posture as HousekeepingService).
func NewStockWatchConditionJob(
	conditions interfaces.StockWatchConditionRepository,
	notifications interfaces.StockWatchNotificationRepository,
	quotes QuoteFetcher,
	notifier AlertNotifier,
) *StockWatchConditionJob {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		// Load the location explicitly rather than trusting the container's TZ:
		// the default image is UTC, so "08:30" would otherwise fire at 16:30
		// Beijing time and the alert would be an evening footnote.
		logger.Warnf(context.Background(),
			"[StockWatchConditions] Asia/Shanghai tzdata unavailable (%v); using fixed +08:00", err)
		loc = shanghaiFallback
	}
	return &StockWatchConditionJob{
		conditions:    conditions,
		notifications: notifications,
		quotes:        quotes,
		notifier:      notifier,
		cron: cron.New(
			cron.WithSeconds(),
			cron.WithLocation(loc),
			cron.WithChain(
				cron.Recover(cron.DefaultLogger),
				cron.SkipIfStillRunning(cron.DefaultLogger),
			),
		),
	}
}

// Start registers the daily schedule and begins the runner. Idempotent.
func (j *StockWatchConditionJob) Start(ctx context.Context) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.started {
		return nil
	}
	if _, err := j.cron.AddFunc(StockWatchConditionSchedule, func() {
		// Background, not the bootstrap ctx: a cancelled startup must not stop
		// tomorrow's run.
		if err := j.RunOnce(context.Background()); err != nil {
			logger.Warnf(context.Background(), "[StockWatchConditions] daily run failed: %v", err)
		}
	}); err != nil {
		return err
	}
	j.cron.Start()
	j.started = true
	logger.Infof(ctx, "[StockWatchConditions] started with schedule %q (Asia/Shanghai)", StockWatchConditionSchedule)
	return nil
}

// StopWithin halts the cron and waits up to timeout for an in-flight run.
func (j *StockWatchConditionJob) StopWithin(timeout time.Duration) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !j.started {
		return
	}
	if !runtime.WaitFor(j.cron.Stop().Done(), timeout) {
		logger.Warnf(context.Background(),
			"[StockWatchConditions] in-flight run still going after %s; continuing shutdown", timeout)
	}
	j.started = false
}

// RunOnce is one complete evaluation run. Exported so tests (and an operator
// with a REPL) can drive it without waiting for the cron tick.
//
// Ordering is the whole safety story:
//
//  1. Read every scope and the union of symbols.
//  2. Fetch ALL quotes. A failure here returns before any write, so a flaky
//     data service can never leave watermarks advanced past unrecorded
//     crossings.
//  3. Evaluate and persist per scope, each scope in one transaction.
//  4. Push at most ONE aggregated message for the whole run, then record the
//     attempt (even when there is no webhook).
func (j *StockWatchConditionJob) RunOnce(ctx context.Context) error {
	scopes, err := j.conditions.ListScopes(ctx)
	if err != nil {
		return fmt.Errorf("[StockWatchConditions] list scopes: %w", err)
	}
	if len(scopes) == 0 {
		return nil
	}

	type scopedConditions struct {
		scope types.StockWatchScope
		conds []*types.StockWatchCondition
	}
	loaded := make([]scopedConditions, 0, len(scopes))
	symbolSet := make(map[string]bool)
	for _, scope := range scopes {
		conds, err := j.conditions.ListByScope(ctx, scope.UserID, scope.TenantID)
		if err != nil {
			return fmt.Errorf("[StockWatchConditions] list conditions for %s/%d: %w", scope.UserID, scope.TenantID, err)
		}
		if len(conds) == 0 {
			continue
		}
		loaded = append(loaded, scopedConditions{scope: scope, conds: conds})
		for _, c := range conds {
			symbolSet[c.THSCode] = true
		}
	}
	if len(loaded) == 0 {
		return nil
	}
	symbols := make([]string, 0, len(symbolSet))
	for code := range symbolSet {
		symbols = append(symbols, code)
	}
	sort.Strings(symbols)

	// Step 2: fetch everything, write nothing yet.
	readings, err := j.quotes.Fetch(ctx, symbols)
	if err != nil {
		return fmt.Errorf("[StockWatchConditions] quote fetch failed, run aborted before any write: %w", err)
	}

	// Step 3: evaluate and persist per scope. firingScopes collects the scopes
	// that produced at least one crossing, so the one aggregated push can be
	// attributed back to the users it concerns.
	firingScopes := make([]types.StockWatchScope, 0, len(loaded))
	allFirings := make([]types.StockWatchConditionTransition, 0)
	for _, sc := range loaded {
		transitions := watchcond.Evaluate(sc.conds, readings)
		if len(transitions) == 0 {
			continue
		}
		if err := j.conditions.ApplyEvaluations(ctx, sc.scope.UserID, sc.scope.TenantID, transitions); err != nil {
			return fmt.Errorf("[StockWatchConditions] apply evaluations for %s/%d: %w",
				sc.scope.UserID, sc.scope.TenantID, err)
		}
		firedBefore := len(allFirings)
		for _, t := range transitions {
			if t.Fired {
				allFirings = append(allFirings, t)
			}
		}
		if len(allFirings) > firedBefore {
			firingScopes = append(firingScopes, sc.scope)
		}
	}
	if len(allFirings) == 0 {
		return nil
	}

	// Step 4: one message for the whole run.
	message := watchcond.FormatAlert(alertDate(allFirings), readings, allFirings)
	if !j.notifier.Enabled() {
		logger.Warnf(ctx,
			"[StockWatchConditions] %d condition(s) crossed but no Feishu webhook is configured; "+
				"trigger events were written, nothing was pushed", len(allFirings))
		j.recordNotifications(ctx, firingScopes, message, 0,
			fmt.Errorf("feishu webhook not configured (WEKNORA_FEISHU_ALERT_WEBHOOK unset)"))
		return nil
	}

	attempts, sendErr := j.notifier.Send(ctx, message)
	if sendErr != nil {
		logger.Warnf(ctx,
			"[StockWatchConditions] push failed after %d attempt(s); trigger events remain on the page: %v",
			attempts, sendErr)
	}
	j.recordNotifications(ctx, firingScopes, message, attempts, sendErr)
	return nil
}

// recordNotifications writes one audit row per scope that had a firing.
//
// One row per scope rather than one per firings: the run sends a single
// aggregated message, and each row states "this scope's portion was part of
// that delivery attempt, and here is how it went". A write failure is logged,
// not returned — the trigger events are already committed, and losing the audit
// row must not make the job look like it failed when the page is correct.
func (j *StockWatchConditionJob) recordNotifications(
	ctx context.Context,
	scopes []types.StockWatchScope,
	message string,
	attempts int,
	sendErr error,
) {
	errText := ""
	if sendErr != nil {
		errText = sendErr.Error()
	}
	for _, scope := range scopes {
		row := &types.StockWatchNotification{
			UserID:   scope.UserID,
			TenantID: scope.TenantID,
			Kind:     types.StockWatchNotificationConditionTriggered,
			Payload:  message,
			OK:       sendErr == nil,
			Error:    errText,
			Attempts: attempts,
		}
		if err := j.notifications.Insert(ctx, row); err != nil {
			logger.Warnf(ctx,
				"[StockWatchConditions] failed to record notification audit for %s/%d: %v",
				scope.UserID, scope.TenantID, err)
		}
	}
}

// alertDate is the newest trading day among the firings — the date the user
// should read the message as "as of". Older suspended symbols may carry an
// older date; the newest is the run's actual data freshness.
func alertDate(firings []types.StockWatchConditionTransition) string {
	newest := ""
	for _, f := range firings {
		if f.EvalDate > newest {
			newest = f.EvalDate
		}
	}
	return newest
}
