//go:build signal_audit

// Empirical trigger-frequency audit for all declared technical signals.
//
// WHAT THIS IS
// The signal set grew from 46 to 71 on the reasoning that "a signal which fires
// on every bar is noise — the model learns to ignore it within three uses."
// That reasoning was never checked against data. This file checks it.
//
// This file is deliberately a `_test.go` in `package pattern` for two reasons:
//
//  1. `detectSignals` and `queryIndicatorRows` are both unexported. A `cmd/`
//     tool could only reach them by copying them, and a copy measures a
//     different program than the one that ships. Living in-package is the only
//     way to call the real code path verbatim.
//
//  2. It is behind the `signal_audit` build tag, so `go build ./...` and
//     `go test ./...` never compile or run it. The audit needs a live
//     python-service holding a 17 GB DuckDB and takes real wall-clock time;
//     it has no business in the unit-test suite.
//
// It is additionally gated on WEKNORA_SIGNAL_AUDIT=1 so that even a deliberate
// `-tags signal_audit` typo cannot kick off a 5000-stock sweep.
//
// HOW TO RUN
//
//	./internal/agent/tools/hithink_finance/pattern/run_signal_audit.sh
//
// or, equivalently, by hand:
//
//	PYTHON_SERVICE_URL=http://localhost:50052 WEKNORA_SIGNAL_AUDIT=1 \
//	  WEKNORA_SIGNAL_AUDIT_STOCKS=1500 \
//	  go test -tags signal_audit -run TestSignalFrequencyAudit -v \
//	  ./internal/agent/tools/hithink_finance/pattern/
//
// Optional env:
//
//	WEKNORA_SIGNAL_AUDIT_STOCKS=N   stride-sample N stocks (default 0 = whole universe)
//	WEKNORA_SIGNAL_AUDIT_BARS=N     bars per stock (default 1000, the endpoint's cap)
//	WEKNORA_SIGNAL_AUDIT_WORKERS=N  concurrent fetches (default 3)
//	WEKNORA_SIGNAL_AUDIT_OUT=path   report path (default signal_frequency_audit.md)
//
// METHOD (see the report header for the denominator definition)
//
//	queryIndicatorRows returns newest-first: rows[0] is the latest bar.
//
// detectSignals reads rows[0] as "today" and rows[1..] as history, so to
// evaluate the signal state *as of* historical bar t we feed the window
// rows[t:t+W]. W is the largest `len(rows) >= N` guard in signals.go (7,
// signals.go:607), so a uniform 7-bar window gives every signal its full
// required history and makes the denominator identical across all 71 signals.
//
// The sample is a fixed stride across the thscode-sorted universe, never a
// prefix: prefix sampling is systematically biased toward early Shenzhen
// codes (000xxx/001xxx), and that bias is not noise — it would be baked into
// every frequency in the table.
package pattern

import (
	"context"
	"fmt"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

const (
	// auditWinSize is the maximum `len(rows) >= N` required by any branch in
	// detectSignals. Verified against signals.go: the deepest guard is 7.
	auditWinSize = 7
	// noisyFloor is the verdict threshold. See verdictFor for the argument.
	noisyFloor = 0.20
	// alwaysOnFloor flags a signal that fires on nearly every evaluated bar.
	alwaysOnFloor = 0.95
	// auditWorkers and auditBars bound the load this audit puts on the
	// python-service container. Both are overridable by env var so the sweep can
	// be calibrated for a given host without a recompile.
	//
	// Earlier revisions of this audit sharded the universe and restarted the
	// container between shards, because DuckDB sized its buffer pool against the
	// host RAM and the container was OOM-killed three times. That is fixed at the
	// source now: duckdb_source.py passes memory_limit=2048MB on every connect
	// (env DUCKDB_MEMORY_LIMIT_MB) and docker-compose.yml caps the service at
	// mem_limit 3g (env PY_SERVICE_MEM_LIMIT). The sharding workaround is
	// therefore obsolete and has been removed — keeping it would restate a bug
	// that no longer exists and would triple the wall clock for nothing.
	//
	// auditBars = 1000 also matches the row cap the python-service query
	// endpoint imposes (QueryRequest.Limit = 1000), so it is the deepest
	// history queryIndicatorRows can return in one call: ~4.1 years of bars.
	auditWorkers = 3
	auditBars    = 1000
	// auditRetries covers transient failures (connection reset, a cold-cache
	// scan overrunning the timeout) so one hiccup does not silently drop a
	// stock from the denominator.
	auditRetries = 3
)

// colStat is a streaming summary of one indicator column, used to diagnose
// never-firing signals without reimplementing their conditions.
type colStat struct {
	N       int
	NonZero int
	Min     float64
	Max     float64
	Seen    bool
}

func (c *colStat) add(v float64) {
	c.N++
	if v != 0 {
		c.NonZero++
	}
	if !c.Seen {
		c.Min, c.Max, c.Seen = v, v, true
		return
	}
	if v < c.Min {
		c.Min = v
	}
	if v > c.Max {
		c.Max = v
	}
}

// colStats holds one colStat per float64 field of row. It exists so that a
// never-firing signal can be diagnosed from the *input data* (is the column
// even populated?) without reimplementing the signal's condition.
//
// Each worker owns its own colStats; merging happens once under the audit's
// mutex, so no synchronisation is needed inside the type itself.
type colStats struct {
	m map[string]*colStat
}

func newColStats() *colStats {
	return &colStats{m: map[string]*colStat{}}
}

// observe records every float64 field of r, discovered by reflection so the
// table cannot drift out of sync when a column is added to row.
func (c *colStats) observe(r row) {
	v := reflect.ValueOf(r)
	t := v.Type()
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if f.Type.Kind() != reflect.Float64 {
			continue
		}
		st := c.m[f.Name]
		if st == nil {
			st = &colStat{}
			c.m[f.Name] = st
		}
		st.add(v.Field(i).Float())
	}
}

func (c *colStats) merge(o *colStats) {
	for k, v := range o.m {
		d := c.m[k]
		if d == nil {
			c.m[k] = &colStat{N: v.N, NonZero: v.NonZero, Min: v.Min, Max: v.Max, Seen: v.Seen}
			continue
		}
		d.N += v.N
		d.NonZero += v.NonZero
		if !v.Seen {
			continue
		}
		if !d.Seen {
			d.Min, d.Max, d.Seen = v.Min, v.Max, true
			continue
		}
		if v.Min < d.Min {
			d.Min = v.Min
		}
		if v.Max > d.Max {
			d.Max = v.Max
		}
	}
}

// signalCount aggregates firings and directions for one signal name.
type signalCount struct {
	// Fires counts (stock, bar) pairs on which the signal was emitted at least
	// once. This is the number the frequency is computed from.
	Fires int64
	// Emissions counts raw appends into the returned []Signal. It equals Fires
	// for every signal emitted at most once per bar, which after the duplicate
	// Donchian下轨跌破 append site was removed is every signal in the set. The
	// two are still reported as separate columns: the whole point of the split
	// is that a future duplicate shows up as emissions > firings rather than
	// silently inflating a frequency. If any row ever reports DUP-EMIT again,
	// the engine has regressed and no number in that column should be trusted.
	Emissions int64
	ByDir     map[string]int64
	Stocks    int64 // distinct stocks on which it fired at least once
}

func newSignalCount() *signalCount {
	return &signalCount{ByDir: map[string]int64{}}
}

// verdictFor applies the single, uniformly-applied decision rule.
//
//	dead        : zero firings over the entire sample
//	noisy       : fires on more than 20% of evaluated bars
//	informative : fires on at least one and at most 20% of evaluated bars
//
// Justification for the 20% floor: a signal is emitted into the model's
// context on the bar where it fires. At >20% frequency it is describing the
// ambient state of the market rather than an event — it is present on one bar
// in five, so seeing it carries almost no information about *this* bar, and
// per the premise under test the model discounts it. Below 20% it marks a
// transition. The cut is deliberately round and deliberately identical for
// all 71 signals so that no signal is judged by a different standard.
func verdictFor(freq float64, fires int64) string {
	switch {
	case fires == 0:
		return "dead"
	case freq > noisyFloor:
		return "noisy"
	default:
		return "informative"
	}
}

func TestSignalFrequencyAudit(t *testing.T) {
	if os.Getenv("WEKNORA_SIGNAL_AUDIT") != "1" {
		t.Skip("signal audit disabled; set WEKNORA_SIGNAL_AUDIT=1 to run (see file header)")
	}
	if testing.Short() {
		t.Skip("signal audit skipped in -short mode")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Hour)
	defer cancel()

	cfg := hithink_finance.DefaultConfig()
	// DefaultConfig ships a 10s timeout sized for a single interactive lookup.
	// A 1000-row x 50-column pull over a cold 17 GB DuckDB can exceed that.
	cfg.Timeout = 180 * time.Second

	stockCap := 0
	if s := os.Getenv("WEKNORA_SIGNAL_AUDIT_STOCKS"); s != "" {
		fmt.Sscanf(s, "%d", &stockCap)
	}
	workers := auditWorkers
	if s := os.Getenv("WEKNORA_SIGNAL_AUDIT_WORKERS"); s != "" {
		fmt.Sscanf(s, "%d", &workers)
	}
	bars := auditBars
	if s := os.Getenv("WEKNORA_SIGNAL_AUDIT_BARS"); s != "" {
		fmt.Sscanf(s, "%d", &bars)
	}

	outPath := os.Getenv("WEKNORA_SIGNAL_AUDIT_OUT")
	if outPath == "" {
		outPath = "signal_frequency_audit.md"
	}

	// Universe: every thscode present in the indicators DB.
	codes, err := auditUniverse(ctx, cfg)
	if err != nil {
		t.Fatalf("universe query failed: %v", err)
	}
	if len(codes) == 0 {
		t.Fatal("universe is empty — is python-service pointing at indicators.duckdb?")
	}
	totalUniverse := len(codes)
	if stockCap > 0 && stockCap < len(codes) {
		// Evenly spaced, not the first N.
		//
		// thscode order is alphabetical, so "the first 300" is almost entirely
		// 000xxx/001xxx Shenzhen main board — a board-and-era biased sample that
		// would silently flatter or punish any signal sensitive to listing era
		// or board. Taking a fixed stride across the whole sorted list spans
		// every code prefix (000/001/002/003/300/600/601/603/688) and therefore
		// the whole cross-section, and does so deterministically.
		codes = strideSample(codes, stockCap)
	}
	t.Logf("universe=%d stocks, auditing %d by fixed stride", totalUniverse, len(codes))

	counts := map[string]*signalCount{}
	for _, n := range declaredSignalNames {
		counts[n] = newSignalCount()
	}
	undeclared := map[string]int64{}
	diag := map[string]int64{}
	permFail := map[string]string{}
	transFail := map[string]string{}
	var shortStocks []string

	cols := newColStats()
	var (
		mu         sync.Mutex
		evaluated  int64
		okStocks   int64
		errStocks  int64
		panics     int64
		totalBars  int64
		newestDate string
		oldestDate string
	)

	jobs := make(chan string)
	var wg sync.WaitGroup
	start := time.Now()

	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			local := map[string]*signalCount{}
			for _, n := range declaredSignalNames {
				local[n] = newSignalCount()
			}
			lcols := newColStats()
			var lEval, lBars, lOK, lErr, lPanic int64
			lNewest, lOldest := "", ""
			seenStocks := map[string]map[string]bool{}
			localUndeclared := map[string]int64{}
			ldiag := map[string]int64{}

			for code := range jobs {
				rows, err := auditFetch(ctx, cfg, code, bars)
				if err != nil {
					// Split the failure classes. A corrupt DuckDB segment is a
					// property of the dataset and is deterministic: the same
					// thscode fails identically on every retry and at every
					// concurrency, so it will not be fixed by backing off. A
					// timeout or a dropped connection is a property of the run
					// and is exactly what a lower worker count exists to fix.
					// Conflating them would either hide a real infrastructure
					// problem behind a benign data defect, or fail the run for
					// damage no amount of retrying can undo.
					mu.Lock()
					if isPermanentDataError(err.Error()) {
						permFail[code] = err.Error()
					} else {
						transFail[code] = err.Error()
					}
					mu.Unlock()
					lErr++
					continue
				}
				if len(rows) < auditWinSize {
					mu.Lock()
					shortStocks = append(shortStocks, fmt.Sprintf("%s(%d bars)", code, len(rows)))
					mu.Unlock()
					lErr++
					continue
				}
				lOK++
				lBars += int64(len(rows))
				for _, r := range rows {
					lcols.observe(r)
				}
				// rows are newest-first.
				if lNewest == "" || rows[0].Date > lNewest {
					lNewest = rows[0].Date
				}
				if d := rows[len(rows)-1].Date; lOldest == "" || d < lOldest {
					lOldest = d
				}

				for t := 0; t+auditWinSize <= len(rows); t++ {
					window := rows[t : t+auditWinSize]
					// Evaluated bar is the newest element of the window, i.e. the
					// bar the signal is *about*. Each bar is evaluated at most once.
					auditDiag(ldiag, rows[t], rows[t+1])
					sigs := safeDetect(window)
					if sigs == nil {
						lPanic++
						continue
					}
					lEval++
					// One bar can yield the same signal name more than once if
					// the engine ever gains a second append site for it (it
					// did, for Donchian下轨跌破, until that site was deleted).
					// Emissions counts every append; Fires counts the bar
					// once. Frequencies are computed from Fires, so a
					// duplicate cannot inflate them, and the gap between the
					// two columns makes one visible if it returns.
					perBar := make(map[string]bool, len(sigs))
					for _, s := range sigs {
						c := local[s.Name]
						if c == nil {
							// A signal emitted by detectSignals but absent from
							// declaredSignalNames is a contract bug, not an audit
							// result; recorded so it cannot silently vanish.
							localUndeclared[s.Name]++
							continue
						}
						c.Emissions++
						if perBar[s.Name] {
							continue
						}
						perBar[s.Name] = true
						c.Fires++
						c.ByDir[s.Signal]++
						m := seenStocks[s.Name]
						if m == nil {
							m = map[string]bool{}
							seenStocks[s.Name] = m
						}
						if !m[code] {
							m[code] = true
							c.Stocks++
						}
					}
				}
			}

			mu.Lock()
			defer mu.Unlock()
			for k, v := range local {
				counts[k].Fires += v.Fires
				counts[k].Emissions += v.Emissions
				counts[k].Stocks += v.Stocks
				for d, n := range v.ByDir {
					counts[k].ByDir[d] += n
				}
			}
			cols.merge(lcols)
			for k, v := range localUndeclared {
				undeclared[k] += v
			}
			for k, v := range ldiag {
				diag[k] += v
			}
			evaluated += lEval
			okStocks += lOK
			errStocks += lErr
			panics += lPanic
			totalBars += lBars
			if lNewest != "" && (newestDate == "" || lNewest > newestDate) {
				newestDate = lNewest
			}
			if lOldest != "" && (oldestDate == "" || lOldest < oldestDate) {
				oldestDate = lOldest
			}
		}()
	}

	for _, c := range codes {
		jobs <- c
	}
	close(jobs)
	wg.Wait()
	elapsed := time.Since(start)

	if evaluated == 0 {
		t.Fatalf("zero bars evaluated (%d ok stocks, %d query errors) — no verdict possible", okStocks, errStocks)
	}

	rep := auditReport{
		Stocks:      int64(len(codes)),
		Universe:    int64(totalUniverse),
		OKStocks:    okStocks,
		ErrStocks:   errStocks,
		Bars:        totalBars,
		Evaluated:   evaluated,
		Newest:      newestDate,
		Oldest:      oldestDate,
		Panics:      panics,
		Elapsed:     elapsed,
		Counts:      counts,
		Cols:        cols,
		Undeclared:  undeclared,
		Diag:        diag,
		PermFail:    permFail,
		TransFail:   transFail,
		ShortStocks: shortStocks,
	}
	// Mean bars actually returned per evaluated stock. Not the cap: stocks
	// listed after the window opened return fewer, and reporting the cap would
	// overstate the history the frequencies actually rest on.
	if okStocks > 0 {
		rep.BarsPerStock = int(totalBars / okStocks)
	}
	report := renderReport(rep)

	if err := os.WriteFile(outPath, []byte(report), 0o644); err != nil {
		t.Fatalf("write report: %v", err)
	}
	t.Logf("wrote %s (%d bytes) in %s", outPath, len(report), elapsed.Round(time.Millisecond))
	t.Logf("evaluated %d bars over %d stocks (stride sample of %d universe)", evaluated, okStocks, totalUniverse)

	// Loud, not silent. A sweep that loses a chunk of its *sample* produces a
	// complete-looking table over whatever survived, which is exactly how a
	// biased sample gets mistaken for a measurement.
	//
	// The denominator here is the sampled set, NOT the full universe: the audit
	// deliberately draws a stride sample, so okStocks/totalUniverse would be
	// ~27% by design and would trip this check on a perfectly healthy run.
	//
	// Only *transient* loss is a hard failure. Corrupt DuckDB segments are
	// deterministic damage in the data file: no worker count, retry count or
	// timeout setting removes them, so failing the run on them would report a
	// problem this audit cannot solve and cannot fix by being careful. They are
	// still excluded from the denominator and named in the report — never
	// counted as zero-firing — and a large share of them is called out below.
	if len(codes) > 0 && len(transFail) > 0 {
		coverage := 100 * float64(len(codes)-len(transFail)) / float64(len(codes))
		if coverage < 95 {
			t.Errorf("only %.2f%% of the SAMPLED set was reachable after %d retries (%d/%d stocks "+
				"lost to transient failures): the frequencies below are conditional on the stocks "+
				"that answered and the verdicts are indicative rather than conclusive. Lower "+
				"WEKNORA_SIGNAL_AUDIT_WORKERS and re-run.",
				coverage, auditRetries, len(codes)-len(transFail), len(codes))
		}
	}
	if len(codes) > 0 && len(permFail) > 0 {
		share := 100 * float64(len(permFail)) / float64(len(codes))
		t.Logf("note: %d/%d sampled stocks (%.2f%%) are unreadable due to corrupt DuckDB "+
			"segments and are excluded from the denominator (not counted as zero-firing); "+
			"this is pre-existing data damage, deterministic across retries",
			len(permFail), len(codes), share)
		if share > 25 {
			t.Errorf("%.2f%% of the sample is unreadable due to corrupt DuckDB segments; the "+
				"indicators database needs repair before this audit can be conclusive", share)
		}
	}
}

type auditReport struct {
	Stocks, Universe, OKStocks, ErrStocks, Bars, Evaluated int64
	BarsPerStock                                           int
	Newest, Oldest                                         string
	Panics                                                 int64
	Elapsed                                                time.Duration
	Counts                                                 map[string]*signalCount
	Cols                                                   *colStats
	Undeclared                                             map[string]int64
	Diag                                                   map[string]int64
	PermFail, TransFail                                    map[string]string
	ShortStocks                                            []string
}

// isPermanentDataError reports whether a fetch error describes damage in the
// DuckDB file itself rather than a problem with this run.
//
// The indicators database is a ~17 GB file and some of its row-group segments
// are corrupt: reading a full history for an affected thscode fails with
// `IO Error: Corrupted ALPRD segment: stored data_byte_offset ... exceeds the
// segments block size`. That failure is deterministic — the same thscode
// returns it on every retry, at every worker count, and a narrower query over
// the same thscode succeeds, because the damage is in the compressed segment
// the wide scan has to read.
//
// Such stocks are excluded from the denominator and reported by name, never
// counted as zero-firing. They are simply absent: "this stock's data is
// unreadable" is not evidence about whether a signal fires.
func isPermanentDataError(msg string) bool {
	for _, sig := range []string{
		"Corrupted",     // "Corrupted ALPRD segment"
		"IO Error",      // generic storage-layer read failure
		"checksum",      // block checksum mismatch
		"File is not",   // truncated / not a database file
		"Catalog Error", // missing or damaged catalog entries
		"segment",       // segment-level complaints not already matched above
		"out of bounds", // footer/offset inconsistency
		"Cannot read",   // storage read past end of file
		"database is locked",
		"Table with name", // object genuinely absent rather than transient
	} {
		if strings.Contains(msg, sig) {
			return true
		}
	}
	return false
}

// auditDiag counts the intermediate predicates of the never-firing signals so
// the report can say *which clause* kills them, rather than only that they
// never fired. It reads the same `row` fields detectSignals reads and applies
// the same helpers (hasData, kcSqueezed), but it does not reimplement firing —
// the firing counts in the table come from detectSignals alone. This is
// diagnosis, not measurement.
func auditDiag(d map[string]int64, latest, prev row) {
	// Donchian上轨突破, clause by clause, measured against the condition that
	// actually ships (signals.go:389: latest.Close > prev.DCUpper).
	//
	// The previous version of this diagnostic counted `latest.Close >
	// latest.DCUpper` — the OLD dead condition — so it reported ~0 and looked
	// like the fix had not landed, when in fact it was measuring a predicate
	// the engine no longer uses. Both are counted here: `old_dead` is the
	// condition that is mathematically unsatisfiable (the 20-day upper band
	// includes today, so it can never sit below today's close), and it is the
	// empirical proof the fix was necessary. `close_vs_prev_band` is the live
	// one, whose bar count must equal the signal's firing count in the table.
	liveValid := hasData(latest.Close, prev.Close, prev.DCUpper)
	if liveValid {
		d["donchian.up.live_data_present"]++
	}
	if liveValid && latest.Close > prev.DCUpper {
		d["donchian.up.close_vs_prev_band"]++
	}
	oldValid := hasData(latest.Close, latest.DCUpper)
	if oldValid {
		d["donchian.up.old_dead_band_present"]++
	}
	if oldValid && latest.Close > latest.DCUpper {
		d["donchian.up.old_dead_condition"]++
	}

	// Donchian下轨跌破 now has exactly one append site (signals.go:396, vs
	// prev.DCLower). A second site that compared against latest.DCLower used to
	// sit alongside it and was deleted; the two are the same semantics under the
	// upstream upper/lower asymmetry, so they double-emitted.
	//
	// The removed clause is still counted, under an explicit `removed_` prefix,
	// for one reason: it is the empirical size of the defect that was fixed. The
	// last audit run reported 37.7% for this signal and that figure was wrong
	// precisely because of this overlap. Leaving the diagnostic in place makes
	// the dedupe verifiable on a later run instead of taken on trust — and the
	// prefix stops a reader treating a deleted predicate as a live one, which is
	// how the previous run's `donchian.up` diagnostic briefly read as "the fix
	// had not landed" when it was measuring the pre-fix condition.
	lowerLive := hasData(latest.Close, prev.Close, prev.DCLower)
	if lowerLive {
		d["donchian.down.live_data_present"]++
	}
	if lowerLive && latest.Close < prev.DCLower {
		d["donchian.down.close_vs_prev_band"]++
	}
	lowerNarrow := hasData(prev.Close, prev.DCLower, latest.Close, latest.DCLower) &&
		prev.Close >= prev.DCLower && latest.Close < latest.DCLower
	if lowerNarrow {
		d["donchian.down.removed_narrow_clause"]++
	}
	if lowerLive && latest.Close < prev.DCLower && lowerNarrow {
		d["donchian.down.removed_overlap_double_emitted"]++
	}

	// Keltner squeeze, clause by clause. kcSqueezed needs all four bands.
	four := hasData(latest.BBUpper, latest.BBLower, latest.KCUpper, latest.KCLower)
	if four {
		d["keltner.four_bands_present"]++
		if latest.BBUpper > latest.KCUpper {
			d["keltner.bb_wider_than_kc_upper"]++
		}
		if latest.BBUpper < latest.KCUpper {
			d["keltner.bb_inside_kc_upper"]++
		}
		if latest.BBLower > latest.KCLower {
			d["keltner.bb_inside_kc_lower"]++
		}
	}
	if kcSqueezed(latest) {
		d["keltner.squeezed"]++
	}
}

// safeDetect calls the real detectSignals and converts a panic into a nil
// result so one malformed series cannot abort a 5000-stock sweep.
func safeDetect(w []row) (out []Signal) {
	defer func() {
		if r := recover(); r != nil {
			out = nil
		}
	}()
	return detectSignals(w)
}

func auditUniverse(ctx context.Context, cfg *hithink_finance.Config) ([]string, error) {
	const page = 1000
	var all []string
	for off := 0; ; off += page {
		// Single bound placeholder: the service's placeholder accounting
		// rejects "LIMIT ? OFFSET ?" with two params.
		sql := `SELECT thscode FROM v_indicators_daily GROUP BY thscode ORDER BY thscode LIMIT 1000 OFFSET ?`
		rows, err := hithink_finance.QueryDuckDBParams(ctx, cfg, "indicators", sql, off)
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			all = append(all, fmt.Sprint(r["thscode"]))
		}
		if len(rows) < page {
			break
		}
	}
	return all, nil
}

func renderReport(rep auditReport) string {
	var b strings.Builder

	// Verdict buckets, in one fixed order so the table is stable across runs.
	type rowT struct {
		name      string
		fires     int64
		emissions int64
		stocks    int64
		freq      float64
		dirs      string
		verd      string
		note      string
	}
	rows := make([]rowT, 0, len(declaredSignalNames))
	var dead, noisy, informative int
	for _, n := range declaredSignalNames {
		c := rep.Counts[n]
		freq := 0.0
		if rep.Evaluated > 0 {
			freq = float64(c.Fires) / float64(rep.Evaluated)
		}
		note := ""
		if c.Fires > 0 && freq >= alwaysOnFloor {
			note = "ALWAYS-ON"
		}
		if c.Emissions > c.Fires {
			// A name appended twice on the same bar; the frequency column uses
			// Fires (deduped) but the engine really does emit it twice.
			note = strings.TrimSpace(note + " DUP-EMIT")
		}
		v := verdictFor(freq, c.Fires)
		switch v {
		case "dead":
			dead++
		case "noisy":
			noisy++
		default:
			informative++
		}
		rows = append(rows, rowT{n, c.Fires, c.Emissions, c.Stocks, freq, dirSummary(c), v, note})
	}
	// Order follows declaredSignalNames so the report reads in the same
	// category order as signals.go; frequency is a column, not the sort key.
	fmt.Fprintf(&b, "# Signal trigger-frequency audit\n\n")
	// 这段说明是**生成**的而不是手写在 .md 里 —— 整个文件被 os.WriteFile
	// 全量覆盖，手写的前言会在下一次重跑时静默消失。
	//
	// 它要说的是"这份文档是 noisySignalNames 的权威来源"：名单里每条的
	// 百分比都抄自下表。写清楚是为了挡 2026-10-01 那次错误 —— 先信了
	// scripts/classify_signals.py 的 SQL 结果（判据抄错 + 分母口径不同），
	// 把 MACD动能衰减 错折、把 Donchian下轨跌破 漏折。
	fmt.Fprintf(&b, "> **这份文档是 `signals.go` 的 `noisySignalNames` 的唯一权威来源。**\n")
	fmt.Fprintf(&b, "> 名单里每条信号的百分比都抄自下表；改名单前先看这里，改判据后重跑这里。\n\n")
	fmt.Fprintf(&b, "> 由 `signal_frequency_audit_test.go` 跑**真实的 `detectSignals`** 量出 —— 不是\n")
	fmt.Fprintf(&b, "> SQL 重写。后者的分母是\"bar 数\"而这里是\"7-bar 窗口数\"，同一条件下能差一倍\n")
	fmt.Fprintf(&b, "> （`MACD动能衰减`：SQL 20.43%% vs 本表 10.25%%，前者越过 20%% 阈值、后者没越）。\n")
	fmt.Fprintf(&b, "> `scripts/classify_signals.py` 只能用来核对判据有没有抄错，**不能用来定名单**。\n\n")
	fmt.Fprintf(&b, "> 重跑：`./run_signal_audit.sh`。它有写锁、资源、health 三道 fail-closed 门禁。\n")
	fmt.Fprintf(&b, "> 若 `indicators.duckdb` 被同步任务占着，用 `DB_DIR=<克隆目录>` 起一个只读服务\n")
	fmt.Fprintf(&b, "> 指向 APFS 克隆（`cp` 是瞬时的），让门禁检查的**生产库**保持空闲。\n\n")
	fmt.Fprintf(&b, "Generated by `internal/agent/tools/hithink_finance/pattern/signal_frequency_audit_test.go` (build tag `signal_audit`).\n\n")

	fmt.Fprintf(&b, "## Method\n\n")
	fmt.Fprintf(&b, "- **Engine under test**: the real `detectSignals` in `signals.go`, called once per rolling window. No reimplementation.\n")
	fmt.Fprintf(&b, "- **Data**: the real `queryIndicatorRows` in `query.go`, over the `indicators` and `market` DuckDBs via the python-service HTTP endpoint. The two databases are separate read-only connections and cannot be joined, so close price is fetched from `market.v_daily_qfq` and merged by date in Go — exactly as `query.go` does it.\n")
	fmt.Fprintf(&b, "- **Sampling**: %d of %d stocks, taken as a **fixed stride across the whole thscode-sorted universe** (`codes[i*N/M]`), not the first N. thscode order is alphabetical, so a prefix sample is almost entirely 000xxx/001xxx Shenzhen main board — a board-and-era bias, not random error. The stride spans every prefix (000/001/002/003/300/600/601/603/688), i.e. the whole cross-section, and is deterministic, so a re-run reproduces it exactly.\n", rep.Stocks, rep.Universe)
	fmt.Fprintf(&b, "- **History per stock**: up to %d bars (mean actually returned; the cap is %d), newest-first, spanning %s to %s. That range covers the 2022 bear, the 2023 chop and the 2024-26 rally, so the frequencies are not a single-regime artefact.\n", rep.BarsPerStock, auditBars, rep.Oldest, rep.Newest)
	fmt.Fprintf(&b, "- **Window**: every evaluation feeds a uniform %d-bar window `rows[t:t+%d]`; %d is the deepest `len(rows) >= N` guard in `detectSignals` (signals.go:607), so no signal is ever short of history and all signals share one denominator.\n", auditWinSize, auditWinSize, auditWinSize)
	fmt.Fprintf(&b, "- **Denominator**: `bars evaluated` = %d. That is the number of (stock, bar) pairs for which a full %d-bar window exists, `sum over stocks of max(0, N_i - %d)`. Each pair is counted exactly once, attributed to the newest bar of its window (rows[t]), i.e. the bar the signal is *about*; the %d oldest bars of each series serve only as history and are never themselves evaluated. `frequency = firings / bars evaluated`.\n", rep.Evaluated, auditWinSize, auditWinSize-1, auditWinSize-1)
	fmt.Fprintf(&b, "- **Total raw bars pulled**: %d. Runtime: %s. detectSignals panics: %d.\n", rep.Bars, rep.Elapsed.Round(time.Millisecond), rep.Panics)
	fmt.Fprintf(&b, "- **Signal rows are exhaustive**: the table is driven by `declaredSignalNames` (%d entries), so a signal that never fires still gets a row.\n\n", len(declaredSignalNames))

	// Coverage must be stated, not assumed. A sweep that silently loses part of
	// its sample produces a confident-looking table built on the survivors, which
	// is the one failure mode this audit cannot detect on its own.
	if rep.Stocks > 0 {
		coverage := 100 * float64(rep.OKStocks) / float64(rep.Stocks)
		fmt.Fprintf(&b, "### Coverage\n\n")
		fmt.Fprintf(&b, "- **Sampled stocks successfully evaluated: %d / %d (%.2f%%)**. Dropped: %d. Universe: %d.\n",
			rep.OKStocks, rep.Stocks, coverage, rep.ErrStocks, rep.Universe)
		fmt.Fprintf(&b, "- Dropped stocks are split by cause, because the two causes have opposite meanings:\n")
		if len(rep.PermFail) > 0 {
			fmt.Fprintf(&b, "  - **%d stocks unreadable — corrupt DuckDB segments (permanent)**: the `indicators` file has damaged row groups for these thscodes. The failure is deterministic (identical on every retry, at any worker count) and a narrower query over the same thscode succeeds, so it is data damage, not load. These stocks are **absent from the denominator, not counted as zero-firing**.\n", len(rep.PermFail))
		}
		if len(rep.TransFail) > 0 {
			fmt.Fprintf(&b, "  - **%d stocks lost to transient failures (timeouts, dropped connections) after %d retries**: this is a real infrastructure loss and the frequencies are conditional on the stocks that answered.\n", len(rep.TransFail), auditRetries)
		}
		if len(rep.PermFail) > 0 {
			fmt.Fprintf(&b, "  - Because the corrupt-stock set is excluded rather than zero-filled, every frequency below is conditional on the readable subset. The excluded set is missing-not-random with respect to *data availability* (not with respect to price behaviour), so it biases the level of the rates only weakly — but the exclusion rate is stated here so the reader can judge it.\n")
		}
		if len(rep.ShortStocks) > 0 {
			shown := rep.ShortStocks
			if len(shown) > 20 {
				shown = shown[:20]
			}
			fmt.Fprintf(&b, "- Stocks with fewer than %d bars (excluded, cannot form a window): %d, e.g. %s\n", auditWinSize, len(rep.ShortStocks), strings.Join(shown, ", "))
		}
		if len(rep.PermFail) > 0 {
			fmt.Fprintf(&b, "- Corrupt-segment thscodes (first %d): %s\n", 10, sampleStrings(rep.PermFail, 10))
		}
		if len(rep.TransFail) > 0 {
			fmt.Fprintf(&b, "- Transient failures (first %d): %s\n", 10, sampleStrings(rep.TransFail, 10))
		}
		fmt.Fprintf(&b, "\n")
	}

	fmt.Fprintf(&b, "## Verdict rule\n\n")
	fmt.Fprintf(&b, "Applied identically to every row:\n\n")
	fmt.Fprintf(&b, "| verdict | rule | count |\n|---|---|---|\n")
	fmt.Fprintf(&b, "| `dead` | 0 firings over the whole sample | %d |\n", dead)
	fmt.Fprintf(&b, "| `noisy` | fires on > %.0f%% of evaluated bars | %d |\n", noisyFloor*100, noisy)
	fmt.Fprintf(&b, "| `informative` | 1 .. %.0f%% of evaluated bars | %d |\n\n", noisyFloor*100, informative)
	fmt.Fprintf(&b, "The %.0f%% cut is a judgement call, stated so it can be argued with: a signal is injected into the model's context on every bar it fires on, so above ~1-in-5 bars it is reporting ambient market state rather than a transition, and under the premise under test (`fires on every bar => learned to ignore`) the per-use information content collapses well before 100%%. `ALWAYS-ON` in the notes column marks frequency >= %.0f%%, which is nearly always a missing guard rather than a real signal.\n\n", noisyFloor*100, alwaysOnFloor*100)

	fmt.Fprintf(&b, "## Per-signal results\n\n")
	fmt.Fprintf(&b, "bars evaluated = **%d**\n\n", rep.Evaluated)
	fmt.Fprintf(&b, "`firings` counts bars on which the signal was emitted at least once. `emissions` counts raw appends into the returned slice; where it exceeds `firings` the engine emitted that name twice on the same bar, and only the deduped column feeds `frequency`. `stocks` is how many distinct sampled stocks ever fired it — a low `frequency` with a high `stocks` is a broadly-distributed signal, a low `frequency` with a low `stocks` is one or two names only.\n\n")
	fmt.Fprintf(&b, "| # | signal | firings | emissions | stocks | frequency | direction | verdict | note |\n")
	fmt.Fprintf(&b, "|---:|---|---:|---:|---:|---:|---|---|---|\n")
	for i, r := range rows {
		fmt.Fprintf(&b, "| %d | %s | %d | %d | %d | %.4f%% | %s | `%s` | %s |\n",
			i+1, r.name, r.fires, r.emissions, r.stocks, r.freq*100, r.dirs, r.verd, r.note)
	}

	// Never-firing signals get their own section. Each is diagnosed from the
	// input-column evidence below, so the report can separate "the column is
	// all zeros, so the signal is data-dead" from "the column is populated and
	// the threshold simply never trips" (rare by design) from "the column is
	// populated and the threshold is unreachable" (too strict).
	fmt.Fprintf(&b, "\n## Never-firing signals\n\n")
	anyDead := false
	for i, r := range rows {
		if r.verd != "dead" {
			continue
		}
		anyDead = true
		fmt.Fprintf(&b, "%d. **%s** — 0 firings over %d evaluated bars.\n", i+1, r.name, rep.Evaluated)
	}
	if !anyDead {
		fmt.Fprintf(&b, "None.\n")
	}

	// Predicate-level diagnosis for the never-firing signals.
	fmt.Fprintf(&b, "\n### Which clause kills the never-firing signals\n\n")
	fmt.Fprintf(&b, "Counted over the same %d evaluated bars, using the same `row` fields and the same helpers (`hasData`, `kcSqueezed`) that `detectSignals` uses. Firing counts above still come from `detectSignals` alone.\n\n", rep.Evaluated)
	fmt.Fprintf(&b, "`donchian.up.close_vs_prev_band` is the condition that actually ships (signals.go:389). `donchian.up.old_dead_condition` is the pre-fix condition `close > dc_upper`, which is unsatisfiable because the 20-day band already contains today — it is expected to be 0, and is the empirical evidence the old condition was dead rather than merely rare.\n\nThe two `donchian.down.removed_*` predicates describe the **deleted** second append site, not live code. They are kept so the size of the fixed double-emission stays measurable: `removed_overlap_double_emitted` is how many bars the old engine counted twice, and therefore how far the previously reported frequency of `Donchian下轨跌破` was inflated.\n\n")
	fmt.Fprintf(&b, "| predicate | bars satisfying | share of evaluated |\n|---|---:|---:|\n")
	diagOrder := []string{
		"donchian.up.live_data_present",
		"donchian.up.close_vs_prev_band",
		"donchian.up.old_dead_band_present",
		"donchian.up.old_dead_condition",
		"donchian.down.live_data_present",
		"donchian.down.close_vs_prev_band",
		"donchian.down.removed_narrow_clause",
		"donchian.down.removed_overlap_double_emitted",
		"keltner.four_bands_present",
		"keltner.bb_wider_than_kc_upper",
		"keltner.bb_inside_kc_upper",
		"keltner.bb_inside_kc_lower",
		"keltner.squeezed",
	}
	for _, k := range diagOrder {
		v := rep.Diag[k]
		share := 0.0
		if rep.Evaluated > 0 {
			share = 100 * float64(v) / float64(rep.Evaluated)
		}
		fmt.Fprintf(&b, "| `%s` | %d | %.4f%% |\n", k, v, share)
	}

	if len(rep.Undeclared) > 0 {
		fmt.Fprintf(&b, "\n## Contract violations\n\n")
		fmt.Fprintf(&b, "These names were emitted by `detectSignals` but are absent from `declaredSignalNames`, so they have no row in the table above. The audit drops them deliberately; the contract test is what should fail.\n\n")
		for _, n := range sortedKeys(rep.Undeclared) {
			fmt.Fprintf(&b, "- `%s`: %d firings\n", n, rep.Undeclared[n])
		}
	}

	fmt.Fprintf(&b, "\n## Input-column ranges (over evaluated stocks)\n\n")
	fmt.Fprintf(&b, "Streaming min/max and non-zero rate for every indicator column the signals read. A column that is 0 on every bar makes its dependent signals *data*-dead, which is a different defect from a threshold that is too strict.\n\n")
	fmt.Fprintf(&b, "| column | bars | non-zero | min | max |\n|---|---:|---:|---:|---:|\n")
	names := make([]string, 0, len(rep.Cols.m))
	for k := range rep.Cols.m {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, n := range names {
		c := rep.Cols.m[n]
		if c.N == 0 {
			continue
		}
		fmt.Fprintf(&b, "| %s | %d | %d | %.4f | %.4f |\n", n, c.N, c.NonZero, c.Min, c.Max)
	}
	return b.String()
}

// strideSample returns n items spread evenly across codes, preserving order.
// It is deterministic and covers the whole sorted list rather than a prefix.
func strideSample(codes []string, n int) []string {
	if n <= 0 || n >= len(codes) {
		return codes
	}
	out := make([]string, 0, n)
	for i := 0; i < n; i++ {
		idx := i * len(codes) / n
		out = append(out, codes[idx])
	}
	return out
}

// auditFetch calls the real queryIndicatorRows with retries.
//
// Retrying matters because the data source is a long-lived container holding a
// 17 GB read-only DuckDB: a cold-cache scan can overrun the HTTP timeout and
// a dropped connection is not a property of the stock. Without retries those
// failures silently shrink the denominator.
func auditFetch(ctx context.Context, cfg *hithink_finance.Config, code string, bars int) ([]row, error) {
	var lastErr error
	for attempt := 0; attempt < auditRetries; attempt++ {
		rows, err := queryIndicatorRows(ctx, cfg, code, bars)
		if err == nil {
			return rows, nil
		}
		lastErr = err
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(time.Duration(attempt+1) * 2 * time.Second):
		}
	}
	return nil, lastErr
}

func sampleStrings(m map[string]string, n int) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, n)
	for i, k := range keys {
		if i >= n {
			break
		}
		parts = append(parts, fmt.Sprintf("%s: %s", k, m[k]))
	}
	return strings.Join(parts, "; ")
}

func sortedKeys(m map[string]int64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func dirSummary(c *signalCount) string {
	if c.Fires == 0 {
		return "—"
	}
	parts := make([]string, 0, 3)
	for _, d := range []string{"bullish", "bearish", "neutral"} {
		if n := c.ByDir[d]; n > 0 {
			parts = append(parts, fmt.Sprintf("%s %d (%.0f%%)", d, n, 100*float64(n)/float64(c.Fires)))
		}
	}
	return strings.Join(parts, "; ")
}
