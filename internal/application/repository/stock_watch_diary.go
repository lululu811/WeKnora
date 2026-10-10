package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// stockWatchDiaryRepository reads and writes the daily observation diary
// (see types.StockWatchDiary for why this is a table and not more events).
//
// The one non-obvious method is Upsert: (user_id, tenant_id, thscode,
// trade_date) is unique, so a rerun of the same trading day overwrites rather
// than appends. That is what makes the daily job safe to re-run by hand.
type stockWatchDiaryRepository struct {
	db *gorm.DB
}

// NewStockWatchDiaryRepository constructs the GORM-backed implementation.
func NewStockWatchDiaryRepository(db *gorm.DB) interfaces.StockWatchDiaryRepository {
	return &stockWatchDiaryRepository{db: db}
}

// ListDiaryScopes returns every (user_id, tenant_id) that has at least one
// symbol in a state worth writing a diary for.
//
// It reads stock_watches, NOT stock_watch_conditions, and that difference is
// the whole point: StockWatchConditionRepository.ListScopes is a DISTINCT over
// the condition table, so it only ever returns people who set a threshold.
// A diary is written for every tracked symbol in observing or holding, and the
// majority of a real pool has no conditions on it — reusing ListScopes would
// silently skip exactly the rows the feature is for.
//
// A scope with no eligible symbol is not a scope; deriving it from the pool
// table means that is the natural answer rather than something to filter.
func (r *stockWatchDiaryRepository) ListDiaryScopes(ctx context.Context) ([]types.StockWatchScope, error) {
	var scopes []types.StockWatchScope
	err := r.db.WithContext(ctx).
		Model(&types.StockWatch{}).
		Where("state IN ?", []string{
			types.StockWatchStateObserving, types.StockWatchStateHolding,
		}).
		Distinct("user_id", "tenant_id").
		Find(&scopes).Error
	return scopes, err
}

// ListWatchedByScope returns the symbols in one scope that are eligible for a
// diary, in a stable order.
//
// Stable because the prompt lists symbols in this order and the model's
// output is matched back by thscode; a random order would make two runs over
// identical inputs produce differently-ordered prompts, which is the kind of
// thing that shows up as unexplained verdict churn.
func (r *stockWatchDiaryRepository) ListWatchedByScope(
	ctx context.Context, userID string, tenantID uint64,
) ([]*types.StockWatch, error) {
	var list []*types.StockWatch
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND state IN ?", userID, tenantID, []string{
			types.StockWatchStateObserving, types.StockWatchStateHolding,
		}).
		Order("thscode ASC").
		Find(&list).Error
	return list, err
}

// ListBySymbol returns one symbol's diaries, newest trading day first.
func (r *stockWatchDiaryRepository) ListBySymbol(
	ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
) ([]*types.StockWatchDiary, error) {
	if limit <= 0 {
		limit = types.DefaultStockWatchDiaryLimit
	}
	var list []*types.StockWatchDiary
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ?", userID, tenantID, thscode).
		Order("trade_date DESC").
		Limit(limit).
		Find(&list).Error
	return list, err
}

// RecentBefore returns up to limit diaries for one symbol strictly older than
// `before`.
//
// Strictly older, not "up to and including": the history is fed to the model
// as what it already said, and including the row being written would be asking
// it to read its own answer back to itself. The caller passes the trade date
// it is about to write.
func (r *stockWatchDiaryRepository) RecentBefore(
	ctx context.Context, userID string, tenantID uint64, thscode string,
	before types.DateOnly, limit int,
) ([]*types.StockWatchDiary, error) {
	if limit <= 0 {
		limit = types.StockWatchDiaryHistoryDays
	}
	var list []*types.StockWatchDiary
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ? AND trade_date < ?",
			userID, tenantID, thscode, before).
		Order("trade_date DESC").
		Limit(limit).
		Find(&list).Error
	return list, err
}

// Get returns one diary by its identity key, or (nil, nil) when there is none.
//
// (nil, nil) rather than an error because "this symbol has no diary for that
// day" is an answer the caller must handle, not a failure: the drawer's
// "regenerate" action is exactly the case where no row is the normal state.
func (r *stockWatchDiaryRepository) Get(
	ctx context.Context, userID string, tenantID uint64, thscode string, tradeDate types.DateOnly,
) (*types.StockWatchDiary, error) {
	var row types.StockWatchDiary
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND thscode = ? AND trade_date = ?",
			userID, tenantID, thscode, tradeDate).
		First(&row).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// UpsertMany writes a run's diaries in one transaction.
//
// One transaction for the whole run, not per row: a run that half-succeeded
// would leave a pool where some symbols have today's diary and others do not,
// with nothing recording that the run was incomplete. All-or-nothing means
// the absence of a diary is always explainable by "the run failed", and
// stock_watch_notifications is what records that.
//
// Each row is an UPSERT on the unique index (user_id, tenant_id, thscode,
// trade_date), spelled out rather than left to GORM's inference: those four
// columns ARE the identity of a diary, and a wrong inference here would
// silently create a second row for a day the job has already written — the
// exact duplication the index exists to prevent.
func (r *stockWatchDiaryRepository) UpsertMany(
	ctx context.Context, diaries []*types.StockWatchDiary,
) error {
	if len(diaries) == 0 {
		return nil
	}
	return r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		for _, d := range diaries {
			if d == nil {
				continue
			}
			if d.TradeDate.IsZero() {
				return fmt.Errorf("diary %s: trade_date is required", d.THSCode)
			}
			if !types.IsValidStockWatchDiaryVerdict(d.Verdict) {
				return fmt.Errorf("diary %s: invalid verdict %q", d.THSCode, d.Verdict)
			}
			res := tx.Clauses(clause.OnConflict{
				Columns: []clause.Column{
					{Name: "user_id"}, {Name: "tenant_id"}, {Name: "thscode"}, {Name: "trade_date"},
				},
				DoUpdates: clause.AssignmentColumns([]string{
					"verdict", "confidence", "reasons", "body", "model_id", "readings", "updated_at",
				}),
			}).Create(d)
			if res.Error != nil {
				return res.Error
			}
		}
		return nil
	})
}

// ListByTradeDate returns every diary for one (user, tenant, trade_date).
// The grading job uses this to load the observation job's output.
func (r *stockWatchDiaryRepository) ListByTradeDate(
	ctx context.Context, userID string, tenantID uint64, tradeDate types.DateOnly,
) ([]*types.StockWatchDiary, error) {
	var list []*types.StockWatchDiary
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND trade_date = ?",
			userID, tenantID, tradeDate).
		Order("thscode ASC").
		Find(&list).Error
	return list, err
}

// UpdateScores writes final_score, rank and scores onto existing diary rows.
// UPDATE only, not UPSERT: the rows must already exist. A missing row is
// skipped silently — the grading job cannot score a diary that was never
// written, and erroring on one missing row would roll back the scores for
// every other symbol in the batch.
func (r *stockWatchDiaryRepository) UpdateScores(
	ctx context.Context, diaries []*types.StockWatchDiary,
) error {
	if len(diaries) == 0 {
		return nil
	}
	return r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		for _, d := range diaries {
			if d == nil || d.FinalScore == nil {
				continue
			}
			res := tx.Model(&types.StockWatchDiary{}).
				Where("user_id = ? AND tenant_id = ? AND thscode = ? AND trade_date = ?",
					d.UserID, d.TenantID, d.THSCode, d.TradeDate).
				Updates(map[string]any{
					"final_score": d.FinalScore,
					"rank":        d.Rank,
					"scores":      d.Scores,
				})
			if res.Error != nil {
				return res.Error
			}
		}
		return nil
	})
}

// TopRanked returns the top N scored diaries for one (user, tenant, trade_date).
// If tradeDate is zero, it finds the latest trade_date that has scored diaries.
// Only rows with a non-null final_score are included, ordered by score DESC.
func (r *stockWatchDiaryRepository) TopRanked(
	ctx context.Context, userID string, tenantID uint64,
	tradeDate types.DateOnly, limit int,
) ([]*types.StockWatchDiary, error) {
	if limit <= 0 {
		limit = 50
	}
	if tradeDate.IsZero() {
		var latest types.DateOnly
		err := r.db.WithContext(ctx).
			Model(&types.StockWatchDiary{}).
			Where("user_id = ? AND tenant_id = ? AND final_score IS NOT NULL", userID, tenantID).
			Select("MAX(trade_date)").
			Scan(&latest).Error
		if err != nil || latest.IsZero() {
			return nil, err
		}
		tradeDate = latest
	}
	var list []*types.StockWatchDiary
	err := r.db.WithContext(ctx).
		Where("user_id = ? AND tenant_id = ? AND trade_date = ? AND final_score IS NOT NULL",
			userID, tenantID, tradeDate).
		Order("final_score DESC").
		Limit(limit).
		Find(&list).Error
	return list, err
}
