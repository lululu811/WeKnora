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
//
// A genuinely new row is also the pool's first event (`added`), written in the
// same transaction: an entry with no record of entering the pool would make
// the history a partial truth. A mere re-add is not an `added` event — the
// symbol never left — and the name refresh has no event kind of its own, so it
// stays out of the log rather than being dressed up as something it is not.
func (r *stockWatchRepository) Add(
	ctx context.Context, item *types.StockWatch,
) (*types.StockWatch, bool, error) {
	var (
		out     *types.StockWatch
		created bool
	)
	err := r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		rec := *item
		res := tx.
			Where(&types.StockWatch{UserID: item.UserID, TenantID: item.TenantID, THSCode: item.THSCode}).
			FirstOrCreate(&rec)
		if res.Error != nil {
			return res.Error
		}
		created = res.RowsAffected > 0
		if created {
			// FirstOrCreate 拿到的是数据库行的状态，但 State/Note 由调用方带入，
			// 这里以落库后的 rec 为准记账，避免事件里的 to_state 与实际行不一致。
			if err := insertStockWatchEvent(tx, &types.StockWatchEvent{
				UserID:   rec.UserID,
				TenantID: rec.TenantID,
				Kind:     types.StockWatchEventAdded,
				THSCode:  rec.THSCode,
				ToState:  rec.State,
				Note:     rec.Note,
			}); err != nil {
				return err
			}
			out = &rec
			return nil
		}
		if rec.Name == item.Name && rec.Exchange == item.Exchange {
			out = &rec
			return nil
		}
		rec.Name = item.Name
		rec.Exchange = item.Exchange
		rec.UpdatedAt = time.Now()
		if err := tx.
			Model(&types.StockWatch{}).
			Where("user_id = ? AND tenant_id = ? AND thscode = ?", item.UserID, item.TenantID, item.THSCode).
			Updates(map[string]interface{}{
				"name":       rec.Name,
				"exchange":   rec.Exchange,
				"updated_at": rec.UpdatedAt,
			}).Error; err != nil {
			return err
		}
		out = &rec
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	return out, created, nil
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

// Update patches one row inside a single transaction.
//
// Two things happen here that must not be separable:
//   - the state transition is validated against the row as it is *inside* the
//     transaction, not against whatever a caller read a moment ago. Two
//     concurrent PUTs each validating their own snapshot could otherwise both
//     pass and land a move neither one was allowed to make.
//   - each state/note change writes its event through the same tx, after the
//     row update succeeds. So a failed row update leaves no event, and a
//     failed event insert rolls the row update back: the log can never claim
//     something the pool did not do, and the pool can never change with no
//     trace of why.
func (r *stockWatchRepository) Update(
	ctx context.Context, userID string, tenantID uint64, thscode string, patch interfaces.StockWatchPatch,
) (*types.StockWatch, error) {
	var updated *types.StockWatch
	err := r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var rec types.StockWatch
		if err := tx.
			Where("user_id = ? AND tenant_id = ? AND thscode = ?", userID, tenantID, thscode).
			First(&rec).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				// (nil, nil): "no such row", not "the update failed" — the
				// handler decides whether that is a 404 or something to ignore.
				return nil
			}
			return err
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
		stateChanged := false
		fromState := rec.State
		if patch.State != nil && *patch.State != rec.State {
			if !types.StockWatchStateCanTransition(rec.State, *patch.State) {
				return types.ErrStockWatchIllegalTransition
			}
			rec.State = *patch.State
			updates["state"] = rec.State
			stateChanged = true
		}
		noteChanged := false
		if patch.Note != nil && *patch.Note != rec.Note {
			rec.Note = *patch.Note
			updates["note"] = rec.Note
			noteChanged = true
		}
		if len(updates) == 0 {
			// 空 patch 是"什么都不改"，不是清空字段 —— 也就没有事件可写。
			updated = &rec
			return nil
		}
		rec.UpdatedAt = time.Now()
		updates["updated_at"] = rec.UpdatedAt

		// Model(&rec) carries the composite primary key, so GORM scopes the
		// UPDATE to exactly this row — no separate WHERE to keep in sync.
		if err := tx.Model(&rec).Updates(updates).Error; err != nil {
			return err
		}

		// 事件在行更新成功之后写。state_changed 带上是时的备注快照，因为
		// "为什么这次改状态" 正是事件要留住的东西；note_changed 不写状态，
		// from/to 只在状态本身是事件的主题时才填。
		if stateChanged {
			if err := insertStockWatchEvent(tx, &types.StockWatchEvent{
				UserID:    userID,
				TenantID:  tenantID,
				Kind:      types.StockWatchEventStateChanged,
				THSCode:   rec.THSCode,
				FromState: fromState,
				ToState:   rec.State,
				Note:      rec.Note,
			}); err != nil {
				return err
			}
		}
		if noteChanged {
			if err := insertStockWatchEvent(tx, &types.StockWatchEvent{
				UserID:   userID,
				TenantID: tenantID,
				Kind:     types.StockWatchEventNoteChanged,
				THSCode:  rec.THSCode,
				Note:     rec.Note,
			}); err != nil {
				return err
			}
		}
		updated = &rec
		return nil
	})
	if err != nil {
		return nil, err
	}
	return updated, nil
}
