package repository

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// stockWatchConditionRepository is the GORM-backed store for user-authored
// numeric conditions and their evaluation watermark.
type stockWatchConditionRepository struct {
	db *gorm.DB
}

// NewStockWatchConditionRepository constructs the GORM-backed implementation.
func NewStockWatchConditionRepository(db *gorm.DB) interfaces.StockWatchConditionRepository {
	return &stockWatchConditionRepository{db: db}
}

func (r *stockWatchConditionRepository) List(
	ctx context.Context, userID string, tenantID uint64, thscode string,
) ([]*types.StockWatchCondition, error) {
	var list []*types.StockWatchCondition
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ?", userID, tenantID, thscode).
		Order("created_at ASC, id ASC").
		Find(&list).Error
	return list, err
}

// Create is idempotent on the natural key (thscode, field, op, value).
//
// A condition is its threshold; there is nothing else to say about it, so
// re-adding the same one is not a new fact. This is a find-then-insert rather
// than GORM's FirstOrCreate: the primary key here is a surrogate uuid, and a
// FirstOrCreate whose destination already carries an id would look the row up
// BY ID, miss, and then die on the unique index — the exact opposite of
// idempotent.
//
// The unique index remains the invariant; the fallback lookup below is how a
// genuine concurrent insert (two requests both missing, both inserting) still
// returns the same row instead of a 500.
func (r *stockWatchConditionRepository) Create(
	ctx context.Context, cond *types.StockWatchCondition,
) (*types.StockWatchCondition, bool, error) {
	existing, err := r.findByNaturalKey(ctx, cond)
	if err != nil {
		return nil, false, err
	}
	if existing != nil {
		return existing, false, nil
	}

	rec := *cond
	if rec.ID == "" {
		rec.ID = uuid.New().String()
	}
	if err := r.db.WithContext(ctx).Create(&rec).Error; err != nil {
		// A concurrent insert may have won the race between the lookup and this
		// insert; if so the row the caller wanted now exists, which is success.
		if raced, ferr := r.findByNaturalKey(ctx, cond); ferr == nil && raced != nil {
			return raced, false, nil
		}
		return nil, false, err
	}
	return &rec, true, nil
}

func (r *stockWatchConditionRepository) findByNaturalKey(
	ctx context.Context, cond *types.StockWatchCondition,
) (*types.StockWatchCondition, error) {
	var found types.StockWatchCondition
	err := r.db.WithContext(ctx).
		Where(
			"user_id = ? AND tenant_id = ? AND thscode = ? AND field = ? AND op = ? AND value = ?",
			cond.UserID, cond.TenantID, cond.THSCode, cond.Field, cond.Op, cond.Value,
		).
		First(&found).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &found, nil
}

func (r *stockWatchConditionRepository) Remove(
	ctx context.Context, userID string, tenantID uint64, thscode, id string,
) (bool, error) {
	res := r.db.WithContext(ctx).
		Where(
			"user_id = ? AND tenant_id = ? AND thscode = ? AND id = ?",
			userID, tenantID, thscode, id,
		).
		Delete(&types.StockWatchCondition{})
	if res.Error != nil {
		return false, res.Error
	}
	return res.RowsAffected > 0, nil
}

func (r *stockWatchConditionRepository) ListScopes(ctx context.Context) ([]types.StockWatchScope, error) {
	var scopes []types.StockWatchScope
	err := r.db.WithContext(ctx).
		Model(&types.StockWatchCondition{}).
		Distinct("user_id", "tenant_id").
		Find(&scopes).Error
	return scopes, err
}

func (r *stockWatchConditionRepository) ListByScope(
	ctx context.Context, userID string, tenantID uint64,
) ([]*types.StockWatchCondition, error) {
	var list []*types.StockWatchCondition
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ?", userID, tenantID).
		Order("thscode ASC, created_at ASC, id ASC").
		Find(&list).Error
	return list, err
}

// ApplyEvaluations applies one run's decisions for one scope atomically.
//
// The transaction is the whole scope, not one condition: the watermark is only
// trustworthy if the day it names was fully decided. Committing condition A's
// watermark while condition B's crossing is still unwritten would make the next
// run skip B forever ("no new trading day" for B) — a silently lost alert, the
// exact failure this feature exists to avoid.
//
// The event insert is guarded by the row update actually matching (RowsAffected
// > 0): a condition deleted between the read and this write must not leave an
// event claiming a crossing for a row that no longer exists. from_state /
// to_state stay empty — a condition crossing is not a pool state transition,
// and `state` remains human-only.
func (r *stockWatchConditionRepository) ApplyEvaluations(
	ctx context.Context, userID string, tenantID uint64,
	transitions []types.StockWatchConditionTransition,
) error {
	if len(transitions) == 0 {
		return nil
	}
	now := time.Now()
	return r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		for _, t := range transitions {
			// The column is a DATE; bind a real date value rather than the raw
			// string so both drivers encode it natively (pgx rejects a string
			// for a DATE parameter in binary form).
			evalDate, err := types.ParseDateOnly(t.EvalDate)
			if err != nil {
				return fmt.Errorf("condition %s: invalid eval date %q: %w", t.ConditionID, t.EvalDate, err)
			}
			res := tx.Model(&types.StockWatchCondition{}).
				Where("id = ? AND user_id = ? AND tenant_id = ? AND thscode = ?",
					t.ConditionID, userID, tenantID, t.THSCode).
				Updates(map[string]interface{}{
					"last_satisfied": t.Satisfied,
					"last_eval_date": evalDate,
					"updated_at":     now,
				})
			if res.Error != nil {
				return res.Error
			}
			if !t.Fired || res.RowsAffected == 0 {
				continue
			}
			if err := insertStockWatchEvent(tx, &types.StockWatchEvent{
				UserID:   userID,
				TenantID: tenantID,
				Kind:     types.StockWatchEventConditionTriggered,
				THSCode:  t.THSCode,
				Note:     t.Note,
				// 事件的"哪一天"来自判定，不来自插入时间：任务在 D+1 早上报告 D 日收盘。
				EvalDate: &evalDate,
			}); err != nil {
				return err
			}
		}
		return nil
	})
}

// stockWatchNotificationRepository records delivery attempts (append-only).
type stockWatchNotificationRepository struct {
	db *gorm.DB
}

// NewStockWatchNotificationRepository constructs the GORM-backed implementation.
func NewStockWatchNotificationRepository(db *gorm.DB) interfaces.StockWatchNotificationRepository {
	return &stockWatchNotificationRepository{db: db}
}

func (r *stockWatchNotificationRepository) Insert(
	ctx context.Context, notification *types.StockWatchNotification,
) error {
	return r.db.WithContext(ctx).Create(notification).Error
}
