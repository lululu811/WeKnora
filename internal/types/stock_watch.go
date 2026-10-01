package types

import (
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
	THSCode   string    `json:"thscode"    gorm:"column:thscode;type:varchar(16);primaryKey"`
	Name      string    `json:"name"       gorm:"type:varchar(64)"`
	Exchange  string    `json:"exchange"   gorm:"type:varchar(8)"`
	SortOrder int       `json:"sort_order" gorm:"not null;default:0"`
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
