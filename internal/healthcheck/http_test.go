package healthcheck

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

// serveReadiness mounts only the readiness route, the way the router does.
func serveReadiness(t *testing.T, insp *Inspector) *gin.Engine {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/health/readiness", insp.ReadinessHandler)
	return r
}

func getJSON(t *testing.T, r *gin.Engine, path string) (int, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
	var body map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &body), "body: %s", w.Body.String())
	return w.Code, body
}

// TestReadinessIsSeparateFromLiveness is acceptance criterion 3.
//
// Liveness must stay green while defects are present: the compose
// healthcheck and `cli doctor` probe /health, and a restart triggered by a
// dangling model id would not fix the dangling model id. The degraded signal
// has to be somewhere else.
func TestReadinessIsSeparateFromLiveness(t *testing.T) {
	db := newTestDB(t)
	// A real defect, the exact shape from the incident.
	seedModel(t, db, "builtin-llm-default", "qwen3.7-plus", "LLM",
		time.Date(2026, 9, 25, 8, 3, 10, 0, time.UTC))
	seedAgent(t, db, "builtin-smart-reasoning", "Smart Reasoning",
		`{"model_id":"builtin-llm-default"}`)

	insp := New(Config{DB: db, MemoryService: &stubMemory{}, LogFindings: false})
	insp.Run(context.Background())

	// Liveness: unchanged, static 200, ignores findings entirely. This
	// mirrors the handler the router still registers.
	live := gin.New()
	live.GET("/health", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok"}) })
	code, body := getJSON(t, live, "/health")
	assert.Equal(t, http.StatusOK, code, "liveness must stay green with findings present")
	assert.Equal(t, "ok", body["status"])

	// Readiness: degraded, and it says what is wrong.
	r := serveReadiness(t, insp)
	code, body = getJSON(t, r, "/health/readiness")
	assert.Equal(t, http.StatusServiceUnavailable, code)
	assert.Equal(t, "degraded", body["status"])
	assert.Equal(t, false, body["ready"])

	findings, ok := body["findings"].([]any)
	require.True(t, ok)
	require.NotEmpty(t, findings)
	first, _ := findings[0].(map[string]any)
	assert.Contains(t, first["object"], "Smart Reasoning",
		"the degraded payload must name the object")
	assert.Equal(t, "builtin-llm-default", first["bad_value"],
		"the degraded payload must name the bad value")
}

// seedFullyReachingAgent gives an agent an allowlist covering every
// registered tool, so check 5 has nothing to report.
//
// Without it, a test database with one agent produces a real "N registered
// tools are in no agent's allowed_tools" finding, and the test would be
// asserting about a degraded pass while believing it was asserting about a
// clean one.
func seedFullyReachingAgent(t *testing.T, db *gorm.DB) {
	t.Helper()
	names := registeredToolNames()
	allow := make([]string, 0, len(names))
	for name := range names {
		allow = append(allow, name)
	}
	sort.Strings(allow)
	blob, err := json.Marshal(map[string]any{"model_id": "", "allowed_tools": allow})
	require.NoError(t, err)
	seedAgent(t, db, "covering", "Fully Reaching Agent", string(blob))
}

// TestReadinessDistinguishesNeverRun covers criterion 5 at the HTTP surface:
// a client must be able to tell "has not run" from "ran, found nothing".
func TestReadinessDistinguishesNeverRun(t *testing.T) {
	r := serveReadiness(t, New(Config{DB: newTestDB(t)}))

	// Never ran.
	code, body := getJSON(t, r, "/health/readiness")
	assert.Equal(t, http.StatusServiceUnavailable, code)
	assert.Equal(t, "unknown", body["status"])
	assert.Contains(t, body["detail"], "has not completed a pass")
	assert.ElementsMatch(t, AllChecks(), body["checks"],
		"the payload must say which checks owe an answer")
	assert.NotContains(t, body, "started_at",
		"a pass that has not run has no start time to report")

	// Ran, and found nothing.
	clean := newTestDB(t)
	seedFullyReachingAgent(t, clean)
	insp := New(Config{DB: clean, MemoryService: &stubMemory{}, LogFindings: false})
	insp.Run(context.Background())

	r = serveReadiness(t, insp)
	code, body = getJSON(t, r, "/health/readiness")
	require.Equal(t, http.StatusOK, code)
	assert.Equal(t, "ok", body["status"])
	assert.Equal(t, true, body["ready"])
	assert.Equal(t, false, body["incomplete"])
	assert.Contains(t, body, "started_at",
		"a completed pass is timestamped; that is what distinguishes it from never-run")
}

// TestReadinessReportsPartialPass makes sure a pass that could not run every
// check is never dressed up as healthy.
func TestReadinessReportsPartialPass(t *testing.T) {
	db := newTestDB(t)
	seedFullyReachingAgent(t, db)
	// Now the only problem is a check that cannot run.
	require.NoError(t, db.Exec("DROP TABLE task_dead_letters").Error)
	insp := New(Config{DB: db, MemoryService: &stubMemory{}, LogFindings: false})
	insp.Run(context.Background())

	r := serveReadiness(t, insp)
	code, body := getJSON(t, r, "/health/readiness")
	assert.Equal(t, http.StatusServiceUnavailable, code)
	assert.Equal(t, "unknown", body["status"])
	assert.Equal(t, true, body["incomplete"])

	errs, ok := body["check_errors"].(map[string]any)
	require.True(t, ok, "the payload must name the checks that did not run")
	assert.Contains(t, errs, CheckDeadLetterBacklog)
}
