package handler

import (
	stderrors "errors"
	"net/http"
	"strconv"

	"github.com/Tencent/WeKnora/internal/application/service"
	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/gin-gonic/gin"
)

// StockWatchHandler exposes the per-user watchlist API ("个股追踪").
//
// Authorization model: identical to UserResourceFavoriteHandler — every query
// is scoped to the (user_id, tenant_id) pair taken from the auth context, and
// callers cannot pass either as a parameter. A watchlist is a personal
// navigation aid, so there is deliberately no admin-style "list another
// user's symbols" path.
type StockWatchHandler struct {
	service interfaces.StockWatchService
	// conditions is the user-authored numeric-condition surface. It is a
	// separate service because authoring a condition (validation, idempotency)
	// shares nothing with tracking a symbol, but it is the same handler so the
	// auth scoping rule and the error mapping are stated once.
	conditions interfaces.StockWatchConditionService
}

func NewStockWatchHandler(
	svc interfaces.StockWatchService,
	conditions interfaces.StockWatchConditionService,
) *StockWatchHandler {
	return &StockWatchHandler{service: svc, conditions: conditions}
}

// watchContext resolves the (userID, tenantID) pair to scope all queries to.
// Centralised so every endpoint answers an unauthenticated call with the same
// shape instead of three slightly different ones.
func watchContext(c *gin.Context) (string, uint64, bool) {
	uidVal, ok := c.Get(types.UserIDContextKey.String())
	if !ok {
		c.Error(apperrors.NewUnauthorizedError("user ID not found"))
		return "", 0, false
	}
	userID, _ := uidVal.(string)
	if userID == "" {
		c.Error(apperrors.NewUnauthorizedError("user ID not found"))
		return "", 0, false
	}
	tenantID := c.GetUint64(types.TenantIDContextKey.String())
	if tenantID == 0 {
		c.Error(apperrors.NewUnauthorizedError("workspace ID not found"))
		return "", 0, false
	}
	return userID, tenantID, true
}

// mapStockWatchError translates the service's sentinel errors to HTTP status
// codes. Returns true when it handled the error.
func mapStockWatchError(c *gin.Context, err error) bool {
	switch {
	case stderrors.Is(err, service.ErrStockWatchInvalidCode),
		stderrors.Is(err, service.ErrStockWatchEmptyCode),
		stderrors.Is(err, service.ErrStockWatchInvalidState),
		stderrors.Is(err, service.ErrStockWatchNoteTooLong),
		// 条件：字段/方向不在白名单、阈值不是有限数、id 为空，都是调用方输入问题。
		stderrors.Is(err, service.ErrStockWatchConditionInvalidField),
		stderrors.Is(err, service.ErrStockWatchConditionInvalidOp),
		stderrors.Is(err, service.ErrStockWatchConditionInvalidValue),
		stderrors.Is(err, service.ErrStockWatchConditionInvalidID),
		// 域错误：状态机不认这一步。它与"值不存在"同属调用方输入问题，同样 400。
		stderrors.Is(err, types.ErrStockWatchIllegalTransition):
		c.Error(apperrors.NewBadRequestError(err.Error()))
	case stderrors.Is(err, service.ErrStockWatchLimitReached):
		c.Error(apperrors.NewConflictError(err.Error()))
	default:
		return false
	}
	return true
}

// ListStockWatch godoc
// @Summary      List my watchlist
// @Description  Lists the calling user's watched symbols in the current workspace, in display order
// @Tags         User
// @Success      200  {object}  map[string]interface{}
// @Router       /watchlist [get]
func (h *StockWatchHandler) ListStockWatch(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	list, err := h.service.List(ctx, userID, tenantID)
	if err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if list == nil {
		list = []*types.StockWatch{}
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": list})
}

// AddStockWatchRequest is the body for POST /watchlist.
//
// Name and exchange are optional: the caller (the picker) already has them
// from the symbol search, but a caller that only knows the code still works —
// the row then renders as the bare code until a re-add refreshes the label.
type AddStockWatchRequest struct {
	THSCode  string `json:"thscode"`
	Name     string `json:"name"`
	Exchange string `json:"exchange"`
}

// AddStockWatch godoc
// @Summary      Watch a symbol
// @Description  Adds a symbol to the calling user's watchlist; re-adding refreshes the stored name
// @Tags         User
// @Param        body  body      AddStockWatchRequest  true  "Symbol to watch"
// @Success      200   {object}  map[string]interface{}
// @Router       /watchlist [post]
func (h *StockWatchHandler) AddStockWatch(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	var req AddStockWatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body").WithDetails(err.Error()))
		return
	}
	row, created, err := h.service.Add(ctx, userID, tenantID, req.THSCode, req.Name, req.Exchange)
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": row, "created": created})
}

// RemoveStockWatch godoc
// @Summary      Stop watching a symbol
// @Tags         User
// @Param        thscode  path  string  true  "Symbol, e.g. 600519.SH"
// @Success      200      {object}  map[string]interface{}
// @Router       /watchlist/{thscode} [delete]
func (h *StockWatchHandler) RemoveStockWatch(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	removed, err := h.service.Remove(ctx, userID, tenantID, c.Param("thscode"))
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	// Removed=false is not an error: the caller's goal ("this symbol must not
	// be on my list") holds either way, and a 404 here would only teach the
	// frontend to retry something that already succeeded.
	c.JSON(http.StatusOK, gin.H{"success": true, "removed": removed})
}

// UpdateStockWatchRequest is the body for PUT /watchlist/:thscode.
// Pointer fields keep "leave unchanged" distinguishable from "set to empty".
type UpdateStockWatchRequest struct {
	Name      *string `json:"name"`
	SortOrder *int    `json:"sort_order"`
	// State is the pool's manual state machine; only the values in
	// types.StockWatchState* are accepted, and only a legal move from the
	// row's current state (同一个事务里校验).
	State *string `json:"state"`
	// Note is the user's reason for tracking the symbol; "" clears it.
	Note *string `json:"note"`
}

// ListStockWatchEvents godoc
// @Summary      Read my pool's event history
// @Description  Lists the append-only event log of the calling user's tracking pool, newest first
// @Tags         User
// @Param        thscode  query  string  false  "Narrow to one symbol, e.g. 600519.SH"
// @Param        limit    query  int     false  "Max rows (default 50, max 200)"
// @Success      200      {object}  map[string]interface{}
// @Router       /watchlist/events [get]
func (h *StockWatchHandler) ListStockWatchEvents(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	// limit 解析失败就退回默认值而不是 400：历史条数是展示细节，为它把整页
	// 活动流打成错误得不偿失。
	limit, _ := strconv.Atoi(c.Query("limit"))
	list, err := h.service.ListEvents(ctx, userID, tenantID, c.Query("thscode"), limit)
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if list == nil {
		list = []*types.StockWatchEvent{}
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": list})
}

// UpdateStockWatch godoc
// @Summary      Update a watched symbol
// @Description  Renames a row or moves it in the display order
// @Tags         User
// @Param        thscode  path  string                  true  "Symbol, e.g. 600519.SH"
// @Param        body    body  UpdateStockWatchRequest  true  "Fields to patch"
// @Success      200     {object}  map[string]interface{}
// @Router       /watchlist/{thscode} [put]
func (h *StockWatchHandler) UpdateStockWatch(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	var req UpdateStockWatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body").WithDetails(err.Error()))
		return
	}
	if req.Name == nil && req.SortOrder == nil && req.State == nil && req.Note == nil {
		c.Error(apperrors.NewBadRequestError("nothing to update: pass name, sort_order, state or note"))
		return
	}
	row, err := h.service.Update(ctx, userID, tenantID, c.Param("thscode"), interfaces.StockWatchPatch{
		Name:      req.Name,
		SortOrder: req.SortOrder,
		State:     req.State,
		Note:      req.Note,
	})
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if row == nil {
		c.Error(apperrors.NewNotFoundError("symbol is not in your watchlist"))
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": row})
}
