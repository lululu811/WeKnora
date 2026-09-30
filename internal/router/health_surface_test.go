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

	// Readiness: the degraded signal, on its own route.
	code, body = getRoute(t, r, "/health/readiness")
	assert.Equal(t, http.StatusServiceUnavailable, code)
	assert.Equal(t, "degraded", body["status"])
	assert.Equal(t, false, body["ready"])
	assert.Equal(t, "GET /health is unaffected by findings and must stay the container healthcheck",
		body["liveness"], "the payload must say which endpoint is safe to probe")
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

	code, _ = getRoute(t, r, "/health/readiness")
	assert.Equal(t, http.StatusNotFound, code,
		"with no inspector there is no readiness signal to serve; liveness is unaffected")
}
