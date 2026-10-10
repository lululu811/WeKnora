package types

import (
	"errors"
	"time"
)

// StockWatchDiary is one trading day's observation of a tracked symbol,
// written by the daily diary job and read by the human on the pool page.
//
// Why a table of its own and not more rows in stock_watch_events: an event
// is "the pool changed once" — added, state_changed, note_changed, and it is
// append-only with a generic `kind` precisely so the log can answer "why did
// this row become what it is". A diary is a different shape of fact: one per
// trading day, a few hundred characters, carrying a snapshot of the readings
// the model was shown. Filing those as events would bury the state history
// under ~250 rows per symbol per year, and the log's job is the one thing
// that must stay readable.
//
// The model writes this row. It never writes stock_watches.state — see
// StockWatchStateTriggered and types.StockWatch: the state machine is the
// human's position, and the verdict here is the machine's opinion about it.
// Adoption is a separate, explicit act by the user.
type StockWatchDiary struct {
	UserID   string `json:"user_id"   gorm:"type:varchar(36)"`
	TenantID uint64 `json:"tenant_id"`
	// column:thscode for the same reason as on StockWatch and
	// StockWatchCondition: GORM folds THSCode into ths_code otherwise.
	THSCode string `json:"thscode" gorm:"column:thscode;type:varchar(16)"`
	// Name is populated dynamically for UI display and is not a column in stock_watch_diaries.
	Name string `json:"name,omitempty" gorm:"-"`
	// TradeDate is the trading day this diary is ABOUT, not the day it was
	// written. The job runs at 08:30 on D+1 and reports D's close, so a
	// wall-clock date here would label a suspended symbol's stale reading as
	// "today" — the same off-by-one-trading-day mistake EvalDate on
	// StockWatchEvent was introduced to prevent. Taken from
	// watchcond.Reading.Date.
	TradeDate DateOnly `json:"trade_date" gorm:"type:date"`
	// Verdict is the machine's answer to "what should I do about this symbol
	// today". One of the StockWatchDiaryVerdict* values. Empty is NOT a legal
	// value: a run that could not decide records StockWatchDiaryVerdictNone
	// rather than leaving the column blank, so "no verdict" and "never
	// evaluated" can never be confused.
	Verdict string `json:"verdict" gorm:"type:varchar(16);not null"`
	// Confidence is the model's own 0-5 certainty. 0 means "no confidence"
	// and is a legal value, distinct from an absent row.
	Confidence int `json:"confidence" gorm:"not null;default:0"`
	// Reasons is a short bulleted justification, rendered as the tooltip
	// behind the verdict badge. Free text on purpose: it is the model's
	// explanation, not something to filter or sort on.
	Reasons string `json:"reasons" gorm:"type:text;not null;default:''"`
	// Body is the diary proper — the paragraph a human reads.
	Body string `json:"body" gorm:"type:text;not null;default:''"`
	// ModelID records which model wrote this row, so any diary can answer
	// "who said this". It also makes a silent model swap (see the workspace
	// fallback in the diary service) visible in the history rather than
	// leaving two visibly different writing styles with no explanation.
	ModelID string `json:"model_id" gorm:"type:varchar(64);not null;default:''"`
	// Readings is the JSON snapshot of the five watchcond.Reading fields the
	// model was shown. Persisted so a diary stays falsifiable: without it,
	// re-reading "close 46.94" months later cannot be checked against what
	// the model actually saw.
	Readings string `json:"readings" gorm:"type:text;not null;default:''"`
	// FinalScore is the grading job's 0-100 aggregate across Q1-Q18.
	// NULL until the grading job has run for this trading day; a diary written
	// by the observation job alone carries no score. The grading job is a
	// separate step that runs after the diary job, so the two writes never
	// race on the same row.
	FinalScore *float64 `json:"final_score" gorm:"type:numeric(5,2)"`
	// Rank is the position within the same trade_date across all scopes.
	// 1 is the best score. NULL until ranked.
	Rank *int `json:"rank" gorm:"type:integer"`
	// Scores is the JSON of the 18 individual question scores and per-question
	// reasons. Kept as text rather than 18 columns so the schema stays flat
	// and the detail drawer can iterate the JSON directly.
	Scores    string    `json:"scores" gorm:"type:text;not null;default:''"`
	CreatedAt time.Time `json:"created_at" gorm:"autoCreateTime"`
	// UpdatedAt changes only when a same-day rerun overwrites the row.
	UpdatedAt time.Time `json:"updated_at" gorm:"autoUpdateTime"`
}

// TableName pins the table to the migration's exact name.
func (StockWatchDiary) TableName() string {
	return "stock_watch_diaries"
}

// Diary verdicts.
//
// Two sets share one column on purpose. They answer the same question — "what
// should I do about this symbol today" — in the two situations the symbol can
// be in, and a symbol is in exactly one of those situations:
//
//   - observing (no position): buy / hold / sell
//   - holding (a position): keep / tighten / exit
//
// Splitting them into two columns would make "did the model decide anything
// today" a two-column question, and a half-filled pair is a state no single
// NOT NULL could express.
//
// StockWatchDiaryVerdictNone is shared by both sets and means exactly one
// thing: there was not enough data to decide. It exists so that a symbol with
// no local quotes, too little history for MA20, or a stalled pipeline is
// reported as "no verdict" instead of being forced into a buy or a sell. A
// model asked to classify a reading it does not have will invent one.
const (
	// StockWatchDiaryVerdictBuy — observing only: the tracking reason is
	// intact and this looks like an entry point.
	StockWatchDiaryVerdictBuy = "buy"
	// StockWatchDiaryVerdictHold — observing only: still worth watching, no
	// action.
	StockWatchDiaryVerdictHold = "hold"
	// StockWatchDiaryVerdictSell — observing only: the tracking reason is
	// gone; worth removing from the pool.
	StockWatchDiaryVerdictSell = "sell"

	// StockWatchDiaryVerdictKeep — holding only: the reason to keep holding
	// is unchanged.
	StockWatchDiaryVerdictKeep = "keep"
	// StockWatchDiaryVerdictTighten — holding only: the reason still holds
	// but has weakened. Not an exit; a request to watch it more closely.
	StockWatchDiaryVerdictTighten = "tighten"
	// StockWatchDiaryVerdictExit — holding only: the reason to hold is gone.
	// Deliberately NOT a stop-loss: the pool carries no cost basis (see
	// types.StockWatch), so this is "your thesis broke", never "you are down
	// x%".
	StockWatchDiaryVerdictExit = "exit"

	// StockWatchDiaryVerdictNone — not enough data to decide. Shared by both
	// sets, and the reason this constant has to exist.
	StockWatchDiaryVerdictNone = "none"
)

// observingVerdicts / holdingVerdicts are the legal value sets, kept next to
// the constants above so a prompt, a validator and the UI cannot each invent
// their own list.
var (
	StockWatchObservingVerdicts = []string{
		StockWatchDiaryVerdictBuy, StockWatchDiaryVerdictHold,
		StockWatchDiaryVerdictSell, StockWatchDiaryVerdictNone,
	}
	StockWatchHoldingVerdicts = []string{
		StockWatchDiaryVerdictKeep, StockWatchDiaryVerdictTighten,
		StockWatchDiaryVerdictExit, StockWatchDiaryVerdictNone,
	}
)

// IsValidStockWatchDiaryVerdict reports whether v is one of the known
// verdicts, for either position state. It answers "is this a verdict at all",
// not "is it the right one for this row" — use VerdictsForState for that.
func IsValidStockWatchDiaryVerdict(v string) bool {
	for _, set := range [][]string{StockWatchObservingVerdicts, StockWatchHoldingVerdicts} {
		for _, s := range set {
			if s == v {
				return true
			}
		}
	}
	return false
}

// VerdictsForState returns the verdicts legal for a symbol in the given
// stock_watches.state. An unknown state returns just the shared "none" —
// there is no honest verdict to offer a state this code does not know.
func VerdictsForState(state string) []string {
	switch state {
	case StockWatchStateObserving, StockWatchStateTriggered:
		return StockWatchObservingVerdicts
	case StockWatchStateHolding:
		return StockWatchHoldingVerdicts
	default:
		return []string{StockWatchDiaryVerdictNone}
	}
}

// IsStockWatchVerdictForState reports whether v is legal for a symbol in the
// given state. This is the check the job applies before persisting a model's
// answer, so a model that answers "keep" for a symbol that is not held is
// recorded as no-verdict rather than stored as a contradiction.
func IsStockWatchVerdictForState(state, v string) bool {
	for _, s := range VerdictsForState(state) {
		if s == v {
			return true
		}
	}
	return false
}

// StockWatchDiaryHistoryDays bounds how many prior verdicts are fed back to
// the model as context.
//
// It is deliberately small. The purpose of the history is to let the model
// see its own recent stance ("yesterday I said tighten, today the reading is
// unchanged") so it does not contradict itself day to day. A longer window
// would not improve that; it would only cost tokens and invite the model to
// narrate a trend it cannot actually see from five scalar readings.
const StockWatchDiaryHistoryDays = 5

// Diary read bounds. The default is what the drawer shows without asking; the
// max caps an explicit `limit` so a single symbol cannot pull its whole
// history into one response.
//
// A trading year is ~250 rows and the drawer shows the most recent handful, so
// the max is generous rather than tight: a user reviewing a symbol's
// year-long arc is exactly the case that should not silently truncate.
const (
	DefaultStockWatchDiaryLimit = 30
	MaxStockWatchDiaryLimit     = 250
)

// ErrVerdictAdoptionRejected is returned when a client tries to adopt a
// verdict into a state that verdict did not ask for. It is a domain error, not
// a validation slip: the point is that the accept endpoint cannot be used as a
// general-purpose state setter wearing a diary costume.
var ErrVerdictAdoptionRejected = errors.New("that state does not follow from this verdict")

// CheckVerdictAdoption reports whether moving a symbol to `toState` is a
// faithful reading of `verdict`.
//
// The mapping is narrow on purpose:
//
//	buy   -> holding   (the human decided to take the position)
//	sell  -> dropped   (the reason is gone, so the symbol leaves the pool)
//	keep  -> holding   (already held; nothing moves, this is a no-op restate)
//	exit  -> dropped
//	tighten -> observing  (stop holding, keep watching — the only verdict that
//	                        can move a held symbol BACK to the pool rather
//	                        than out of it)
//	hold  -> observing  (already observing; a restate)
//	none  -> nothing
//
// Everything else is rejected. Two rules do the work here: a verdict that
// expresses no opinion (`none`) cannot move anything, and a verdict only ever
// justifies the state it names — so "accept a buy" cannot quietly become
// "drop it", which is what would make the button dangerous to click.
func CheckVerdictAdoption(verdict, toState string) error {
	switch verdict {
	case StockWatchDiaryVerdictBuy:
		if toState != StockWatchStateHolding {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictSell:
		if toState != StockWatchStateDropped {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictKeep:
		if toState != StockWatchStateHolding {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictExit:
		if toState != StockWatchStateDropped {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictTighten:
		if toState != StockWatchStateObserving {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictHold:
		if toState != StockWatchStateObserving {
			return ErrVerdictAdoptionRejected
		}
	case StockWatchDiaryVerdictNone:
		// "I could not decide" has no state to move to. Adopting it would be
		// the user acting on nothing.
		return ErrVerdictAdoptionRejected
	default:
		return ErrVerdictAdoptionRejected
	}
	return nil
}

// StateImpliedByVerdict is the state a verdict points at, for the UI to
// preselect. It returns "" for `none` and for any unknown verdict, which the
// drawer renders as "no state change to offer" rather than a disabled button
// with an unexplained label.
func StateImpliedByVerdict(verdict string) string {
	switch verdict {
	case StockWatchDiaryVerdictBuy, StockWatchDiaryVerdictKeep:
		return StockWatchStateHolding
	case StockWatchDiaryVerdictSell, StockWatchDiaryVerdictExit:
		return StockWatchStateDropped
	case StockWatchDiaryVerdictTighten, StockWatchDiaryVerdictHold:
		return StockWatchStateObserving
	default:
		return ""
	}
}

// Diary event kinds, appended to the existing stock_watch_events vocabulary.
// Events stay the record of what the POOL did; these two record what the human
// did about what the model said, which nothing else answers.
const (
	// StockWatchEventVerdictAccepted — the user adopted a verdict, which is
	// the only automated-looking path that is allowed to move `state`, and it
	// moves it because the user clicked.
	StockWatchEventVerdictAccepted = "verdict_accepted"
	// StockWatchEventVerdictIgnored — the user saw a verdict and declined it.
	// Written precisely so that "the model keeps saying buy and the human
	// keeps saying no" is a question the data can answer, and so a future
	// prompt fix has evidence to be tuned against.
	StockWatchEventVerdictIgnored = "verdict_ignored"
)
