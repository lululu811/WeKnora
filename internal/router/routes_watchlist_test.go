package router

import (
	"testing"

	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/handler"
)

// TestStockWatchRoutesAreRegistered pins the watchlist surface, including the
// condition routes.
//
// Why this is worth a test: Gin resolves the radix tree at registration time
// and a static-vs-wildcard conflict is a startup panic, not a 404. The
// condition routes put a parameterised GET (/watchlist/:thscode/conditions)
// next to the static GET /watchlist/events, which is exactly the shape that
// panics in older routers — so "the app boots and these paths exist" is a real
// invariant, not a tautology. A missing route here is a frontend page hitting
// 404 in production.
func TestStockWatchRoutesAreRegistered(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	v1 := engine.Group("/api/v1")
	RegisterStockWatchRoutes(v1, handler.NewStockWatchHandler(nil, nil), &rbacGuards{})

	routes := map[string]bool{}
	for _, r := range engine.Routes() {
		routes[r.Method+" "+r.Path] = true
	}
	for _, want := range []string{
		"GET /api/v1/watchlist",
		"POST /api/v1/watchlist",
		"GET /api/v1/watchlist/events",
		"PUT /api/v1/watchlist/:thscode",
		"DELETE /api/v1/watchlist/:thscode",
		"GET /api/v1/watchlist/:thscode/conditions",
		"POST /api/v1/watchlist/:thscode/conditions",
		"DELETE /api/v1/watchlist/:thscode/conditions/:id",
	} {
		if !routes[want] {
			t.Errorf("route %s not registered", want)
		}
	}
}
