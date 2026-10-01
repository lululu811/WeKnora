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
//
// GET /watchlist/events is such a static sibling. It was chosen over
// `GET /watchlist/:thscode/events` deliberately: the pool-wide activity feed is
// the view that matters, and a per-symbol path would force the feed to fan out
// one request per symbol (or invent a fake thscode). Narrowing to one symbol is
// a `?thscode=` query param on the same route instead.
//
// Is it actually safe? Yes, on two counts:
//   - Gin builds one radix tree per *method*, and the parameterised sibling
//     exists only for PUT and DELETE. There is no `GET /watchlist/:thscode`, so
//     the two GET paths never even meet.
//   - Even if one were added later, gin v1.12 resolves static-vs-parameter
//     siblings rather than panicking (verified: registering both orders is
//     accepted, and the static node wins). The path stays unambiguous for a
//     stronger reason than router internals: a thscode must match
//     `^\d{6}\.(SH|SZ|BJ|HK|US)$` (types.IsValidStockWatchCode), so "events" can
//     never be a symbol and the two can never mean the same thing.
func RegisterStockWatchRoutes(r *gin.RouterGroup, h *handler.StockWatchHandler, g *rbacGuards) {
	watch := r.Group("/watchlist")
	{
		watch.GET("", g.Viewer(), h.ListStockWatch)
		watch.GET("/events", g.Viewer(), h.ListStockWatchEvents)
		watch.POST("", g.Viewer(), h.AddStockWatch)
		watch.PUT("/:thscode", g.Viewer(), h.UpdateStockWatch)
		watch.DELETE("/:thscode", g.Viewer(), h.RemoveStockWatch)

		// Conditions live under the symbol they watch: a condition is
		// meaningless without its symbol, and the URL says so. These are
		// strictly deeper than /:thscode, so Gin has no static-vs-parameter
		// ambiguity to resolve (the "/events" note above applies a fortiori).
		watch.GET("/:thscode/conditions", g.Viewer(), h.ListStockWatchConditions)
		watch.POST("/:thscode/conditions", g.Viewer(), h.AddStockWatchCondition)
		watch.DELETE("/:thscode/conditions/:id", g.Viewer(), h.RemoveStockWatchCondition)
	}
}
