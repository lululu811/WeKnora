package types

import (
	"errors"
	"regexp"
	"time"
)

// StockWatch is one row of a user's private watchlist ("个股追踪 / 持有股观察").
//
// Scope rationale (mirrors UserResourceFavorite): a watchlist is a personal
// navigation aid, not a shareable resource, so it is keyed by (user_id,
// tenant_id) and never by resource ownership. The same user in two tenants
// keeps two independent lists — a symbol watched in one workspace should not
// leak into another.
//
// THSCode is the natural key inside that scope: there is deliberately no
// surrogate id, because the only thing a caller can usefully address is
// "my row for this symbol". That also makes "add" idempotent for free.
//
// Deliberately NOT modeled: cost basis, lots, P&L. Those need decimal money,
// corporate-action (split/dividend) adjustment and a trade ledger to stay
// honest; a nullable `cost` float would silently emit wrong P&L for any
// holding that ever split. Add them as their own table when the need is real.
type StockWatch struct {
	UserID   string `json:"user_id"    gorm:"type:varchar(36);primaryKey"`
	TenantID uint64 `json:"tenant_id"  gorm:"primaryKey"`
	// column:thscode 是必须的：GORM 的默认命名会把 THSCode 折成 ths_code，
	// 而迁移建的是 thscode（与 python-service 的 `v_symbol.thscode`、各处的
	// API 参数同名）。少了这个 tag，查询会在运行时报
	// "no such column: stock_watches.ths_code"。
	THSCode   string `json:"thscode"    gorm:"column:thscode;type:varchar(16);primaryKey"`
	Name      string `json:"name"       gorm:"type:varchar(64)"`
	Exchange  string `json:"exchange"   gorm:"type:varchar(8)"`
	SortOrder int    `json:"sort_order" gorm:"not null;default:0"`
	// State is where this symbol sits in the user's own tracking workflow.
	// Nothing but the user moves it (see StockWatchStateCanTransition); a
	// future buy-point trigger would be the one automatic writer of
	// StockWatchStateTriggered. Defaults to observing on insert.
	State string `json:"state" gorm:"type:varchar(16);not null;default:observing"`
	// Note is the user's free-text reason for tracking the symbol ("等回踩
	// 55 日线", "打新底仓"). Capped at MaxStockWatchNoteLen so the guard is
	// enforced before the DB's varchar truncation/error would surface.
	Note      string    `json:"note"       gorm:"type:varchar(200);not null;default:''"`
	CreatedAt time.Time `json:"created_at" gorm:"autoCreateTime"`
	UpdatedAt time.Time `json:"updated_at" gorm:"autoUpdateTime"`
}

// TableName pins the table to the migration's exact name so GORM's
// pluraliser doesn't drift if the struct is ever renamed.
func (StockWatch) TableName() string {
	return "stock_watches"
}

// MaxStockWatchesPerUser caps one user's list in one tenant.
//
// The cap exists because every render of the list fans out one quote lookup
// per row against the local DuckDB; an unbounded list is a self-inflicted
// slow page, not a privilege. It sits far above any realistic watchlist.
const MaxStockWatchesPerUser = 500

// thscodePattern is the same shape the Python service enforces (_THSCODE in
// python-service/main.py). Kept in sync on purpose: a value that passes here
// but not there would be stored happily and then render as a permanent
// "无数据" row with no way for the user to tell why.
var thscodePattern = regexp.MustCompile(`^\d{6}\.(SH|SZ|BJ|HK|US)$`)

// IsValidStockWatchCode reports whether code looks like a thscode
// ("600519.SH"). Case-insensitive: callers normalise before storing.
func IsValidStockWatchCode(code string) bool {
	return thscodePattern.MatchString(code)
}

// StockWatch states: a small, deliberately manual state machine over one
// tracked symbol.
//
// The pool answers "where do I stand with this symbol?", not "what do I own".
// Only the human moves a row between states — there is no automatic promotion,
// because a machine that guesses "you probably bought it" is a machine that
// silently lies about a position. The single intended automatic writer is a
// future buy-point trigger, and even that may only set `triggered`, which is
// itself an invitation for the human to confirm (→ holding / dropped).
const (
	// StockWatchStateObserving — in the pool, no position, waiting.
	StockWatchStateObserving = "observing"
	// StockWatchStateTriggered — a buy point fired; the human has not acted yet.
	StockWatchStateTriggered = "triggered"
	// StockWatchStateHolding — the human says they hold it.
	StockWatchStateHolding = "holding"
	// StockWatchStateDropped — the human gave up on it, row kept for history.
	StockWatchStateDropped = "dropped"
)

// MaxStockWatchNoteLen matches the note column's varchar(200).
const MaxStockWatchNoteLen = 200

// stockWatchStateTransitions is the whole state machine, as data.
//
// Note what is absent: nothing reaches `triggered` (only the future trigger
// writes it), and nothing is terminal — a dropped symbol can come back to
// observing, because "放弃" is a stance the user is allowed to change their
// mind about, and forcing a delete + re-add would throw away the note and the
// event history that explain why it was dropped.
var stockWatchStateTransitions = map[string][]string{
	StockWatchStateObserving: {StockWatchStateHolding, StockWatchStateDropped},
	StockWatchStateTriggered: {StockWatchStateHolding, StockWatchStateDropped, StockWatchStateObserving},
	StockWatchStateHolding:   {StockWatchStateObserving, StockWatchStateDropped},
	StockWatchStateDropped:   {StockWatchStateObserving},
}

// IsValidStockWatchState reports whether state is one of the four known
// values. It answers "is this a state at all", not "is this reachable".
func IsValidStockWatchState(state string) bool {
	_, ok := stockWatchStateTransitions[state]
	return ok
}

// StockWatchStateCanTransition reports whether from -> to is a legal move.
//
// Unknown `from` or `to` values are illegal by construction: an unrecognised
// state is not a state this machine can leave or enter.
func StockWatchStateCanTransition(from, to string) bool {
	for _, next := range stockWatchStateTransitions[from] {
		if next == to {
			return true
		}
	}
	return false
}

// ErrStockWatchIllegalTransition is the domain sentinel for "that move is not
// in the state machine". It lives in types rather than service because the
// rule it describes is about the domain values, and the repository enforces it
// inside the same transaction that writes the row (see stockWatchRepository.Update)
// — a check split across two round trips would let two concurrent PUTs each
// pass their own validation and produce a transition neither allowed.
var ErrStockWatchIllegalTransition = errors.New("illegal watch state transition")

// Event kinds for stock_watch_events. The column is generic on purpose: an
// event is a fact that happened, and "a buy point fired" must be recordable
// later without a schema change. `added` / `state_changed` / `note_changed` /
// `condition_triggered` are the only writers today.
//
// condition_triggered is the one machine-written kind: a user-authored numeric
// condition crossed its threshold. It deliberately does NOT move the pool row's
// `state` — `triggered` stays reserved for the human to mark by hand, because a
// notifier reporting a reading is not the same thing as a person deciding to
// act on it.
const (
	StockWatchEventAdded              = "added"
	StockWatchEventStateChanged       = "state_changed"
	StockWatchEventNoteChanged        = "note_changed"
	StockWatchEventConditionTriggered = "condition_triggered"
)

// Event feed bounds. The default is what the pool page shows without asking;
// the max exists because this endpoint returns a slice of a table that grows
// without bound, and a client asking for 10^9 rows should get a page, not an
// OOM.
const (
	DefaultStockWatchEventLimit = 50
	MaxStockWatchEventLimit     = 200
)

// StockWatchEvent is one append-only audit row of a user's tracking pool.
//
// Why this exists at all: the pool is a set of *decisions*, and a decision
// without its history is unfalsifiable after the fact ("why did I drop this in
// March?"). The row itself only carries the present; this table carries the
// past. It is append-only — nothing updates or deletes an event — so a state
// change is never a silent overwrite.
//
// Scoped by (user_id, tenant_id) exactly like stock_watches, for the same
// reason: the same human in two workspaces keeps two independent pools and
// therefore two independent histories.
//
// Deliberately absent: position size, cost, P&L. See StockWatch for why a
// nullable float would be a correctness trap for any split-adjusted holding.
type StockWatchEvent struct {
	ID        uint64 `json:"id"         gorm:"primaryKey;autoIncrement"`
	UserID    string `json:"user_id"    gorm:"type:varchar(36)"`
	TenantID  uint64 `json:"tenant_id"`
	Kind      string `json:"kind"       gorm:"type:varchar(32)"`
	THSCode   string `json:"thscode"    gorm:"column:thscode;type:varchar(16)"`
	FromState string `json:"from_state" gorm:"type:varchar(16)"`
	ToState   string `json:"to_state"   gorm:"type:varchar(16)"`
	Note      string `json:"note"       gorm:"type:varchar(200)"`
	// EvalDate is the trading day an evaluation event belongs to.
	//
	// CreatedAt is insert time, which is NOT the day the event is about: the
	// condition job runs at 08:30 on D+1 and reports D's close. Anything asking
	// "was this triggered on the latest trading day" must compare against this
	// field — comparing against CreatedAt is off by one trading day by
	// construction (the 「今日触发」 badge could never light up because of it).
	// NULL for state/note events, which are not about a trading day.
	EvalDate  *DateOnly `json:"eval_date"  gorm:"type:date"`
	CreatedAt time.Time `json:"created_at" gorm:"autoCreateTime"`
}

// TableName pins the table to the migration's exact name.
func (StockWatchEvent) TableName() string {
	return "stock_watch_events"
}
