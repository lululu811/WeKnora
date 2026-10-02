package handler

import (
	"net/http"
	"strconv"

	"github.com/Tencent/WeKnora/internal/application/service"
	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/gin-gonic/gin"
)

// StockWatchDiaryHandler exposes the daily observation diary.
//
// Authorization is identical to StockWatchHandler and for the same reason:
// every query is scoped to the (user_id, tenant_id) from the auth context and
// neither is a parameter.
//
// The one rule this handler exists to enforce is that a model's opinion never
// becomes a pool state on its own. Accept is a POST the human makes; it is the
// only path in the whole feature that can move `state`, and it moves it
// because someone clicked.
type StockWatchDiaryHandler struct {
	diaries *service.StockWatchDiaryService
	watches interfaces.StockWatchService
}

// NewStockWatchDiaryHandler wires the diary endpoints.
//
// watches is the pool service, not the repository, because accepting a verdict
// has to go through the same state machine and the same event log as a manual
// state change. Calling the repository directly would be the one way to move
// `state` without leaving a trace, which is exactly what must not be possible.
func NewStockWatchDiaryHandler(
	diaries *service.StockWatchDiaryService, watches interfaces.StockWatchService,
) *StockWatchDiaryHandler {
	return &StockWatchDiaryHandler{diaries: diaries, watches: watches}
}

// ListStockWatchDiaries godoc
// @Summary      List one symbol's observation diaries
// @Description  Returns the daily LLM observations for a watched symbol, newest trading day first
// @Tags         User
// @Param        thscode  path   string  true   "Symbol, e.g. 600519.SH"
// @Param        limit    query  int     false  "Max rows (default 30, max 250)"
// @Success      200      {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/diaries [get]
func (h *StockWatchDiaryHandler) ListStockWatchDiaries(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	thscode := c.Param("thscode")
	list, err := h.diaries.ListBySymbol(ctx, userID, tenantID, thscode, diaryLimit(c))
	if err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": list})
}

// diaryLimit reads the optional `limit` query parameter.
//
// An unparseable or negative value falls back to the default rather than
// erroring: the parameter exists to bound a response, and refusing a request
// over a malformed bound helps nobody. The service clamps the upper end.
func diaryLimit(c *gin.Context) int {
	raw := c.Query("limit")
	if raw == "" {
		return 0
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 {
		return 0
	}
	return n
}

// AcceptStockWatchDiaryRequest is the body for adopting a verdict.
type AcceptStockWatchDiaryRequest struct {
	// TradeDate is the trading day of the diary being adopted, YYYY-MM-DD.
	//
	// It is required rather than defaulted to "the latest" so that the event
	// log records which observation the human acted on. Defaulting to the
	// newest row would make an adoption from the drawer ambiguous the moment
	// a new diary lands overnight.
	TradeDate string `json:"trade_date" binding:"required"`
	// ToState is where the human is moving the symbol. It is validated against
	// the diary's own verdict set, so a client cannot use "accept" as a
	// back door for an arbitrary state change: accepting "buy" can only mean
	// holding, and accepting "exit" can only mean dropping.
	ToState string `json:"to_state" binding:"required"`
}

// AcceptStockWatchDiary godoc
// @Summary      Adopt an observation diary's verdict
// @Description  Moves the symbol to the state the user chose, and records the adoption in the pool's event log
// @Tags         User
// @Param        thscode  path   string                        true  "Symbol, e.g. 600519.SH"
// @Param        body     body   AcceptStockWatchDiaryRequest  true  "Verdict to adopt"
// @Success      200      {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/diaries/accept [post]
func (h *StockWatchDiaryHandler) AcceptStockWatchDiary(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	thscode := c.Param("thscode")
	var req AcceptStockWatchDiaryRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body").WithDetails(err.Error()))
		return
	}
	tradeDate, err := types.ParseDateOnly(req.TradeDate)
	if err != nil {
		c.Error(apperrors.NewBadRequestError("trade_date must be YYYY-MM-DD"))
		return
	}
	diary, err := h.diaries.Get(ctx, userID, tenantID, thscode, tradeDate)
	if err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if diary == nil {
		// Adopting a diary that does not exist would write an event claiming
		// the user acted on a judgement nobody ever read.
		c.Error(apperrors.NewNotFoundError("no diary for that trading day"))
		return
	}
	if err := types.CheckVerdictAdoption(diary.Verdict, req.ToState); err != nil {
		c.Error(apperrors.NewBadRequestError(err.Error()))
		return
	}
	adoption := adoptionNote(diary, req.ToState)
	row, err := h.watches.Update(ctx, userID, tenantID, thscode, interfaces.StockWatchPatch{
		State: &req.ToState,
		// The event's note records WHICH observation was acted on. Without it
		// the log would say the state changed without saying what prompted it.
		Note: &adoption,
	})
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": row})
}

// IgnoreStockWatchDiaryRequest is the body for declining a verdict.
type IgnoreStockWatchDiaryRequest struct {
	TradeDate string `json:"trade_date" binding:"required"`
}

// IgnoreStockWatchDiary godoc
// @Summary      Decline an observation diary's verdict
// @Description  Records that the user saw the verdict and did not act on it; the pool state is unchanged
// @Tags         User
// @Param        thscode  path   string                        true  "Symbol, e.g. 600519.SH"
// @Param        body     body   IgnoreStockWatchDiaryRequest  true  "Diary to decline"
// @Success      200      {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/diaries/ignore [post]
func (h *StockWatchDiaryHandler) IgnoreStockWatchDiary(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	thscode := c.Param("thscode")
	var req IgnoreStockWatchDiaryRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body").WithDetails(err.Error()))
		return
	}
	tradeDate, err := types.ParseDateOnly(req.TradeDate)
	if err != nil {
		c.Error(apperrors.NewBadRequestError("trade_date must be YYYY-MM-DD"))
		return
	}
	diary, err := h.diaries.Get(ctx, userID, tenantID, thscode, tradeDate)
	if err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if diary == nil {
		c.Error(apperrors.NewNotFoundError("no diary for that trading day"))
		return
	}
	if err := h.diaries.RecordVerdictIgnored(ctx, userID, tenantID, thscode, tradeDate, diary.Verdict); err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true})
}

// adoptionNote is the note snapshot written onto the state_changed event when
// a verdict is adopted.
//
// It names the verdict and its date rather than copying the model's paragraph:
// the event log answers "what did I decide and why", and the diary itself is
// the record of the reasoning. Duplicating a few hundred characters into the
// log would make it read worse without making it more true.
func adoptionNote(diary *types.StockWatchDiary, toState string) string {
	base := "采纳 " + diary.TradeDate.String() + " 的观察建议：" + diary.Verdict + " → " + toState
	if diary.Reasons != "" {
		return base + "（" + diary.Reasons + "）"
	}
	return base
}
