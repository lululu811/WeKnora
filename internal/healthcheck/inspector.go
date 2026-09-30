package healthcheck

import (
	"context"
	"fmt"
	"sync"
	"time"

	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/agent"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
)

// DefaultInterval is how often the periodic pass re-runs.
//
// One hour is chosen against the incident that motivated this package: the
// gap was three days. An hourly pass turns a three-day silent failure into
// a bounded one, and the checks are all cheap indexed reads.
const DefaultInterval = time.Hour

// StuckExtractionLister is the slice of the memory service that check 3
// needs. It is an interface rather than a *memory.Service so this package
// does not import the memory service, and so the check is testable with a
// stub.
//
// The staleness cutoff is deliberately NOT reimplemented here. It is the
// reconciler's definition (memory/reconcile.go reconcileStaleAfter, applied
// inside StuckExtractions), and two notions of "stale" in one repo is the
// kind of drift this checker exists to catch.
type StuckExtractionLister interface {
	StuckExtractions(ctx context.Context, limit int) ([]types.MemoryExtractionSession, error)
}

// Config tunes the inspection. The zero value is usable and yields
// conservative defaults; every field only widens what gets reported.
type Config struct {
	// DB is required. Without it the database-backed checks are skipped
	// and recorded as check errors rather than silently passing.
	DB *gorm.DB
	// MemoryService supplies the extraction backlog check. Optional.
	MemoryService StuckExtractionLister
	// DeadLetterRecentWindow classifies a dead-letter group as "currently
	// failing". Default 24h.
	DeadLetterRecentWindow time.Duration
	// DeadLetterStaleWindow classifies a dead-letter group as "merely old"
	// rather than an active incident. Default 7d.
	DeadLetterStaleWindow time.Duration
	// StuckExtractionLimit bounds the rows the backlog check names. Default 5.
	// The count is always exact; only the sample of named rows is bounded.
	StuckExtractionLimit int
	// MaxFindingsPerCheck bounds findings per check so a systematically
	// broken deployment cannot produce an unbounded report. Default 50.
	MaxFindingsPerCheck int
	// LogFindings controls whether each finding is logged individually at
	// error level. Default true.
	LogFindings bool
}

func (c Config) withDefaults() Config {
	if c.DeadLetterRecentWindow <= 0 {
		c.DeadLetterRecentWindow = 24 * time.Hour
	}
	if c.DeadLetterStaleWindow <= 0 {
		c.DeadLetterStaleWindow = 7 * 24 * time.Hour
	}
	if c.StuckExtractionLimit <= 0 {
		c.StuckExtractionLimit = 5
	}
	if c.MaxFindingsPerCheck <= 0 {
		c.MaxFindingsPerCheck = 50
	}
	return c
}

// Inspector runs the checks and remembers the latest verdict.
//
// The remembered verdict is the degraded signal. It is written by Run and
// read by Snapshot, which the readiness handler serves; nothing in this
// package can make the process refuse traffic.
type Inspector struct {
	cfg Config
	now func() time.Time

	mu     sync.RWMutex
	last   *Report
	lastOK time.Time
}

// New builds an Inspector. A nil DB is accepted: the database-backed checks
// then report themselves as "did not run" instead of vanishing.
func New(cfg Config) *Inspector {
	return &Inspector{cfg: cfg.withDefaults(), now: time.Now}
}

// NewWithClock is New with an injectable clock, for tests that need to
// reason about age windows without sleeping.
func NewWithClock(cfg Config, now func() time.Time) *Inspector {
	insp := New(cfg)
	if now != nil {
		insp.now = now
	}
	return insp
}

// Snapshot returns the latest report, or nil if the inspection has never
// completed a pass.
//
// A nil snapshot is the honest answer to "what did the last pass find?" when
// there has not been one. Callers must render it as unknown, not as ok.
func (i *Inspector) Snapshot() *Report {
	i.mu.RLock()
	defer i.mu.RUnlock()
	if i.last == nil {
		return nil
	}
	// Return a copy so a caller cannot mutate shared state, and so the
	// readiness handler races with nothing.
	cp := *i.last
	cp.Findings = append([]Finding(nil), i.last.Findings...)
	cp.CheckErrors = make(map[string]string, len(i.last.CheckErrors))
	for k, v := range i.last.CheckErrors {
		cp.CheckErrors[k] = v
	}
	cp.ChecksRun = append([]string(nil), i.last.ChecksRun...)
	return &cp
}

// Run executes one full inspection pass and returns its report.
//
// It never returns an error and never panics. A defect in configuration is
// something to report, not a reason to take the process down; and a bug in
// the checker itself must not become the outage it was written to prevent.
// Every check is therefore isolated: it may add findings, or it may record
// itself as not-run, but it can neither abort the pass nor take the process
// with it.
func (i *Inspector) Run(ctx context.Context) *Report {
	if ctx == nil {
		ctx = context.Background()
	}
	started := i.now()
	report := newReport(started)

	// One recover for the whole pass, plus one per check below. The outer
	// one is a backstop: it guarantees that even a panic in the finalize or
	// logging path still leaves a report behind rather than unwinding into
	// whatever periodic goroutine called us.
	defer func() {
		if r := recover(); r != nil {
			report.CheckErrors["panic"] = fmt.Sprintf("inspection panicked: %v", r)
			report.DurationMS = i.now().Sub(started).Milliseconds()
			report.finalize()
			i.store(report)
			logger.Errorf(ctx, "[Healthcheck] inspection pass panicked: %v", r)
		}
	}()

	i.runCheck(ctx, report, CheckDanglingReference, func(ctx context.Context) ([]Finding, error) {
		return i.checkDanglingReferences(ctx)
	})
	i.runCheck(ctx, report, CheckDeadLetterBacklog, func(ctx context.Context) ([]Finding, error) {
		return i.checkDeadLetterBacklog(ctx, i.now())
	})
	i.runCheck(ctx, report, CheckExtractionBacklog, func(ctx context.Context) ([]Finding, error) {
		return i.checkExtractionBacklog(ctx)
	})
	i.runCheck(ctx, report, CheckDeadColumns, func(ctx context.Context) ([]Finding, error) {
		return checkDeadColumns(i.cfg.MaxFindingsPerCheck)
	})
	i.runCheck(ctx, report, CheckToolReachability, func(ctx context.Context) ([]Finding, error) {
		return i.checkToolReachability(ctx)
	})

	report.DurationMS = i.now().Sub(started).Milliseconds()
	report.finalize()
	i.store(report)
	i.log(ctx, report)
	return report
}

// runCheck isolates one check. A panicking or failing check is recorded in
// CheckErrors and the pass continues, because a report of four checks is
// strictly more useful than no report at all.
func (i *Inspector) runCheck(
	ctx context.Context, report *Report, name string, fn func(context.Context) ([]Finding, error),
) {
	defer func() {
		if r := recover(); r != nil {
			report.CheckErrors[name] = fmt.Sprintf("check panicked: %v", r)
		}
	}()
	findings, err := fn(ctx)
	if err != nil {
		report.CheckErrors[name] = err.Error()
		return
	}
	if len(findings) > i.cfg.MaxFindingsPerCheck {
		truncated := len(findings) - i.cfg.MaxFindingsPerCheck
		findings = findings[:i.cfg.MaxFindingsPerCheck]
		findings = append(findings, Finding{
			Check:    name,
			Severity: SeverityInfo,
			Object:   fmt.Sprintf("check %s", name),
			Detail: fmt.Sprintf("%d further finding(s) from this check were not listed",
				truncated),
		})
	}
	report.Findings = append(report.Findings, findings...)
}

func (i *Inspector) store(r *Report) {
	i.mu.Lock()
	defer i.mu.Unlock()
	i.last = r
	if r.Status != StatusUnknown {
		i.lastOK = r.StartedAt
	}
}

// log writes the verdict, always.
//
// The pass logs even when it finds nothing. A silent pass and a pass that
// never ran look identical in a log stream unless the checker says which
// one happened, so it says so on every pass.
func (i *Inspector) log(ctx context.Context, r *Report) {
	switch r.Status {
	case StatusDegraded:
		logger.Errorf(ctx, "[Healthcheck] %s", r.Summary())
	case StatusUnknown:
		logger.Warnf(ctx, "[Healthcheck] %s", r.Summary())
	default:
		logger.Infof(ctx, "[Healthcheck] %s", r.Summary())
	}
	if !i.cfg.LogFindings {
		return
	}
	for _, f := range r.Findings {
		switch f.Severity {
		case SeverityCritical:
			logger.Errorf(ctx, "[Healthcheck][%s] %s", f.Check, f)
		case SeverityWarning:
			logger.Warnf(ctx, "[Healthcheck][%s] %s", f.Check, f)
		default:
			logger.Infof(ctx, "[Healthcheck][%s] %s", f.Check, f)
		}
	}
}

// StartPeriodic runs the inspection once immediately and then on an
// interval until ctx is cancelled.
//
// The first pass is synchronous-ish on a background goroutine, not inline
// in the caller, so a slow database cannot delay serving. It is *not*
// deferred past startup entirely: a dangling reference found at boot is
// worth having in the log before the first request arrives.
//
// A nil Inspector is safe to pass: startup must not break because the
// checker is absent.
func (i *Inspector) StartPeriodic(ctx context.Context, interval time.Duration) {
	i.StartPeriodicUntil(ctx, interval, nil)
}

// StartPeriodicUntil is StartPeriodic with an explicit stop channel, for a
// caller that manages shutdown through the repo's ResourceCleaner rather
// than a context.
//
// stop is a receive-only channel that is never sent on: closing it ends the
// loop. A nil stop means "run until ctx is done".
func (i *Inspector) StartPeriodicUntil(ctx context.Context, interval time.Duration, stop <-chan struct{}) {
	if i == nil {
		logger.Warnf(ctx, "[Healthcheck] inspector is nil; configuration inspection disabled")
		return
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if interval <= 0 {
		interval = DefaultInterval
	}
	go func() {
		i.Run(ctx)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				logger.Infof(ctx, "[Healthcheck] periodic inspection stopped")
				return
			case <-stop:
				logger.Infof(ctx, "[Healthcheck] periodic inspection stopped")
				return
			case <-ticker.C:
				i.Run(ctx)
			}
		}
	}()
}

// registeredToolNames is the set of tool names the binary can register.
//
// It is a function reference rather than a field so the embedded source scan
// stays lazily evaluated, and so a test can substitute a set without a
// database. The scan itself lives in package agent, which is the only place
// go:embed can reach the whole internal/agent/tools tree — see that file for
// why the obvious location silently misses most tools.
var registeredToolNames = agent.RegisteredToolNames
