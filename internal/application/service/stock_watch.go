package service

import (
	"context"
	"errors"
	"strings"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// Sentinel errors so the handler can map cleanly to HTTP status codes
// without leaking repository internals.
var (
	ErrStockWatchInvalidCode  = errors.New("invalid thscode")
	ErrStockWatchEmptyCode    = errors.New("thscode is required")
	ErrStockWatchLimitReached = errors.New("watchlist is full")
)

type stockWatchService struct {
	repo interfaces.StockWatchRepository
}

// NewStockWatchService wraps the repository with input normalisation and the
// per-user size cap. Kept thin on purpose — watching a symbol is a personal
// navigation action with no cross-aggregate side effects to audit.
func NewStockWatchService(repo interfaces.StockWatchRepository) interfaces.StockWatchService {
	return &stockWatchService{repo: repo}
}

// normaliseCode upper-cases and trims a thscode so that "600519.sh" and
// " 600519.SH " address the same row as "600519.SH". Without this the
// composite primary key would happily store both spellings as two rows.
func normaliseCode(code string) (string, error) {
	trimmed := strings.TrimSpace(code)
	if trimmed == "" {
		return "", ErrStockWatchEmptyCode
	}
	upper := strings.ToUpper(trimmed)
	if !types.IsValidStockWatchCode(upper) {
		return "", ErrStockWatchInvalidCode
	}
	return upper, nil
}

func (s *stockWatchService) List(
	ctx context.Context, userID string, tenantID uint64,
) ([]*types.StockWatch, error) {
	return s.repo.List(ctx, userID, tenantID)
}

func (s *stockWatchService) Add(
	ctx context.Context, userID string, tenantID uint64, thscode, name, exchange string,
) (*types.StockWatch, bool, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return nil, false, err
	}

	// Cap check first, then insert. Two concurrent adds can both pass the check
	// and end up one row over the cap; that is acceptable — the cap exists to
	// keep the page fast, not to be an exact invariant, and a lock on every add
	// would cost more than the one extra row.
	count, err := s.repo.Count(ctx, userID, tenantID)
	if err != nil {
		return nil, false, err
	}
	if count >= types.MaxStockWatchesPerUser {
		return nil, false, ErrStockWatchLimitReached
	}

	item := &types.StockWatch{
		UserID:   userID,
		TenantID: tenantID,
		THSCode:  code,
		Name:     strings.TrimSpace(name),
		Exchange: strings.TrimSpace(exchange),
	}
	return s.repo.Add(ctx, item)
}

func (s *stockWatchService) Remove(
	ctx context.Context, userID string, tenantID uint64, thscode string,
) (bool, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return false, err
	}
	return s.repo.Remove(ctx, userID, tenantID, code)
}

func (s *stockWatchService) Update(
	ctx context.Context, userID string, tenantID uint64, thscode string, patch interfaces.StockWatchPatch,
) (*types.StockWatch, error) {
	code, err := normaliseCode(thscode)
	if err != nil {
		return nil, err
	}
	if patch.Name != nil {
		trimmed := strings.TrimSpace(*patch.Name)
		patch.Name = &trimmed
	}
	return s.repo.Update(ctx, userID, tenantID, code, patch)
}
