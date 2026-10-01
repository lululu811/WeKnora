package service

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// Sentinel errors so the handler can map cleanly to HTTP status codes
// without leaking repository internals.
var (
	ErrStockWatchInvalidCode  = errors.New("invalid thscode")
	ErrStockWatchEmptyCode    = errors.New("thscode is required")
	ErrStockWatchLimitReached = errors.New("watchlist is full")
	// ErrStockWatchInvalidState means the value is not one of the four known
	// states. "Is this a state at all" is a cheap, snapshot-free check, so it
	// belongs here; "is this move legal" needs the row as it is right now and
	// is enforced by the repository inside its transaction
	// (types.ErrStockWatchIllegalTransition).
	ErrStockWatchInvalidState = errors.New("invalid watch state")
	// ErrStockWatchNoteTooLong guards the note column's varchar(200) before
	// the database would answer with a truncation or a driver error.
	ErrStockWatchNoteTooLong = errors.New("note is too long")
)

type stockWatchService struct {
	repo   interfaces.StockWatchRepository
	events interfaces.StockWatchEventsRepository
}

// NewStockWatchService wraps the repositories with input normalisation and the
// per-user size cap. Kept thin on purpose — tracking a symbol is a personal
// navigation action with no cross-aggregate side effects to audit.
func NewStockWatchService(
	repo interfaces.StockWatchRepository, events interfaces.StockWatchEventsRepository,
) interfaces.StockWatchService {
	return &stockWatchService{repo: repo, events: events}
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
		// 显式写入默认态，而不是依赖列的 DEFAULT：新增行要同时喂给 `added`
		// 事件（to_state），只有真值在手，事件才不用去猜刚落库的是什么。
		State: types.StockWatchStateObserving,
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
	if patch.State != nil {
		state := strings.TrimSpace(*patch.State)
		if !types.IsValidStockWatchState(state) {
			return nil, ErrStockWatchInvalidState
		}
		patch.State = &state
	}
	if patch.Note != nil {
		// Trim first, then measure: a note of 200 chars plus a stray trailing
		// space is not "too long", and "" now means "clear the note".
		note := strings.TrimSpace(*patch.Note)
		if utf8.RuneCountInString(note) > types.MaxStockWatchNoteLen {
			return nil, ErrStockWatchNoteTooLong
		}
		patch.Note = &note
	}
	return s.repo.Update(ctx, userID, tenantID, code, patch)
}

func (s *stockWatchService) ListEvents(
	ctx context.Context, userID string, tenantID uint64, thscode string, limit int,
) ([]*types.StockWatchEvent, error) {
	// 空 thscode = 整池的活动流；非空才校验格式 —— 让"看全池"这条最常用的
	// 路径不必伪造一个代码。limit 在这里夹紧，repository 就不必替调用方拿主意。
	var code string
	if strings.TrimSpace(thscode) != "" {
		normalised, err := normaliseCode(thscode)
		if err != nil {
			return nil, err
		}
		code = normalised
	}
	if limit <= 0 {
		limit = types.DefaultStockWatchEventLimit
	}
	if limit > types.MaxStockWatchEventLimit {
		limit = types.MaxStockWatchEventLimit
	}
	return s.events.List(ctx, userID, tenantID, code, limit)
}
