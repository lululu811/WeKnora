package repository

import (
	"context"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"gorm.io/gorm"
)

// stockWatchEventsRepository reads the append-only history of a user's
// tracking pool (see types.StockWatchEvent for why the table exists).
//
// It is read-only by design: see interfaces.StockWatchEventsRepository for why
// there is no Insert here.
type stockWatchEventsRepository struct {
	db *gorm.DB
}

// NewStockWatchEventsRepository constructs the GORM-backed implementation.
func NewStockWatchEventsRepository(db *gorm.DB) interfaces.StockWatchEventsRepository {
	return &stockWatchEventsRepository{db: db}
}

func (r *stockWatchEventsRepository) List(
	ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
) ([]*types.StockWatchEvent, error) {
	q := r.db.WithContext(ctx).
		Model(&types.StockWatchEvent{}).
		Where("user_id = ? AND tenant_id = ?", userID, tenantID)
	if thscode != "" {
		q = q.Where("thscode = ?", thscode)
	}
	if limit <= 0 {
		limit = types.DefaultStockWatchEventLimit
	}
	var list []*types.StockWatchEvent
	// id DESC breaks ties on created_at: two events written in the same
	// millisecond (a state + note change in one request) must still come back
	// newest-first in a stable order, and created_at alone cannot promise that.
	err := q.Order("created_at DESC, id DESC").Limit(limit).Find(&list).Error
	return list, err
}

// insertStockWatchEvent writes one audit row through the caller's transaction.
//
// A plain function rather than a method on stockWatchEventsRepository because
// the write path must be *shared with the caller's transaction*: a pool
// mutation and its event have to land together, and handing the row
// repository the events repository (or a *gorm.DB-typed interface) to obtain
// the same tx would buy nothing but indirection.
func insertStockWatchEvent(tx *gorm.DB, event *types.StockWatchEvent) error {
	return tx.Create(event).Error
}
