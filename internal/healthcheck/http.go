package healthcheck

import (
	"context"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/logger"
)

// Readiness is the degraded signal. It is a separate surface from liveness on
// purpose.
//
// /health stays exactly as it was — a static 200 — because that is what the
// compose healthcheck and `cli doctor` probe. A finding must not be able to
// turn a small configuration defect into a container restart, and a restart
// that does not fix the defect is strictly worse than the defect.
//
// This endpoint carries the findings instead. It answers "is this process
// healthy enough to trust?", which is a different question from "is this
// process alive?", and it answers it with the report in the body rather
// than with a status code alone.
//
// Status codes:
//
//	200  status=ok        the inspection ran, every check completed, nothing found
//	503  status=degraded  the inspection ran and found defects (or partial: incomplete)
//	503  status=unknown   the inspection has not run, or learned nothing
//
// The 503s are for operators and alerting. Nothing in the deployment probes
// this path, so a degraded verdict cannot pull the instance out of rotation —
// which is the required posture: report, never refuse to serve.
func (i *Inspector) ReadinessHandler(c *gin.Context) {
	report := i.Snapshot()
	if report == nil {
		// No completed pass. This is NOT reported as ok: an inspection that
		// has not run has verified nothing, and saying otherwise is the
		// original bug one layer up.
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"status":   StatusUnknown,
			"ready":    false,
			"detail":   "the configuration inspection has not completed a pass yet",
			"liveness": "see GET /health, which reports process liveness only",
			"checks":   AllChecks(),
		})
		return
	}

	code := http.StatusOK
	if report.Status != StatusOK {
		code = http.StatusServiceUnavailable
	}
	c.JSON(code, gin.H{
		"status":       report.Status,
		"ready":        report.Status == StatusOK,
		"started_at":   report.StartedAt,
		"duration_ms":  report.DurationMS,
		"incomplete":   report.Incomplete,
		"checks_run":   report.ChecksRun,
		"check_errors": report.CheckErrors,
		"summary":      report.Summary(),
		"findings":     report.Findings,
		"counts": gin.H{
			"total":    len(report.Findings),
			"critical": report.Critical,
			"warning":  report.Warning,
			"info":     report.Info,
		},
		// Stated in the payload so nobody has to guess which endpoint is
		// safe to probe when they decide what to wire up.
		"liveness": "GET /health is unaffected by findings and must stay the container healthcheck",
	})
}

// StartWithStartupPass runs the first inspection and then the periodic loop.
//
// The first pass is intentionally not awaited by the caller. It runs on the
// inspector's own goroutine so a slow or unreachable database delays the
// report but not the first request. That is the whole failure posture in one
// line: nothing here can delay or prevent serving.
func (i *Inspector) StartWithStartupPass(ctx context.Context, interval time.Duration) {
	if i == nil {
		logger.Warnf(ctx, "[Healthcheck] inspector is nil; configuration inspection disabled")
		return
	}
	i.StartPeriodic(ctx, interval)
}

// StartUntil is the stop-channel form, for a caller whose shutdown is driven
// by the repo's ResourceCleaner.
func (i *Inspector) StartUntil(ctx context.Context, interval time.Duration, stop <-chan struct{}) {
	if i == nil {
		logger.Warnf(ctx, "[Healthcheck] inspector is nil; configuration inspection disabled")
		return
	}
	i.StartPeriodicUntil(ctx, interval, stop)
}
