package healthcheck

import (
	"fmt"
	"strings"

	hithink "github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// checkDeadColumns is check 4: indicator columns that tool SQL queries but
// nothing reads.
//
// The analysis lives in the hithink_finance package because that is where
// the machinery lives. discover.go already embeds the tool subpackage
// sources and extracts the SELECT lists of their SQL literals at runtime;
// this check reuses that machinery from a thin adapter rather than writing
// a second SQL scanner that would have to be kept in sync with the tool
// sources forever. discover.go itself is untouched.
//
// Scope, stated plainly so the finding is not read as more than it is: the
// verdict is "named in a SELECT list and nowhere else in the SQL". It does
// not trace a value into a Go struct field, because the DuckDB access layer
// hands rows back as map[string]any and there is no static struct mapping
// to trace. A column consumed by a Go caller that only reads the resulting
// map is reported here; that is a known over-report, and it is the safe
// direction, because a false "this is dead" is a prompt to look and a false
// "this is fine" is the original incident.
func checkDeadColumns(limit int) ([]Finding, error) {
	dead := hithink.DeadColumns()
	if len(dead) == 0 {
		return nil, nil
	}

	findings := make([]Finding, 0, len(dead))
	for _, c := range dead {
		findings = append(findings, Finding{
			Check:    CheckDeadColumns,
			Severity: SeverityInfo,
			Object:   fmt.Sprintf("indicators.v_indicators_daily column %q", c.Column),
			Field:    c.Tools[0],
			Detail: fmt.Sprintf("is selected by %d hardcoded tool SQL literal(s) in %s but "+
				"never read: not passed to a function, not filtered, grouped or ordered on",
				len(c.Tools), strings.Join(c.Tools, ", ")),
			Remediation: "drop the column from the SELECT list, or use it — as it stands it " +
				"reads as a supported signal that no code acts on",
		})
	}
	if limit > 0 && len(findings) > limit {
		return findings[:limit], nil
	}
	return findings, nil
}
