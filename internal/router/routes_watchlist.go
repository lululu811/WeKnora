package router

import (
	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/handler"
)

// RegisterStockWatchRoutes wires the per-user watchlist endpoints
// ("个股追踪 / 持有股观察").
//
// Authorization: the handler always derives (user_id, tenant_id) from the auth
// context, so a Viewer floor is the right gate — there is no cross-user path to
// protect. Like favorites, these endpoints are deliberately not declared for
// scoped API keys (default-deny): a watchlist is edited by the human sitting in
// the UI, and an API key acting on "someone's" list would need a principal
// model these rows do not carry.
//
// Route shape note: /watchlist/:thscode is the only parameterised route under
// this group. Reordering is expressed by PUT-ing sort_order onto a row rather
// than a bulk /watchlist/order endpoint, because a static segment sibling of
// :thscode is exactly the shape that makes Gin's router ambiguous.
func RegisterStockWatchRoutes(r *gin.RouterGroup, h *handler.StockWatchHandler, g *rbacGuards) {
	watch := r.Group("/watchlist")
	{
		watch.GET("", g.Viewer(), h.ListStockWatch)
		watch.POST("", g.Viewer(), h.AddStockWatch)
		watch.PUT("/:thscode", g.Viewer(), h.UpdateStockWatch)
		watch.DELETE("/:thscode", g.Viewer(), h.RemoveStockWatch)
	}
}
