package router

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/Tencent/WeKnora/internal/healthcheck"
)

// This file pins the health surface as the router actually wires it, which
// is the integration point the compose healthcheck and `cli doctor` depend
// on. The behaviour of the inspection itself is covered in the healthcheck
// package; what matters here is that the routes exist, that /health is
// untouched by findings, and that the degraded signal is reachable.

func routerHealthTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.Exec(`
		CREATE TABLE models (id TEXT PRIMARY KEY, name TEXT, type TEXT, is_default BOOLEAN DEFAULT 0, deleted_at DATETIME);
		CREATE TABLE custom_agents (id TEXT, name TEXT, tenant_id INTEGER, config TEXT, deleted_at DATETIME);
		CREATE TABLE tenants (id INTEGER PRIMARY KEY, memory_config TEXT, deleted_at DATETIME);
		CREATE TABLE sessions (id TEXT PRIMARY KEY, rerank_model_id TEXT, summary_model_id TEXT, deleted_at DATETIME);
		CREATE TABLE task_dead_letters (id INTEGER PRIMARY KEY, task_type TEXT, last_error TEXT, failed_at DATETIME);
		CREATE TABLE memory_extraction_sessions (tenant_id INTEGER, subject_id TEXT, session_id TEXT,
			pending BOOLEAN, failure_count INTEGER, failure_code TEXT, updated_at DATETIME);
	`).Error)
	return db
}

func getRoute(t *testing.T, r *gin.Engine, path string) (int, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
	var body map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	return w.Code, body
}

// TestHealthSurfaceLivenessStaysGreenWithFindings is criterion 7 at the
// router: with a real dangling reference present, liveness must still answer
// 200 and the degraded signal must be a separate route.
//
// If these two were merged — or if /health reflected findings — the compose
// healthcheck would restart the container over a stale model id, and the
// restart would not fix the stale model id.
func TestHealthSurfaceLivenessStaysGreenWithFindings(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db := routerHealthTestDB(t)
	require.NoError(t, db.Exec(
		"INSERT INTO models (id, name, type, deleted_at) VALUES ('builtin-llm-default','qwen','LLM', ?)",
		time.Date(2026, 9, 25, 8, 3, 10, 0, time.UTC),
	).Error)
	require.NoError(t, db.Exec(
		"INSERT INTO custom_agents (id, name, tenant_id, config) VALUES (?, ?, 1, ?)",
		"builtin-smart-reasoning", "Smart Reasoning", `{"model_id":"builtin-llm-default"}`,
	).Error)

	insp := healthcheck.New(healthcheck.Config{DB: db, LogFindings: false})
	insp.Run(context.Background())

	r := NewRouter(RouterParams{HealthInspector: insp, SystemHandler: &handler.SystemHandler{}})

	// Liveness: unchanged, always 200, findings or not.
	code, body := getRoute(t, r, "/health")
	assert.Equal(t, http.StatusOK, code, "liveness must not be affected by findings")
	assert.Equal(t, "ok", body["status"])

	// Readiness must not be reachable anonymously.
	//
	// This assertion used to be `StatusServiceUnavailable` with the degraded
	// body — i.e. it asserted the *leak* as if it were the feature, which is how
	// the endpoint stayed public: the test pinned the wrong behaviour, so
	// nothing failed when the route was registered outside the Auth chain.
	code, body = getRoute(t, r, "/health/readiness")
	assert.Equal(t, http.StatusUnauthorized, code,
		"readiness must not be servable anonymously: its findings name cross-tenant "+
			"objects and ids and embed raw error text")
	assert.NotContains(t, body, "findings",
		"an unauthorized response must not carry the findings payload")

	// The degraded payload itself is still covered — invoked directly, because
	// reaching it through the router now requires credentials. Dropping this
	// would trade a security hole for a coverage hole.
	rec := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(rec)
	ctx.Request = httptest.NewRequest(http.MethodGet, "/health/readiness", nil)
	insp.ReadinessHandler(ctx)
	assert.Equal(t, http.StatusServiceUnavailable, rec.Code)
	var readiness map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &readiness))
	assert.Equal(t, "degraded", readiness["status"])
	assert.Equal(t, false, readiness["ready"])
	assert.Equal(t, "GET /health is unaffected by findings and must stay the container healthcheck",
		readiness["liveness"], "the payload must say which endpoint is safe to probe")
}

// TestHealthSurfaceWithoutInspector keeps the liveness path independent of
// the checker. The checker is a new dependency on the server's startup path,
// and a nil inspector must degrade to "the route is not registered" rather
// than taking /health down with it.
func TestHealthSurfaceWithoutInspector(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := NewRouter(RouterParams{SystemHandler: &handler.SystemHandler{}})

	code, body := getRoute(t, r, "/health")
	assert.Equal(t, http.StatusOK, code, "liveness must work with no inspector wired")
	assert.Equal(t, "ok", body["status"])

	// With no inspector the readiness route is never registered, so there is no
	// readiness surface at all.
	//
	// This used to assert 404. That no longer holds: the global Auth middleware
	// runs before Gin's NoRoute handler, and /health/readiness is deliberately
	// off the no-auth allowlist, so an anonymous caller now gets 401 for a path
	// that does not exist. Rather than restate whichever code the router happens
	// to answer first, pin the intent against a path that certainly does not
	// exist — and assert the concrete code too, so a future change to the auth
	// order is visible here instead of silently redefining "absent".
	unknownCode, _ := getRoute(t, r, "/health/no-such-route")
	code, _ = getRoute(t, r, "/health/readiness")
	assert.Equal(t, unknownCode, code,
		"with no inspector there is no readiness surface: it must look exactly like an unknown path")
	assert.Equal(t, http.StatusUnauthorized, code,
		"the readiness route is unregistered here; 401 comes from the global Auth middleware")
}
