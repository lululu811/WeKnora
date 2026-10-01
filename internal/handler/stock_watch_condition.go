package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"

	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
)

// ListStockWatchConditions godoc
// @Summary      List a symbol's conditions
// @Description  Lists the calling user's authored conditions for one tracked symbol
// @Tags         User
// @Param        thscode  path  string  true  "Symbol, e.g. 600519.SH"
// @Success      200  {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/conditions [get]
func (h *StockWatchHandler) ListStockWatchConditions(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	list, err := h.conditions.List(ctx, userID, tenantID, c.Param("thscode"))
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	if list == nil {
		list = []*types.StockWatchCondition{}
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": list})
}

// AddStockWatchConditionRequest is the body for POST /watchlist/:thscode/conditions.
//
// Value is a pointer so a missing field is rejected instead of silently read as
// 0 — zero is a real threshold (跌破 0 不是没有意义，对某些读数就是), and a
// caller that forgot the number must be told, not given a condition they did
// not write.
type AddStockWatchConditionRequest struct {
	Field string   `json:"field"`
	Op    string   `json:"op"`
	Value *float64 `json:"value"`
}

// AddStockWatchCondition godoc
// @Summary      Add a numeric condition to a symbol
// @Description  Adds one threshold the daily job watches; re-adding the same one is idempotent
// @Tags         User
// @Param        thscode  path  string                       true  "Symbol, e.g. 600519.SH"
// @Param        body    body  AddStockWatchConditionRequest  true  "field/op/value"
// @Success      200     {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/conditions [post]
func (h *StockWatchHandler) AddStockWatchCondition(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	var req AddStockWatchConditionRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.Error(apperrors.NewBadRequestError("invalid request body").WithDetails(err.Error()))
		return
	}
	if req.Value == nil {
		c.Error(apperrors.NewBadRequestError("condition value is required"))
		return
	}
	row, created, err := h.conditions.Create(
		ctx, userID, tenantID, c.Param("thscode"), req.Field, req.Op, *req.Value)
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	// created=false is a success: the user's goal ("this condition must exist")
	// already holds, and a 409 would only teach the UI to show an error for a
	// double-click that changed nothing.
	c.JSON(http.StatusOK, gin.H{"success": true, "data": row, "created": created})
}

// RemoveStockWatchCondition godoc
// @Summary      Remove a condition
// @Tags         User
// @Param        thscode  path  string  true  "Symbol, e.g. 600519.SH"
// @Param        id      path  string  true  "Condition id"
// @Success      200     {object}  map[string]interface{}
// @Router       /watchlist/{thscode}/conditions/{id} [delete]
func (h *StockWatchHandler) RemoveStockWatchCondition(c *gin.Context) {
	ctx := c.Request.Context()
	userID, tenantID, ok := watchContext(c)
	if !ok {
		return
	}
	removed, err := h.conditions.Remove(ctx, userID, tenantID, c.Param("thscode"), c.Param("id"))
	if err != nil {
		if mapStockWatchError(c, err) {
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(apperrors.NewInternalServerError(err.Error()))
		return
	}
	// removed=false is not an error, same reasoning as the pool's Remove.
	c.JSON(http.StatusOK, gin.H{"success": true, "removed": removed})
}
