// Package watchcond is the pure condition evaluator: given quote readings and
// user-authored conditions, it decides each condition's next state and which
// crossings fired.
//
// It has no HTTP client, no database handle and no clock — every input arrives
// as an argument and every output is a value. That is deliberate: this is the
// part of the notifier that must be trivially testable, because it is the part
// that can be wrong in ways that look plausible (an off-by-one on the edge, a
// null read as zero, a first evaluation that fires). The job around it is
// plumbing; the arithmetic is here.
package watchcond

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/Tencent/WeKnora/internal/types"
)

// Reading is one symbol's quote snapshot, reduced to exactly the fields a
// condition can reference.
//
// Every numeric field is a pointer so that "unavailable" survives the trip from
// the quotes endpoint, where it is null. A missing reading is NOT zero: zero is
// a real price and a real change percent, and treating "no data" as zero would
// fire "跌破 1235" for a symbol that merely has no local history yet.
type Reading struct {
	// Name is display-only (the alert's headline); never used in a comparison.
	Name string
	// Date is the trading day of the reading, YYYY-MM-DD.
	Date string
	// Close is the latest close (condition field `price`).
	Close *float64
	// ChangePct is the day's change in percent (condition field `pct_change`).
	ChangePct *float64
	// VolumeRatio is today's volume ÷ the mean of the previous 5 bars.
	VolumeRatio *float64
	// MA20 is the 20-bar moving average of close, used only to derive
	// `close_vs_ma20`; it is not itself a condition field.
	MA20 *float64
}

// Value returns the number a condition on `field` should be compared against.
// ok is false when the field is unknown or the reading is unavailable (null),
// which makes the condition undecidable rather than false.
func (r Reading) Value(field string) (value float64, ok bool) {
	switch field {
	case types.StockWatchConditionFieldPrice:
		return deref(r.Close)
	case types.StockWatchConditionFieldPctChange:
		return deref(r.ChangePct)
	case types.StockWatchConditionFieldVolumeRatio:
		return deref(r.VolumeRatio)
	case types.StockWatchConditionFieldCloseVsMA20:
		close, hasClose := deref(r.Close)
		ma, hasMA := deref(r.MA20)
		// ma == 0 would divide by zero. A genuine zero MA20 is impossible for a
		// real instrument, so reaching it means the data is broken; refusing to
		// decide is the only honest answer.
		if !hasClose || !hasMA || ma == 0 {
			return 0, false
		}
		return (close/ma - 1) * 100, true
	default:
		return 0, false
	}
}

func deref(v *float64) (float64, bool) {
	if v == nil {
		return 0, false
	}
	return *v, true
}

// Evaluate decides the next state of every decidable condition.
//
// Rules, in one place so they cannot drift between the job and the tests:
//
//   - An unavailable reading (or unknown field) is UNDECIDABLE: the condition
//     is omitted from the result, so the caller changes neither last_satisfied
//     nor the last_eval_date watermark, and nothing fires.
//   - A condition already evaluated for this reading's trading day is omitted:
//     the watermark is the trading date, and re-deciding the same day would
//     re-arm a crossing that already fired (a 0→1 edge can only be observed
//     once per day, not once per run).
//   - last_satisfied == nil (first ever evaluation) → record the state, do not
//     fire. A condition that is already satisfied the moment it is added is
//     something the user already knew; pushing it would be noise.
//   - Fire only on the strict 0→1 edge: last_satisfied == false and now true.
//     Every other case (true→true, true→false, false→false) records the new
//     state silently.
func Evaluate(
	conditions []*types.StockWatchCondition, readings map[string]Reading,
) []types.StockWatchConditionTransition {
	out := make([]types.StockWatchConditionTransition, 0, len(conditions))
	for _, cond := range conditions {
		if cond == nil {
			continue
		}
		reading, hasReading := readings[cond.THSCode]
		if !hasReading {
			continue
		}
		// A reading with no trading day cannot anchor the watermark: recording
		// a state without a date would leave last_eval_date NULL and let the
		// same data be re-decided on every run. Undecidable is the honest
		// answer — the quotes endpoint only omits the date when it has no bars,
		// and that is already "no data".
		if reading.Date == "" {
			continue
		}
		// Watermark: this condition was already decided for this trading day.
		if cond.LastEvalDate != nil && !cond.LastEvalDate.IsZero() && cond.LastEvalDate.String() >= reading.Date {
			continue
		}
		value, decidable := reading.Value(cond.Field)
		if !decidable {
			continue
		}
		satisfied := cond.Compare(value)
		transition := types.StockWatchConditionTransition{
			ConditionID: cond.ID,
			THSCode:     cond.THSCode,
			Field:       cond.Field,
			Op:          cond.Op,
			Value:       cond.Value,
			Satisfied:   satisfied,
			Reading:     value,
			EvalDate:    reading.Date,
		}
		// The strict edge, and only for an already-decided condition. nil
		// (never evaluated) is not false — it records without firing.
		if cond.LastSatisfied != nil && !*cond.LastSatisfied && satisfied {
			transition.Fired = true
			transition.Note = Describe(cond)
		}
		out = append(out, transition)
	}
	return out
}

// Describe renders the human-readable one-line reason for an alert, e.g.
// "价格跌破 1235.00". It is shared by the event note and the push message so
// the history and the alert can never disagree about what happened.
func Describe(cond *types.StockWatchCondition) string {
	threshold := formatNumber(cond.Value)
	switch cond.Field {
	case types.StockWatchConditionFieldPrice:
		if cond.Op == types.StockWatchConditionOpAbove {
			return "价格升破 " + threshold
		}
		return "价格跌破 " + threshold
	case types.StockWatchConditionFieldPctChange:
		if cond.Op == types.StockWatchConditionOpAbove {
			return "涨跌幅高于 " + threshold + "%"
		}
		return "涨跌幅低于 " + threshold + "%"
	case types.StockWatchConditionFieldVolumeRatio:
		if cond.Op == types.StockWatchConditionOpAbove {
			return "量比高于 " + threshold
		}
		return "量比低于 " + threshold
	case types.StockWatchConditionFieldCloseVsMA20:
		if cond.Op == types.StockWatchConditionOpAbove {
			return "距 MA20 高于 " + threshold + "%"
		}
		return "距 MA20 低于 " + threshold + "%"
	default:
		return fmt.Sprintf("%s %s %s", cond.Field, cond.Op, threshold)
	}
}

// formatNumber renders a threshold without a trailing ".00" on whole numbers,
// so "跌破 1235" reads the way the user typed it, while "2.5" keeps its
// fraction.
func formatNumber(v float64) string {
	return strconv.FormatFloat(v, 'f', -1, 64)
}

// FormatReading renders the actual value that crossed, for the "（现值 …）"
// suffix on an alert line. It carries the unit implied by the field.
func FormatReading(field string, value float64) string {
	switch field {
	case types.StockWatchConditionFieldPctChange, types.StockWatchConditionFieldCloseVsMA20:
		return fmt.Sprintf("%s%%", strconv.FormatFloat(value, 'f', 2, 64))
	default:
		return strconv.FormatFloat(value, 'f', 2, 64)
	}
}

// FormatAlert builds the ONE message a run pushes, aggregating every firing.
//
// One message per run, never one per symbol: the delivery channel is a chat
// webhook, and ten separate pushes for ten crossings is how an alert system
// trains its user to mute it. Grouping by symbol keeps the message scannable
// while still being a single POST.
func FormatAlert(
	evalDate string, readings map[string]Reading, transitions []types.StockWatchConditionTransition,
) string {
	firings := make([]types.StockWatchConditionTransition, 0, len(transitions))
	for _, t := range transitions {
		if t.Fired {
			firings = append(firings, t)
		}
	}
	if len(firings) == 0 {
		return ""
	}
	// Stable order: by symbol, then by the printed reason, so two runs over the
	// same data produce the same message (and the tests are not order-flaky).
	sort.SliceStable(firings, func(i, j int) bool {
		if firings[i].THSCode != firings[j].THSCode {
			return firings[i].THSCode < firings[j].THSCode
		}
		return firings[i].Note < firings[j].Note
	})

	var b strings.Builder
	b.WriteString("【个股条件提醒】")
	if evalDate != "" {
		b.WriteString(evalDate)
	}
	b.WriteString("\n")

	lastCode := ""
	for _, f := range firings {
		if f.THSCode != lastCode {
			label := f.THSCode
			if name := readings[f.THSCode].Name; name != "" {
				label = f.THSCode + " " + name
			}
			b.WriteString("\n" + label + "\n")
			lastCode = f.THSCode
		}
		b.WriteString(fmt.Sprintf("  · %s（现值 %s）\n", f.Note, FormatReading(f.Field, f.Reading)))
	}
	return strings.TrimRight(b.String(), "\n")
}
