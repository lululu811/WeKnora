package handler_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/Tencent/WeKnora/internal/middleware"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 这是一次真实的端到端验证：真的建库、真的跑迁移、真的过 HTTP handler、
// 真的写库。不用 mock，因为本功能最可能出错的地方正是「迁移与 GORM 字段
// 对不上」「事件与行不在同一事务」「日记的唯一键没生效」——这三样 mock
// 一个都测不出来。
//
// 它同时是那条「日记必须落进 stock_watches 而非条件表」规则的回归：
// 下面 Case 1 的池子里**一只票都没有设条件**，所以任何复用 ListScopes 的
// 实现都会在这里返回空列表，测试立刻失败。

const liveUser = "diary-user"
const liveTenant = uint64(4242)

// liveWatchlistDDL 拼出 watchlist 家族在 **sqlite** 目录下的全部 up 迁移。
//
// 与 repository 包里的 watchlistTestDDL 同一个做法（通配符而非硬编码文件名），
// 理由相同：抄一份 DDL 会在某次改迁移时静默失联——测试继续绿，生产表却已经
// 和 GORM 字段对不上了。那份在 repository 的测试包里（package
// repository_test），这里够不着，所以按同一约定重写一份而不是导出它。
func liveWatchlistDDL(t *testing.T) string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join("..", "..", "migrations", "sqlite", "*stock_watch*.up.sql"))
	require.NoError(t, err)
	require.NotEmpty(t, paths, "watchlist 家族的迁移文件必须存在")
	sort.Strings(paths)
	var ddl strings.Builder
	for _, p := range paths {
		raw, err := os.ReadFile(p)
		require.NoError(t, err, "迁移文件必须存在：%s", p)
		ddl.Write(raw)
		ddl.WriteString("\n")
	}
	return ddl.String()
}

func liveDiaryEngine(t *testing.T) (*gin.Engine, *gorm.DB) {
	t.Helper()
	gin.SetMode(gin.TestMode)

	// 跑真正的迁移文件，不抄 DDL。
	ddl := liveWatchlistDDL(t)
	// 断言日记表的迁移确实被拼进来了，免得 glob 规则将来变了而测试无声通过。
	require.Contains(t, ddl, "stock_watch_diaries", "日记表迁移必须被拼进测试 DDL")

	db, err := gorm.Open(
		sqlite.Open("file:diary-live-"+t.Name()+"?mode=memory&cache=shared"),
		&gorm.Config{},
	)
	require.NoError(t, err)
	require.NoError(t, db.Exec(ddl).Error)

	watchRepo := repository.NewStockWatchRepository(db)
	pool := service.NewStockWatchService(watchRepo, repository.NewStockWatchEventsRepository(db))
	diaryRepo := repository.NewStockWatchDiaryRepository(db)
	diarySvc := service.NewStockWatchDiaryService(diaryRepo, watchRepo, nil)

	watchHandler := handler.NewStockWatchHandler(pool, nil)
	diaryHandler := handler.NewStockWatchDiaryHandler(diarySvc, pool)

	engine := gin.New()
	// ErrorHandler 必须挂上：handler 一律用 c.Error(...) 报错再 return，
	// 由这个中间件把错误渲染成响应体。少了它，任何 4xx 都会变成"200 + 空 body"
	// ——那不是 handler 的 bug，而是测试引擎少了一环，看起来却极像 bug。
	engine.Use(middleware.ErrorHandler())
	v1 := engine.Group("/api/v1")
	// 鉴权由中间件注入，这两个键与 middleware 写入的键名一致。
	authed := func(h gin.HandlerFunc) gin.HandlerFunc {
		return func(c *gin.Context) {
			c.Set(types.UserIDContextKey.String(), liveUser)
			c.Set(types.TenantIDContextKey.String(), liveTenant)
			h(c)
		}
	}
	v1.POST("/watchlist", authed(watchHandler.AddStockWatch))
	v1.GET("/watchlist/:thscode/diaries", authed(diaryHandler.ListStockWatchDiaries))
	v1.POST("/watchlist/:thscode/diaries/accept", authed(diaryHandler.AcceptStockWatchDiary))
	v1.POST("/watchlist/:thscode/diaries/ignore", authed(diaryHandler.IgnoreStockWatchDiary))
	v1.GET("/watchlist/events", authed(watchHandler.ListStockWatchEvents))
	return engine, db
}

func doJSON(t *testing.T, engine *gin.Engine, method, path, body string) (int, map[string]any) {
	t.Helper()
	var reader *strings.Reader
	if body == "" {
		reader = strings.NewReader("")
	} else {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	engine.ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec.Code, out
}

// Case 1：入池带理由 → 同一事务里落库 → 理由也进了 added 事件快照。
func TestLivePoolEntryCarriesReason(t *testing.T) {
	engine, db := liveDiaryEngine(t)

	const reason = "订单能见度到明年一季度，估值回落到合理区间"
	code, body := doJSON(t, engine, http.MethodPost, "/api/v1/watchlist",
		`{"thscode":"601127.SH","name":"赛力斯","exchange":"SH","note":"`+reason+`"}`)
	require.Equal(t, http.StatusOK, code, "入池应成功：%v", body)
	require.Equal(t, true, body["created"])

	var row types.StockWatch
	require.NoError(t, db.Where("thscode = ?", "601127.SH").First(&row).Error)
	require.Equal(t, reason, row.Note, "理由必须随入池一起落库")

	// 事件快照里也必须有同一句——这是「它当初为什么进池」唯一可追溯的记录。
	var ev types.StockWatchEvent
	require.NoError(t, db.Where("kind = ?", types.StockWatchEventAdded).First(&ev).Error)
	require.Equal(t, reason, ev.Note, "added 事件必须带上理由快照")

	// 超长理由被拒，而不是截断。
	long := strings.Repeat("备", types.MaxStockWatchNoteLen+1)
	code, _ = doJSON(t, engine, http.MethodPost, "/api/v1/watchlist",
		`{"thscode":"600519.SH","note":"`+long+`"}`)
	require.Equal(t, http.StatusBadRequest, code, "超过上限的理由必须 400")
}

// Case 2：池子里没有设任何条件，日记写入仍然覆盖到这只票。
// 这是 Q16 的核心回归——复用 ListScopes 的实现会在这里返回空。
func TestLiveDiaryCoversSymbolsWithoutConditions(t *testing.T) {
	engine, db := liveDiaryEngine(t)

	// 三只票，观察 / 持仓 / 已放弃各一。
	for _, spec := range []struct{ code, note string }{
		{"601127.SH", "关注交付数据"},
		{"000002.SZ", "底仓"},
		{"688185.SH", "已放弃的票"},
	} {
		code, body := doJSON(t, engine, http.MethodPost, "/api/v1/watchlist",
			fmt.Sprintf(`{"thscode":%q,"note":%q}`, spec.code, spec.note))
		require.Equal(t, http.StatusOK, code, "%v", body)
	}
	// 把第二只推进到 holding，第三只丢进 dropped。
	require.NoError(t, db.Model(&types.StockWatch{}).
		Where("thscode = ?", "000002.SZ").
		Update("state", types.StockWatchStateHolding).Error)
	require.NoError(t, db.Model(&types.StockWatch{}).
		Where("thscode = ?", "688185.SH").
		Update("state", types.StockWatchStateDropped).Error)

	// 关键：一行条件都没建。
	var condCount int64
	require.NoError(t, db.Model(&types.StockWatchCondition{}).Count(&condCount).Error)
	require.Zero(t, condCount, "本用例的前提就是没有条件")

	// scope 必须覆盖到前两只（observing + holding），且不含 dropped。
	repo := repository.NewStockWatchDiaryRepository(db)
	scopes, err := repo.ListDiaryScopes(context.Background())
	require.NoError(t, err)
	require.Len(t, scopes, 1, "必须有且只有一个 scope")
	watched, err := repo.ListWatchedByScope(context.Background(), scopes[0].UserID, scopes[0].TenantID)
	require.NoError(t, err)
	codes := make([]string, 0, len(watched))
	for _, w := range watched {
		codes = append(codes, w.THSCode)
	}
	require.ElementsMatch(t, []string{"000002.SZ", "601127.SH"}, codes,
		"scope 必须来自 stock_watches 的 observing+holding，而不是条件表")
}

// Case 3：日记的唯一键让同一天重跑只覆盖、不重复。
func TestLiveDiaryRerunOverwritesSameTradingDay(t *testing.T) {
	engine, db := liveDiaryEngine(t)
	_, body := doJSON(t, engine, http.MethodPost, "/api/v1/watchlist",
		`{"thscode":"601127.SH","name":"赛力斯","note":"关注交付"}`)
	require.Equal(t, true, body["created"])

	repo := repository.NewStockWatchDiaryRepository(db)
	day, err := types.ParseDateOnly("2026-10-01")
	require.NoError(t, err)

	// 直接写两篇同一天的日记：第二篇覆盖第一篇。
	for _, verdict := range []string{types.StockWatchDiaryVerdictBuy, types.StockWatchDiaryVerdictSell} {
		require.NoError(t, repo.UpsertMany(context.Background(), []*types.StockWatchDiary{{
			UserID: liveUser, TenantID: liveTenant, THSCode: "601127.SH",
			TradeDate: day, Verdict: verdict, Body: "第 " + verdict + " 篇",
		}}))
	}

	var all []*types.StockWatchDiary
	require.NoError(t, db.Where("thscode = ?", "601127.SH").Find(&all).Error)
	require.Len(t, all, 1, "同一交易日只能有一篇日记")
	require.Equal(t, types.StockWatchDiaryVerdictSell, all[0].Verdict, "后写的应覆盖先写的")
}

// Case 4：采纳与忽略真的改状态 / 落事件，且采纳受 verdict 约束。
func TestLiveAcceptAndIgnore(t *testing.T) {
	engine, db := liveDiaryEngine(t)
	_, body := doJSON(t, engine, http.MethodPost, "/api/v1/watchlist",
		`{"thscode":"601127.SH","name":"赛力斯","note":"关注交付"}`)
	require.Equal(t, true, body["created"])

	repo := repository.NewStockWatchDiaryRepository(db)
	day, err := types.ParseDateOnly("2026-10-01")
	require.NoError(t, err)
	require.NoError(t, repo.UpsertMany(context.Background(), []*types.StockWatchDiary{{
		UserID: liveUser, TenantID: liveTenant, THSCode: "601127.SH",
		TradeDate: day, Verdict: types.StockWatchDiaryVerdictBuy, Body: "理由成立",
		Reasons: "订单能见度延长",
	}}))

	// 采纳 buy → holding。
	code, resp := doJSON(t, engine, http.MethodPost, "/api/v1/watchlist/601127.SH/diaries/accept",
		`{"trade_date":"2026-10-01","to_state":"holding"}`)
	require.Equal(t, http.StatusOK, code, "采纳应成功：%v", resp)

	var row types.StockWatch
	require.NoError(t, db.Where("thscode = ?", "601127.SH").First(&row).Error)
	require.Equal(t, types.StockWatchStateHolding, row.State, "采纳 buy 后状态必须是 holding")

	// 采纳 buy 却要求 dropped —— 拒绝。这条是「accept 不能当万能改状态接口用」。
	code, _ = doJSON(t, engine, http.MethodPost, "/api/v1/watchlist/601127.SH/diaries/accept",
		`{"trade_date":"2026-10-01","to_state":"dropped"}`)
	require.Equal(t, http.StatusBadRequest, code, "verdict 与目标状态不符必须 400")

	// 采纳不存在的一天 —— 404，而不是写一条凭空的采纳事件。
	code, _ = doJSON(t, engine, http.MethodPost, "/api/v1/watchlist/601127.SH/diaries/accept",
		`{"trade_date":"2026-09-01","to_state":"holding"}`)
	require.Equal(t, http.StatusNotFound, code)

	// 忽略：状态不变，但事件落下来了。
	code, resp = doJSON(t, engine, http.MethodPost, "/api/v1/watchlist/601127.SH/diaries/ignore",
		`{"trade_date":"2026-10-01"}`)
	require.Equal(t, http.StatusOK, code, "忽略应成功：%v", resp)

	var ignored int64
	require.NoError(t, db.Model(&types.StockWatchEvent{}).
		Where("kind = ?", types.StockWatchEventVerdictIgnored).Count(&ignored).Error)
	require.EqualValues(t, 1, ignored, "忽略必须落事件，否则「AI 一直看错」无从查证")

	// 采纳的状态迁移事件里要写明依据的是哪一篇。
	var adopted types.StockWatchEvent
	require.NoError(t, db.Where("kind = ?", types.StockWatchEventStateChanged).
		Order("id DESC").First(&adopted).Error)
	require.Contains(t, adopted.Note, "2026-10-01", "采纳事件应记下所依据的交易日")
}

// Case 5：日记列表按交易日倒序返回，且受 limit 约束。
func TestLiveDiaryListOrdering(t *testing.T) {
	engine, db := liveDiaryEngine(t)
	_, _ = doJSON(t, engine, http.MethodPost, "/api/v1/watchlist", `{"thscode":"601127.SH","note":"x"}`)

	repo := repository.NewStockWatchDiaryRepository(db)
	for _, d := range []string{"2026-09-28", "2026-09-29", "2026-09-30"} {
		day, err := types.ParseDateOnly(d)
		require.NoError(t, err)
		require.NoError(t, repo.UpsertMany(context.Background(), []*types.StockWatchDiary{{
			UserID: liveUser, TenantID: liveTenant, THSCode: "601127.SH",
			TradeDate: day, Verdict: types.StockWatchDiaryVerdictHold, Body: d,
		}}))
	}

	code, body := doJSON(t, engine, http.MethodGet, "/api/v1/watchlist/601127.SH/diaries", "")
	require.Equal(t, http.StatusOK, code)
	rows, _ := body["data"].([]any)
	require.Len(t, rows, 3)
	first, _ := rows[0].(map[string]any)
	require.Equal(t, "2026-09-30", first["trade_date"], "最新的交易日在前")

	code, body = doJSON(t, engine, http.MethodGet, "/api/v1/watchlist/601127.SH/diaries?limit=2", "")
	require.Equal(t, http.StatusOK, code)
	rows, _ = body["data"].([]any)
	require.Len(t, rows, 2, "limit 生效")
}
