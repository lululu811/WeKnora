package types

import (
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"time"
)

// StockWatchCondition is one user-authored numeric condition on a tracked
// symbol ("个股追踪" page): the user writes a simple threshold, the daily job
// watches it, and a crossing is reported honestly.
//
// Why user-authored and not an algorithm: the B1 "建仓波" buy-point judgement
// was back-tested and had no positive edge (see the feat/watchlist-pool
// history). A machine that guesses when to buy is a machine that silently lies
// about a decision the human never made. So there is no scoring, no ranking and
// no recommendation here — only "the reading you named crossed the line you
// drew", which is a fact and therefore falsifiable.
//
// Scope is the sibling of stock_watches: (user_id, tenant_id, thscode). A
// condition is meaningless without the symbol it watches, and a raw condition
// row on a symbol that is no longer tracked is deliberately allowed to survive
// as history (the pool row carries the note that explains why it was tracked).
//
// The uniqueness rule is (user_id, tenant_id, thscode, field, op, value): the
// same threshold entered twice is one condition, so a double-click on the "add"
// button cannot silently create a duplicate that fires two alerts.
type StockWatchCondition struct {
	ID       string `json:"id"       gorm:"type:varchar(36);primaryKey"`
	UserID   string `json:"user_id"  gorm:"type:varchar(36)"`
	TenantID uint64 `json:"tenant_id"`
	// column:thscode is required for the same reason as on StockWatch: GORM
	// would otherwise fold THSCode into ths_code and the query would fail at
	// runtime against the migration's `thscode`.
	THSCode string `json:"thscode" gorm:"column:thscode;type:varchar(16)"`
	// Field is the quote reading the threshold is compared against; one of the
	// StockWatchConditionField* values.
	Field string `json:"field" gorm:"type:varchar(24)"`
	// Op is the comparison direction; one of the StockWatchConditionOp* values.
	Op string `json:"op" gorm:"type:varchar(8)"`
	// Value is the user's threshold. There is deliberately no "tolerance" or
	// band: a condition is a single line, and the honest report of a crossing
	// is the only output.
	Value float64 `json:"value"`
	// LastSatisfied is the reading's last decided state. nil means "never
	// evaluated" — distinct from false, and the distinction is load-bearing:
	// a condition that is already true when it is added must NOT fire (that is
	// something the user already knew), so nil is a record-only state.
	LastSatisfied *bool `json:"last_satisfied"`
	// LastEvalDate is the trading day the last evaluation ran for, and it is
	// the watermark: no new trading day means the whole run is skipped, which
	// makes weekends, holidays and ETL outages silent instead of a false alarm.
	LastEvalDate *DateOnly `json:"last_eval_date" gorm:"type:date"`
	CreatedAt    time.Time `json:"created_at" gorm:"autoCreateTime"`
	UpdatedAt    time.Time `json:"updated_at" gorm:"autoUpdateTime"`
}

// TableName pins the table to the migration's exact name.
func (StockWatchCondition) TableName() string {
	return "stock_watch_conditions"
}

// Condition fields. Each maps to a numeric field of python-service's
// `/api/quotes` response; the arithmetic is defined here and nowhere else.
const (
	// StockWatchConditionFieldPrice compares against the latest close.
	StockWatchConditionFieldPrice = "price"
	// StockWatchConditionFieldPctChange compares against change_pct, which the
	// quotes endpoint already returns as a percentage (e.g. -3.2 means -3.2%).
	StockWatchConditionFieldPctChange = "pct_change"
	// StockWatchConditionFieldVolumeRatio compares today's volume against the
	// mean of the PREVIOUS 5 bars (volume_ratio from the quotes endpoint).
	StockWatchConditionFieldVolumeRatio = "volume_ratio"
	// StockWatchConditionFieldCloseVsMA20 compares the percent deviation from
	// MA20: (close / ma20 - 1) * 100.
	StockWatchConditionFieldCloseVsMA20 = "close_vs_ma20"
)

// Condition comparison directions.
const (
	// StockWatchConditionOpAbove fires when the reading rises strictly above
	// the threshold.
	StockWatchConditionOpAbove = "above"
	// StockWatchConditionOpBelow fires when the reading falls strictly below
	// the threshold.
	StockWatchConditionOpBelow = "below"
)

// stockWatchConditionFields / Ops are allowlists, not merely documentation:
// an unknown field is a caller bug that must be rejected at the edge, because a
// silently-ignored condition would sit on the page looking active while never
// being evaluated.
var stockWatchConditionFields = map[string]bool{
	StockWatchConditionFieldPrice:       true,
	StockWatchConditionFieldPctChange:   true,
	StockWatchConditionFieldVolumeRatio: true,
	StockWatchConditionFieldCloseVsMA20: true,
}

var stockWatchConditionOps = map[string]bool{
	StockWatchConditionOpAbove: true,
	StockWatchConditionOpBelow: true,
}

// IsValidStockWatchConditionField reports whether field is a known reading.
func IsValidStockWatchConditionField(field string) bool {
	return stockWatchConditionFields[field]
}

// IsValidStockWatchConditionOp reports whether op is a known direction.
func IsValidStockWatchConditionOp(op string) bool {
	return stockWatchConditionOps[op]
}

// Compare evaluates one condition's reading against its threshold.
//
// It answers only "is the line crossed", with no tolerance band: a condition is
// a single line, and pretending a reading near the line "basically" crossed it
// would be inventing a judgement the user did not author.
func (c StockWatchCondition) Compare(reading float64) bool {
	switch c.Op {
	case StockWatchConditionOpAbove:
		return reading > c.Value
	case StockWatchConditionOpBelow:
		return reading < c.Value
	default:
		// Unreachable for a stored row (the service rejects unknown ops), but
		// returning false is the safe direction: an unknown op must never fire.
		return false
	}
}

// StockWatchConditionTransition is the decided next state of one condition for
// one evaluation run.
//
// Fired is the strict 0→1 edge only: (last_satisfied == false) && now true. A
// transition with Fired == false still carries the new state to record — the
// watermark must advance even when nothing crossed, or every run would keep
// re-deciding the same unchanged reading.
type StockWatchConditionTransition struct {
	ConditionID string
	THSCode     string
	Field       string
	Op          string
	Value       float64
	Satisfied   bool
	// Reading is the actual value that decided the condition, kept so the
	// event note and the alert can report "现价 1204.53" instead of only the
	// threshold — the number that crossed is the useful part.
	Reading float64
	// EvalDate is the trading day this transition was decided for.
	EvalDate string
	Fired    bool
	// Note is the human-readable description of the crossing (e.g. "价格跌破
	// 1235.00"), filled only when Fired. It is produced by the evaluator so the
	// event row and the alert say the same thing.
	Note string
}

// StockWatchScope identifies one (user, tenant) pair that owns conditions.
// The daily job iterates scopes rather than rows so a user's conditions are
// evaluated together against one quote batch.
type StockWatchScope struct {
	UserID   string
	TenantID uint64
}

// stock_watch_notifications kinds. Today only condition triggers are pushed.
const StockWatchNotificationConditionTriggered = "condition_triggered"

// StockWatchNotification is the audit trail of one delivery attempt.
//
// Why this exists: "系统说会推送，结果没收到" must be answerable after the
// fact. The event row proves a crossing happened; this row proves whether the
// system tried to tell anyone, how many times, and what the failure was. It is
// written even when the webhook is unset (ok=false, error explains why) so the
// absence of a push is visible instead of looking like there was nothing to say.
type StockWatchNotification struct {
	ID       uint64 `json:"id"        gorm:"primaryKey;autoIncrement"`
	UserID   string `json:"user_id"   gorm:"type:varchar(36)"`
	TenantID uint64 `json:"tenant_id"`
	Kind     string `json:"kind"      gorm:"type:varchar(32)"`
	// Payload is the exact content that was (or would have been) sent, so the
	// record is self-contained and not a promise of what some later render
	// might have produced.
	Payload string `json:"payload"   gorm:"type:text"`
	OK      bool   `json:"ok"`
	Error   string `json:"error"     gorm:"type:text"`
	// Attempts counts the HTTP attempts made for this run. 0 means the send was
	// skipped before any attempt (e.g. no webhook configured).
	Attempts  int       `json:"attempts"`
	CreatedAt time.Time `json:"created_at" gorm:"autoCreateTime"`
}

// TableName pins the table to the migration's exact name.
func (StockWatchNotification) TableName() string {
	return "stock_watch_notifications"
}

// DateOnly is a calendar date (YYYY-MM-DD) with no time of day and no zone.
//
// Why not time.Time: the watermark is a *trading day*, and a timestamp would
// both round-trip through JSON as RFC3339 (violating the API contract
// `last_eval_date: 'YYYY-MM-DD'`) and invite timezone bugs — "2026-10-02
// 00:00:00Z" is a different instant from "2026-10-02 00:00:00+08:00" even
// though both name the same trading day.
type DateOnly time.Time

// ParseDateOnly parses "YYYY-MM-DD".
func ParseDateOnly(s string) (DateOnly, error) {
	t, err := time.Parse(time.DateOnly, s)
	if err != nil {
		return DateOnly{}, err
	}
	return DateOnly(t), nil
}

// String returns the canonical YYYY-MM-DD form. ISO dates compare correctly as
// strings, which is exactly how the watermark check is written.
func (d DateOnly) String() string {
	return time.Time(d).Format(time.DateOnly)
}

// IsZero reports whether the date is unset.
func (d DateOnly) IsZero() bool {
	return time.Time(d).IsZero()
}

// MarshalJSON emits "YYYY-MM-DD" (or null via a nil pointer).
func (d DateOnly) MarshalJSON() ([]byte, error) {
	return json.Marshal(d.String())
}

// UnmarshalJSON accepts "YYYY-MM-DD" and null.
func (d *DateOnly) UnmarshalJSON(b []byte) error {
	if string(b) == "null" {
		*d = DateOnly{}
		return nil
	}
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return err
	}
	parsed, err := ParseDateOnly(s)
	if err != nil {
		return err
	}
	*d = parsed
	return nil
}

// Scan implements sql.Scanner. Both Postgres (DATE → time.Time) and SQLite
// (DATETIME → string) are accepted; only the calendar day is kept.
func (d *DateOnly) Scan(value any) error {
	switch v := value.(type) {
	case nil:
		*d = DateOnly{}
		return nil
	case time.Time:
		y, m, day := v.Date()
		*d = DateOnly(time.Date(y, m, day, 0, 0, 0, 0, time.UTC))
		return nil
	case string:
		return d.scanText(v)
	case []byte:
		return d.scanText(string(v))
	default:
		return fmt.Errorf("types: cannot scan %T into DateOnly", value)
	}
}

func (d *DateOnly) scanText(s string) error {
	if len(s) > len(time.DateOnly) {
		s = s[:len(time.DateOnly)]
	}
	parsed, err := ParseDateOnly(s)
	if err != nil {
		return err
	}
	*d = parsed
	return nil
}

// Value implements driver.Valuer.
//
// It returns a time.Time, NOT a string: pgx sends a DATE parameter in binary
// form, and a plain string is not encodable for that OID — the driver would
// fail at bind time. time.Time is encoded via pgx's date codec, which reads the
// value's own calendar day (Y/M/D), so midnight-UTC dates round-trip without a
// timezone shift.
//
// A zero date is NULL.
func (d DateOnly) Value() (driver.Value, error) {
	if time.Time(d).IsZero() {
		return nil, nil
	}
	return time.Time(d), nil
}

// GormDataType keeps the column type aligned with the DATE in both migrations.
func (d DateOnly) GormDataType() string {
	return "date"
}
