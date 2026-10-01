package repository

import (
	"context"
	"errors"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"gorm.io/gorm"
)

type stockWatchRepository struct {
	db *gorm.DB
}

// NewStockWatchRepository constructs the GORM-backed implementation.
func NewStockWatchRepository(db *gorm.DB) interfaces.StockWatchRepository {
	return &stockWatchRepository{db: db}
}

func (r *stockWatchRepository) List(ctx context.Context, userID string, tenantID uint64) ([]*types.StockWatch, error) {
	var list []*types.StockWatch
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ?", userID, tenantID).
		// sort_order 全为 0（默认）时退化成加入顺序，这正是没手动排序过的
		// 用户期望的视图，因此不需要额外的一次性初始化。
		Order("sort_order ASC, created_at ASC").
		Find(&list).Error
	return list, err
}

func (r *stockWatchRepository) Count(ctx context.Context, userID string, tenantID uint64) (int64, error) {
	var n int64
	err := r.db.WithContext(ctx).
		Model(&types.StockWatch{}).
		Where("user_id = ? AND tenant_id = ?", userID, tenantID).
		Count(&n).Error
	return n, err
}

// Add upserts: the composite primary key plus FirstOrCreate makes concurrent
// double-clicks collapse into one row with no error path (same trick as
// user_resource_favorite.go). A re-add is not a no-op — the stored display
// name is refreshed, because the market's authoritative name for a symbol can
// change (ST/*ST prefixes, renames) and a stale label is worse than no label.
func (r *stockWatchRepository) Add(
	ctx context.Context, item *types.StockWatch,
) (*types.StockWatch, bool, error) {
	rec := *item
	res := r.db.WithContext(ctx).
		Where(&types.StockWatch{UserID: item.UserID, TenantID: item.TenantID, THSCode: item.THSCode}).
		FirstOrCreate(&rec)
	if res.Error != nil {
		return nil, false, res.Error
	}
	if res.RowsAffected > 0 {
		return &rec, true, nil
	}
	if rec.Name == item.Name && rec.Exchange == item.Exchange {
		return &rec, false, nil
	}
	rec.Name = item.Name
	rec.Exchange = item.Exchange
	rec.UpdatedAt = time.Now()
	err := r.db.WithContext(ctx).
		Model(&types.StockWatch{}).
		Where("user_id = ? AND tenant_id = ? AND thscode = ?", item.UserID, item.TenantID, item.THSCode).
		Updates(map[string]interface{}{
			"name":       rec.Name,
			"exchange":   rec.Exchange,
			"updated_at": rec.UpdatedAt,
		}).Error
	if err != nil {
		return nil, false, err
	}
	return &rec, false, nil
}

func (r *stockWatchRepository) Remove(
	ctx context.Context, userID string, tenantID uint64, thscode string,
) (bool, error) {
	res := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ?", userID, tenantID, thscode).
		Delete(&types.StockWatch{})
	if res.Error != nil {
		return false, res.Error
	}
	return res.RowsAffected > 0, nil
}

func (r *stockWatchRepository) Update(
	ctx context.Context, userID string, tenantID uint64, thscode string, patch interfaces.StockWatchPatch,
) (*types.StockWatch, error) {
	var rec types.StockWatch
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ?", userID, tenantID, thscode).
		First(&rec).Error
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil
		}
		return nil, err
	}

	updates := map[string]interface{}{}
	if patch.Name != nil {
		rec.Name = *patch.Name
		updates["name"] = rec.Name
	}
	if patch.SortOrder != nil {
		rec.SortOrder = *patch.SortOrder
		updates["sort_order"] = rec.SortOrder
	}
	if len(updates) == 0 {
		return &rec, nil
	}
	rec.UpdatedAt = time.Now()
	updates["updated_at"] = rec.UpdatedAt

	// Model(&rec) carries the composite primary key, so GORM scopes the UPDATE
	// to exactly this row — no separate WHERE to keep in sync.
	if err := r.db.WithContext(ctx).Model(&rec).Updates(updates).Error; err != nil {
		return nil, err
	}
	return &rec, nil
}
