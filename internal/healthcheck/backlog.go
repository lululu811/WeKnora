package healthcheck

import (
	"context"
	"fmt"
	"time"
)

// checkDeadLetterBacklog is check 2: task_dead_letters, grouped by task
// type, split into "currently failing" and "merely old".
//
// The split is the point. 782 summary:generation rows from a DNS incident
// five days ago and 27 memory:extract rows from last night are the same
// shape in the database and completely different problems: the first is a
// cleanup ticket, the second is an outage. A single undifferentiated count
// forces the reader to decide which one they are looking at, which is
// exactly the decision that stops anyone from looking at all.
//
// Both are always reported, with the newest failure timestamp in the detail.
// A reader who disagrees with the window thresholds still has the ages.
func (i *Inspector) checkDeadLetterBacklog(ctx context.Context, now time.Time) ([]Finding, error) {
	if i.cfg.DB == nil {
		return nil, fmt.Errorf("no database handle configured")
	}

	cols, rows, err := queryRows(ctx, i.cfg.DB,
		"SELECT task_type, COUNT(*) AS count, MIN(failed_at) AS oldest, MAX(failed_at) AS newest "+
			"FROM task_dead_letters GROUP BY task_type ORDER BY count DESC")
	if err != nil {
		return nil, fmt.Errorf("group dead letters by task type: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	idx := columnIndex(cols)

	findings := make([]Finding, 0, len(rows))
	for _, row := range rows {
		taskType := asString(row[idx["task_type"]])
		count, _ := parseDBInt(row[idx["count"]])
		// An unparseable timestamp is reported as such rather than as the
		// zero time: a 0001-01-01 "oldest" would read as 2000 years of
		// accumulated failure and is exactly the kind of invented urgency
		// that makes an operator stop trusting a report.
		newest, ok := parseDBTime(row[idx["newest"]])
		if !ok {
			return nil, fmt.Errorf("task_type %q: MAX(failed_at) is unreadable (%v)",
				taskType, row[idx["newest"]])
		}
		oldest, oldestOK := parseDBTime(row[idx["oldest"]])
		if !oldestOK {
			oldest = newest
		}

		age := now.Sub(newest)
		var severity Severity
		var verdict string
		switch {
		case age <= i.cfg.DeadLetterRecentWindow:
			// Newest entry is recent: this is failing right now.
			severity = SeverityCritical
			verdict = "CURRENTLY FAILING"
		case age > i.cfg.DeadLetterStaleWindow:
			// Nothing new for over a week: historical debris.
			severity = SeverityInfo
			verdict = "historical debris, no failures in the last " +
				i.cfg.DeadLetterStaleWindow.String()
		default:
			severity = SeverityWarning
			verdict = "stopped failing, backlog not yet cleared"
		}

		sample := i.newestDeadLetterError(ctx, taskType)
		detail := fmt.Sprintf(
			"%d dead letter(s), oldest %s, newest %s (%s ago)",
			count, oldest.UTC().Format(time.RFC3339),
			newest.UTC().Format(time.RFC3339), age.Round(time.Minute),
		)
		if sample != "" {
			detail += fmt.Sprintf("; most recent error: %s", truncate(sample, 200))
		}
		findings = append(findings, Finding{
			Check:    CheckDeadLetterBacklog,
			Severity: severity,
			Object:   fmt.Sprintf("task_dead_letters: task_type=%q", taskType),
			BadValue: fmt.Sprintf("%d rows", count),
			Detail:   verdict + ": " + detail,
			Remediation: func() string {
				if severity == SeverityInfo {
					return "prune or archive these rows; the underlying failure is no longer occurring"
				}
				return "find why this task type exhausts its retry budget; the rows are the only record of it"
			}(),
		})
	}
	return findings, nil
}

// newestDeadLetterError returns the error string of the most recent row for
// a task type.
//
// This is the field that turns a count into a diagnosis: "782 rows" says
// nothing, and the DNS incident's actual error named the failing host.
func (i *Inspector) newestDeadLetterError(ctx context.Context, taskType string) string {
	var row struct {
		LastError string `gorm:"column:last_error"`
	}
	err := i.cfg.DB.WithContext(ctx).Raw(
		"SELECT last_error FROM task_dead_letters WHERE task_type = ? ORDER BY failed_at DESC LIMIT 1",
		taskType,
	).Scan(&row).Error
	if err != nil {
		// A missing sample does not invalidate the count. Leave the detail
		// without an error excerpt rather than dropping the whole finding.
		return ""
	}
	return row.LastError
}

// checkExtractionBacklog is check 3: rows in memory_extraction_sessions
// still pending.
//
// The "stuck" test is not reimplemented here. It is the reconciler's:
// StuckExtractions applies the same reconcileStaleAfter cutoff the repair
// pass uses, so the number this check reports and the number the reconciler
// acts on cannot drift apart. Two notions of "stale" in one repo is precisely
// the drift class this package exists to catch, and inventing a third here
// would have been the same bug in a new place.
func (i *Inspector) checkExtractionBacklog(ctx context.Context) ([]Finding, error) {
	if i.cfg.DB == nil {
		return nil, fmt.Errorf("no database handle configured")
	}
	if i.cfg.MemoryService == nil {
		return nil, fmt.Errorf("memory service not wired; extraction backlog is unknown, not zero")
	}

	// Total pending is a plain count, not a staleness judgement, so it is
	// read directly: the total is what tells you whether the wedged subset
	// is 2 rows or 2 million.
	var pending int64
	if err := i.cfg.DB.WithContext(ctx).
		Raw("SELECT COUNT(*) FROM memory_extraction_sessions WHERE pending = ?", true).
		Scan(&pending).Error; err != nil {
		return nil, fmt.Errorf("count pending memory extraction sessions: %w", err)
	}

	stuck, err := i.cfg.MemoryService.StuckExtractions(ctx, i.cfg.StuckExtractionLimit)
	if err != nil {
		return nil, fmt.Errorf("list stuck memory extractions: %w", err)
	}
	if len(stuck) == 0 {
		return nil, nil
	}

	sample := ""
	if len(stuck) > 0 {
		r := stuck[0]
		sample = fmt.Sprintf("oldest stuck row: tenant=%d subject=%s session=%s untouched since %s "+
			"(failure_count=%d failure_code=%q)",
			r.TenantID, truncate(r.SubjectID, 64), truncate(r.SessionID, 64),
			r.UpdatedAt.UTC().Format(time.RFC3339), r.FailureCount, r.FailureCode)
	}
	truncated := ""
	if pending > int64(len(stuck)) {
		truncated = fmt.Sprintf("; %d pending row(s) in total", pending)
	}
	detail := fmt.Sprintf("%d row(s) have been pending=true long past the in-flight window "+
		"(the memory reconciler applies the same reconcileStaleAfter cutoff) — the durable queue "+
		"is wedged, so nothing is being learned from these conversations%s", len(stuck), truncated)
	if sample != "" {
		detail += "; " + sample
	}
	return []Finding{{
		Check:    CheckExtractionBacklog,
		Severity: SeverityWarning,
		Object:   "memory_extraction_sessions",
		BadValue: fmt.Sprintf("%d pending", pending),
		Detail:   detail,
		Remediation: "check the memory reconciler is scheduled and that the tenant's " +
			"memory_config extract_model_id resolves; then confirm memory_items is growing",
	}}, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
