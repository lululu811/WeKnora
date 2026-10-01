package service

import (
	"context"
	"errors"
	"math"
	"strings"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// Sentinel errors for user-authored conditions, mapped to 400 by the handler
// (see mapStockWatchError). They live next to the pool's sentinels because they
// belong to the same feature surface and the handler already has one mapping.
var (
	ErrStockWatchConditionInvalidField = errors.New("invalid condition field")
	ErrStockWatchConditionInvalidOp    = errors.New("invalid condition op")
	// ErrStockWatchConditionInvalidValue rejects NaN and both infinities.
	// A non-finite threshold can never be crossed honestly: `reading > NaN` is
	// false for every reading, so the condition would sit on the page looking
	// active while being permanently dead — and `+Inf`/`-Inf` would fire (or
	// never fire) for reasons the user never authored.
	ErrStockWatchConditionInvalidValue = errors.New("condition value must be a finite number")
	ErrStockWatchConditionInvalidID    = errors.New("invalid condition id")
)

type stockWatchConditionService struct {
	repo interfaces.StockWatchConditionRepository
}

// NewStockWatchConditionService wraps the repository with the allowlist and
// finiteness checks. Kept thin: a condition has no cross-aggregate side effects
// to audit at authoring time (the run-time effects are the job's concern).
func NewStockWatchConditionService(
	repo interfaces.StockWatchConditionRepository,
) interfaces.StockWatchConditionService {
	return &stockWatchConditionService{repo: repo}
}

func (s *stockWatchConditionService) List(
	ctx context.Context, userID string, tenantID uint64, thscode string,
) ([]*types.StockWatchCondition, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return nil, err
	}
	return s.repo.List(ctx, userID, tenantID, code)
}

func (s *stockWatchConditionService) Create(
	ctx context.Context, userID string, tenantID uint64, thscode, field, op string, value float64,
) (*types.StockWatchCondition, bool, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return nil, false, err
	}
	field = strings.TrimSpace(field)
	if !types.IsValidStockWatchConditionField(field) {
		return nil, false, ErrStockWatchConditionInvalidField
	}
	op = strings.TrimSpace(op)
	if !types.IsValidStockWatchConditionOp(op) {
		return nil, false, ErrStockWatchConditionInvalidOp
	}
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, false, ErrStockWatchConditionInvalidValue
	}
	return s.repo.Create(ctx, &types.StockWatchCondition{
		UserID:   userID,
		TenantID: tenantID,
		THSCode:  code,
		Field:    field,
		Op:       op,
		Value:    value,
	})
}

func (s *stockWatchConditionService) Remove(
	ctx context.Context, userID string, tenantID uint64, thscode, id string,
) (bool, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return false, err
	}
	id = strings.TrimSpace(id)
	if id == "" {
		return false, ErrStockWatchConditionInvalidID
	}
	return s.repo.Remove(ctx, userID, tenantID, code, id)
}
