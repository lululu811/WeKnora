// Package healthcheck is a startup-time and periodic inspection of silent
// configuration and pipeline failures.
//
// It exists because of a specific class of incident: a production bug that
// ran for three days, was never logged, never alerted, and never failed a
// test. The chain was a soft-deleted model row that a tenant's
// memory_config.extract_model_id still pointed at; every memory extraction
// resolved that model, failed, retried, dead-lettered, and the
// memory_extraction_sessions rows sat pending forever. memory_items never
// got a single row. The system reported success the whole time.
//
// Every check here follows the same three rules:
//
//  1. A finding is a defect to surface, never a reason to refuse to boot.
//     A stale configuration value must not turn a small problem into a full
//     outage, so nothing in this package can fail startup or block traffic.
//  2. A finding names the object, the bad value, and — wherever knowable —
//     what the value should be. "config invalid" is not actionable;
//     `agent "Smart Reasoning" model_id="builtin-llm-default" is
//     soft-deleted (deleted_at=...)` is.
//  3. "Ran and found nothing" must be distinguishable from "did not run".
//     See Report.CheckErrors and Status, which exist for exactly that.
package healthcheck

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// Status is the coarse verdict of one inspection pass.
//
// The distinction between StatusOK and StatusUnknown is the whole point of
// this package. A checker that silently reports nothing when it never ran is
// the same bug one layer up: the original incident produced no error, no
// alert, and no failing test while the subsystem was producing nothing.
type Status string

const (
	// StatusUnknown means the inspection has not produced a verdict yet, or
	// every check failed to run. It is deliberately NOT ok: absence of
	// evidence must not be reported as evidence of absence.
	StatusUnknown Status = "unknown"
	// StatusOK means the inspection ran and found nothing. Findings == 0 and
	// CheckErrors is empty or partial; see Report.Incomplete.
	StatusOK Status = "ok"
	// StatusDegraded means the inspection ran and found at least one defect.
	// The process is still serving traffic — degraded is a signal, not a
	// gate.
	StatusDegraded Status = "degraded"
)

// Check names. They appear in the report payload and in the log line, so
// an operator can grep for a specific check without reading the body.
const (
	CheckDanglingReference = "dangling_reference"
	CheckDeadLetterBacklog = "dead_letter_backlog"
	CheckExtractionBacklog = "extraction_backlog"
	CheckDeadColumns       = "dead_columns"
	CheckToolReachability  = "tool_reachability"
)

// AllChecks is the full roster, in the order findings are reported.
//
// Order is priority order: the dangling-reference check is first because it
// is the one that would have caught last night's incident directly, and it is
// the only one that names a specific wrong value a human can go fix.
func AllChecks() []string {
	return []string{
		CheckDanglingReference,
		CheckDeadLetterBacklog,
		CheckExtractionBacklog,
		CheckDeadColumns,
		CheckToolReachability,
	}
}

// Severity ranks a finding. Only SeverityCritical maps to a hard degraded
// status; the rest are advisory and never change process liveness.
type Severity string

const (
	SeverityCritical Severity = "critical"
	SeverityWarning  Severity = "warning"
	SeverityInfo     Severity = "info"
)

// Finding is one actionable defect.
//
// Object is the thing that is misconfigured, BadValue is the value that is
// wrong, and Remediation is what it should be instead. A finding missing
// BadValue is a bug in the check that produced it: an operator reading
// "config invalid" with no value cannot act, which is the failure mode
// being eliminated.
type Finding struct {
	// Check is one of the Check* constants.
	Check string `json:"check"`
	// Severity is one of the Severity* constants.
	Severity Severity `json:"severity"`
	// Object names the misconfigured thing, e.g.
	// `custom_agents:"Smart Reasoning" (id=builtin-smart-reasoning)`.
	Object string `json:"object"`
	// Field is the configuration path inside Object, e.g. `config->>'model_id'`.
	Field string `json:"field,omitempty"`
	// BadValue is the offending value, verbatim.
	BadValue string `json:"bad_value,omitempty"`
	// Detail is one sentence of plain-English explanation.
	Detail string `json:"detail"`
	// Remediation says what the value should be, when knowable. Empty means
	// the check could not determine a correct value, not that none exists.
	Remediation string `json:"remediation,omitempty"`
}

// String renders a finding as a single actionable log line.
//
// Every field an operator needs to start fixing is in this one line: the
// object, the field, the bad value, why it is wrong, and what to do.
func (f Finding) String() string {
	var b strings.Builder
	b.WriteString(f.Object)
	if f.Field != "" {
		b.WriteString(" field ")
		b.WriteString(f.Field)
	}
	if f.BadValue != "" {
		fmt.Fprintf(&b, " = %q", f.BadValue)
	}
	b.WriteString(" -> ")
	b.WriteString(f.Detail)
	if f.Remediation != "" {
		b.WriteString("; fix: ")
		b.WriteString(f.Remediation)
	}
	return b.String()
}

// Report is the result of one inspection pass.
//
// Findings and CheckErrors answer different questions and must not be
// conflated. Findings means "ran, and here is what is wrong". CheckErrors
// means "did not run, so this check knows nothing". A pass with zero
// findings and a non-empty CheckErrors has NOT cleared the system.
type Report struct {
	// Status is the coarse verdict derived from Findings and CheckErrors.
	Status Status `json:"status"`
	// StartedAt is when the pass began. A readiness consumer that sees a
	// stale StartedAt is looking at an inspection that died mid-pass.
	StartedAt time.Time `json:"started_at"`
	// DurationMS is how long the pass took, so a hung pass is visible as a
	// large duration rather than as an old timestamp alone.
	DurationMS int64 `json:"duration_ms"`
	// Incomplete is true when at least one check failed to run. It is
	// carried at the top level so a consumer that only reads `status` can
	// still tell a full pass from a partial one.
	Incomplete bool `json:"incomplete"`
	// ChecksRun lists every check the pass attempted, whether or not it
	// produced findings.
	ChecksRun []string `json:"checks_run"`
	// CheckErrors maps check name -> why it did not run. Absence of a name
	// here means the check ran.
	CheckErrors map[string]string `json:"check_errors,omitempty"`
	// Findings are the defects, ordered by check priority.
	Findings []Finding `json:"findings"`
	// Critical and Warning are precomputed counts for dashboards.
	Critical int `json:"critical"`
	Warning  int `json:"warning"`
	Info     int `json:"info"`
}

// newReport allocates a Report with the invariants already established:
// ChecksRun is the full roster and CheckErrors is an allocated map, so a
// caller never has to distinguish "no errors" from "errors map is nil".
func newReport(started time.Time) *Report {
	return &Report{
		Status:      StatusUnknown,
		StartedAt:   started,
		ChecksRun:   AllChecks(),
		CheckErrors: map[string]string{},
		Findings:    []Finding{},
	}
}

// finalize derives Status and the severity counts.
//
// The derivation is deliberately strict about claiming success. "ok" means
// this process can vouch for the configuration, which requires that every
// check actually ran:
//
//   - at least one finding                  -> degraded
//   - no findings but a check did not run   -> unknown, with the reason per
//     check. A partial pass that found nothing has not verified anything;
//     reporting it as ok is the lie this package exists to prevent.
//   - no findings and every check ran       -> ok
func (r *Report) finalize() {
	r.Critical, r.Warning, r.Info = 0, 0, 0
	for _, f := range r.Findings {
		switch f.Severity {
		case SeverityCritical:
			r.Critical++
		case SeverityWarning:
			r.Warning++
		default:
			r.Info++
		}
	}
	r.Incomplete = len(r.CheckErrors) > 0
	switch {
	case len(r.Findings) > 0:
		r.Status = StatusDegraded
	case r.Incomplete:
		r.Status = StatusUnknown
	default:
		r.Status = StatusOK
	}
}

// Summary renders the one-line form used in logs and in the readiness body.
func (r *Report) Summary() string {
	if r == nil {
		return "healthcheck: no report yet (inspection has not run)"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "healthcheck: %s, %d finding(s)", r.Status, len(r.Findings))
	if r.Critical > 0 {
		fmt.Fprintf(&b, " (%d critical)", r.Critical)
	}
	if r.Warning > 0 {
		fmt.Fprintf(&b, " (%d warning)", r.Warning)
	}
	if r.Info > 0 {
		fmt.Fprintf(&b, " (%d info)", r.Info)
	}
	if r.Incomplete {
		names := make([]string, 0, len(r.CheckErrors))
		for name := range r.CheckErrors {
			names = append(names, name)
		}
		sort.Strings(names)
		fmt.Fprintf(&b, "; INCOMPLETE, did not run: %s", strings.Join(names, ", "))
	}
	fmt.Fprintf(&b, "; took %dms", r.DurationMS)
	return b.String()
}
