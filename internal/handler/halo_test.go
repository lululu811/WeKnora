package handler

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/agent/tools/halo"
	"github.com/Tencent/WeKnora/internal/middleware"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// fakeKnowledgeService 只实现本文件用到的方法。嵌入接口让其余方法在未被调用时
// 保持 nil —— 一旦测试路径意外走到别的方法，会立刻 panic 而不是静默通过。
type fakeKnowledgeService struct {
	interfaces.KnowledgeService
	items []*types.Knowledge
	err   error
}

func (f *fakeKnowledgeService) ListKnowledgeByKnowledgeBaseID(
	_ context.Context, _ string,
) ([]*types.Knowledge, error) {
	return f.items, f.err
}

func metaOf(t *testing.T, kv map[string]any) types.JSON {
	t.Helper()
	raw, err := json.Marshal(kv)
	require.NoError(t, err)
	return types.JSON(raw)
}

// ---------------------------------------------------------------------------
// 标题：确定性是幂等的兜底判据
// ---------------------------------------------------------------------------

func TestHaloArchiveTitleIsDeterministic(t *testing.T) {
	got := haloArchiveTitle("600519.SH", "2025-12-31")
	assert.Equal(t, "HALO 600519.SH 2025-12-31", got)
	// 同一输入必须给出同一标题：人一眼能认出是哪只票哪一期，也是元数据写入
	// 失败时唯一还能认出重复的判据。
	assert.Equal(t, got, haloArchiveTitle("600519.SH", "2025-12-31"))
}

func TestHaloArchiveTitleWithoutPeriod(t *testing.T) {
	assert.Equal(t, "HALO 600519.SH", haloArchiveTitle("600519.SH", ""))
}

// ---------------------------------------------------------------------------
// 元数据：必须满足 UpdateKnowledge 的硬约束
//
// knowledgeService.UpdateKnowledge 会拒绝：超过 20 个字段、键长 > 64、
// 字符串值 > 1000、以及任何非 string/number/bool/null 的值。血缘是**另一次
// 写入**，违反约束时整个 attachLineage 会失败 —— 而失败只记日志、不阻断归档，
// 于是症状是「归档成功但下次幂等匹配找不到它，重复归档出第二份」。
// 所以这里把约束钉死，而不是等它变成重复文档。
// ---------------------------------------------------------------------------

func TestHaloArchiveMetadataSatisfiesUpdateConstraints(t *testing.T) {
	meta := haloArchiveMetadata("600519.SH", "2025-12-31", "annual", "mixed")

	require.LessOrEqual(t, len(meta), 20, "UpdateKnowledge 最多接受 20 个字段")
	for key, value := range meta {
		assert.LessOrEqual(t, len(key), 64, "键长上限 64：%s", key)
		assert.NotEmpty(t, key)
		switch v := value.(type) {
		case string:
			assert.LessOrEqual(t, len(v), 1000, "字符串值上限 1000：%s", key)
		case float64, bool, nil:
		default:
			// 嵌套对象/数组会被 UpdateKnowledge 直接拒绝。
			t.Fatalf("字段 %s 的类型 %T 不被 UpdateKnowledge 接受（只允许 string/number/bool/null）", key, value)
		}
	}
}

func TestHaloArchiveMetadataDeclaresLLMScoredDims(t *testing.T) {
	meta := haloArchiveMetadata("600519.SH", "2025-12-31", "annual", "")
	// 不声明这一条，检索到该文档的模型读到「护城河 7 分」时无法知道它是算出来的
	// 还是判出来的。7 个定性维度都是模型给分，必须显式写出来。
	assert.Equal(t, haloLLMScoredDims, meta[haloMetaLLMDims])
	for _, dim := range []string{"moat", "stag", "esg", "management", "shareholder", "valuation", "risk"} {
		assert.Contains(t, haloLLMScoredDims, dim)
	}
}

func TestHaloArchiveMetadataCarriesIdentityAndCaliber(t *testing.T) {
	meta := haloArchiveMetadata("600519.SH", "2025-12-31", "annual", "mixed")
	assert.Equal(t, "600519.SH", meta[haloMetaThscode])
	assert.Equal(t, "2025-12-31", meta[haloMetaPeriod])
	assert.Equal(t, "annual", meta[haloMetaReport])
	assert.Equal(t, "mixed", meta[haloMetaAssetType])
	assert.NotEmpty(t, meta[haloMetaGenerated])
	assert.Contains(t, meta[haloMetaCaliber], "不要与其他口径混用")
}

func TestHaloArchiveMetadataOmitsUnknownAssetType(t *testing.T) {
	// 资产类型未知时宁可不写，也不要写个空串 —— 空串会被读成「已判定为无类型」。
	meta := haloArchiveMetadata("600519.SH", "2025-12-31", "annual", "")
	_, exists := meta[haloMetaAssetType]
	assert.False(t, exists)
}

// ---------------------------------------------------------------------------
// 幂等匹配
// ---------------------------------------------------------------------------

func TestFindArchivedReportMatchesOnIdentity(t *testing.T) {
	svc := &fakeKnowledgeService{items: []*types.Knowledge{
		{ID: "other", CustomMetadata: metaOf(t, map[string]any{
			haloMetaThscode: "000001.SZ", haloMetaPeriod: "2025-12-31",
		})},
		{ID: "hit", CustomMetadata: metaOf(t, map[string]any{
			haloMetaThscode: "600519.SH", haloMetaPeriod: "2025-12-31",
		})},
	}}
	h := &HaloHandler{knowledgeService: svc}

	got, err := h.findArchivedReport(context.Background(), "kb-1", "600519.SH", "2025-12-31")
	require.NoError(t, err)
	require.NotNil(t, got)
	assert.Equal(t, "hit", got.ID)
}

func TestFindArchivedReportRequiresBothThscodeAndPeriod(t *testing.T) {
	// 同一只票的不同报告期是两份不同的报告，不能互相顶掉。
	svc := &fakeKnowledgeService{items: []*types.Knowledge{
		{ID: "annual", CustomMetadata: metaOf(t, map[string]any{
			haloMetaThscode: "600519.SH", haloMetaPeriod: "2025-12-31",
		})},
	}}
	h := &HaloHandler{knowledgeService: svc}

	got, err := h.findArchivedReport(context.Background(), "kb-1", "600519.SH", "2024-12-31")
	require.NoError(t, err)
	assert.Nil(t, got, "报告期不同必须视为未归档，否则会把上一期的报告覆盖掉")
}

func TestFindArchivedReportToleratesForeignMetadata(t *testing.T) {
	// 同一 KB 里其它来源的文档带着各种自定义元数据。它们不该让归档失败，
	// 也不该被误判成已归档的 HALO 报告。
	svc := &fakeKnowledgeService{items: []*types.Knowledge{
		{ID: "not-json-object", CustomMetadata: types.JSON(`["a","b"]`)},
		{ID: "broken", CustomMetadata: types.JSON(`{`)},
		{ID: "empty"},
		{ID: "unrelated", CustomMetadata: metaOf(t, map[string]any{"owner": "someone"})},
	}}
	h := &HaloHandler{knowledgeService: svc}

	got, err := h.findArchivedReport(context.Background(), "kb-1", "600519.SH", "2025-12-31")
	require.NoError(t, err)
	assert.Nil(t, got)
}

func TestFindArchivedReportSkipsNilEntries(t *testing.T) {
	svc := &fakeKnowledgeService{items: []*types.Knowledge{nil}}
	h := &HaloHandler{knowledgeService: svc}

	got, err := h.findArchivedReport(context.Background(), "kb-1", "600519.SH", "2025-12-31")
	require.NoError(t, err)
	assert.Nil(t, got)
}

// ---------------------------------------------------------------------------
// handler 级集成：真的走一遍 gin → 权限校验 → 归档动作
//
// 这一层要验的是「接线是否正确」，纯函数测试覆盖不到：
// 权限校验是否生效、Create 与 Update 是否按幂等分支走、血缘是否真的写了、
// 以及发往 python-service 的请求里两个 include_* 标志是否如设计。
// ---------------------------------------------------------------------------

type fakeKBService struct {
	interfaces.KnowledgeBaseService
	kb *types.KnowledgeBase
}

func (f *fakeKBService) GetKnowledgeBaseByID(
	_ context.Context, _ string,
) (*types.KnowledgeBase, error) {
	return f.kb, nil
}

type recordingKnowledgeService struct {
	interfaces.KnowledgeService
	list     []*types.Knowledge
	created  []*types.ManualKnowledgePayload
	updated  []*types.ManualKnowledgePayload
	lineages []map[string]any
}

func (r *recordingKnowledgeService) ListKnowledgeByKnowledgeBaseID(
	_ context.Context, _ string,
) ([]*types.Knowledge, error) {
	return r.list, nil
}

func (r *recordingKnowledgeService) CreateKnowledgeFromManual(
	_ context.Context, kbID string, payload *types.ManualKnowledgePayload, _ string,
) (*types.Knowledge, error) {
	r.created = append(r.created, payload)
	return &types.Knowledge{ID: "new-doc", KnowledgeBaseID: kbID}, nil
}

func (r *recordingKnowledgeService) UpdateManualKnowledge(
	_ context.Context, knowledgeID string, payload *types.ManualKnowledgePayload,
) (*types.Knowledge, error) {
	r.updated = append(r.updated, payload)
	return &types.Knowledge{ID: knowledgeID}, nil
}

func (r *recordingKnowledgeService) UpdateKnowledge(
	_ context.Context, k *types.Knowledge,
) error {
	var meta map[string]any
	if err := json.Unmarshal(k.CustomMetadata, &meta); err != nil {
		return err
	}
	r.lineages = append(r.lineages, meta)
	return nil
}

// haloStub 假扮 python-service 的 /halo/score，并记录收到的请求体。
type haloStub struct {
	body   map[string]any
	got    map[string]any
	server *httptest.Server
}

func newHaloStub(t *testing.T, body map[string]any) *haloStub {
	t.Helper()
	s := &haloStub{body: body}
	s.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &s.got)
		assert.Equal(t, "/halo/score", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.body)
	}))
	t.Cleanup(s.server.Close)
	return s
}

func archiveRouter(ks interfaces.KnowledgeService, client *halo.HTTPClient) *gin.Engine {
	gin.SetMode(gin.TestMode)
	h := NewHaloHandler(ks, &fakeKBService{kb: &types.KnowledgeBase{
		ID: "307ea0c1-690b-46f3-af2c-f350e6b122ca", TenantID: 10000,
	}}, nil, nil, client)
	r := gin.New()
	// ErrorHandler 必须挂：handler 走的是 c.Error(...) 上报错误，没有这个中间件
	// 时 c.Error 只记录不写响应，错误路径会静默返回 200 空体 —— 那样测试就查不出
	// 「该拒绝的请求被拒了没有」。
	r.Use(middleware.ErrorHandler())
	// 复刻 auth 中间件的效果：KBAccessRequest 的兼容路径读的就是这个 gin 键。
	r.Use(func(c *gin.Context) {
		c.Set(types.TenantIDContextKey.String(), uint64(10000))
		c.Set(types.UserIDContextKey.String(), "admin")
		c.Next()
	})
	r.POST("/halo/archive", h.ArchiveHaloReport)
	return r
}

func doArchive(t *testing.T, r *gin.Engine, body string) (int, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/halo/archive", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return w.Code, out
}

const archiveBody = `{"knowledge_base_id":"307ea0c1-690b-46f3-af2c-f350e6b122ca","thscode":"600519"}`

func TestArchiveCreatesDraftKnowledgeWithLineage(t *testing.T) {
	stub := newHaloStub(t, map[string]any{
		"ok": true, "thscode": "600519.SH", "period": "2025-12-31",
		"asset_type": "mixed", "markdown": "# HALO 600519.SH\n\n正文",
	})
	ks := &recordingKnowledgeService{}
	code, out := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)), archiveBody)

	require.Equal(t, http.StatusOK, code)
	assert.Equal(t, "created", out["data"].(map[string]any)["action"])

	require.Len(t, ks.created, 1)
	assert.Equal(t, "# HALO 600519.SH\n\n正文", ks.created[0].Content)
	assert.Equal(t, types.ManualKnowledgeStatusDraft, ks.created[0].Status,
		"默认必须落草稿：报告里约三成是 AI 判断且自带 30 天有效期")
	assert.Equal(t, "HALO 600519.SH 2025-12-31", ks.created[0].Title)
	assert.Empty(t, ks.updated)

	require.Len(t, ks.lineages, 1, "血缘必须写进去，否则下次幂等匹配找不到它")
	assert.Equal(t, "600519.SH", ks.lineages[0][haloMetaThscode])
	assert.Equal(t, "2025-12-31", ks.lineages[0][haloMetaPeriod])
	assert.Equal(t, haloLLMScoredDims, ks.lineages[0][haloMetaLLMDims])
}

func TestArchiveUpdatesInPlaceWhenAlreadyArchived(t *testing.T) {
	stub := newHaloStub(t, map[string]any{
		"ok": true, "thscode": "600519.SH", "period": "2025-12-31",
		"markdown": "# 新正文",
	})
	ks := &recordingKnowledgeService{list: []*types.Knowledge{{
		ID: "existing-doc",
		CustomMetadata: metaOf(t, map[string]any{
			haloMetaThscode: "600519.SH", haloMetaPeriod: "2025-12-31",
		}),
	}}}
	code, out := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)), archiveBody)

	require.Equal(t, http.StatusOK, code)
	assert.Equal(t, "updated", out["data"].(map[string]any)["action"])
	assert.Empty(t, ks.created, "同一 (标的, 报告期) 不得产生第二份")
	require.Len(t, ks.updated, 1)
	assert.Equal(t, "# 新正文", ks.updated[0].Content)
	assert.Equal(t, "existing-doc", out["data"].(map[string]any)["knowledge_id"],
		"原地更新必须保留 knowledge ID，否则已有引用会断")
}

func TestArchiveRefusesWhenNoFilingFacts(t *testing.T) {
	stub := newHaloStub(t, map[string]any{
		"ok": false, "thscode": "600519.SH",
		"reason": "600519.SH 没有已落库的年报事实。先调用 halo.filing.sync。",
	})
	ks := &recordingKnowledgeService{}
	code, out := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)), archiveBody)

	assert.Equal(t, http.StatusBadRequest, code)
	// 错误体由 middleware.ErrorHandler 统一成
	// {"error": {"code", "message", "details"}, "success": false} —— message 嵌在
	// error 对象里，不在顶层。
	errObj, ok := out["error"].(map[string]any)
	require.True(t, ok, `错误体应形如 {"error": {...}}，实际: %v`, out)
	assert.Contains(t, errObj["message"], "没有已落库的年报事实")
	assert.Empty(t, ks.created, "空骨架不入库：那会变成一条会骗人的文档")
	assert.Empty(t, ks.lineages)
}

func TestArchiveRejectsEmptyThscode(t *testing.T) {
	stub := newHaloStub(t, map[string]any{"ok": true})
	ks := &recordingKnowledgeService{}
	code, _ := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)),
		`{"knowledge_base_id":"kb-1","thscode":"  "}`)
	assert.Equal(t, http.StatusBadRequest, code)
	assert.Empty(t, ks.created)
}

func TestArchiveRefusesMarkdownlessResult(t *testing.T) {
	// ok=true 但没有 markdown：同样不能落一条空文档。
	stub := newHaloStub(t, map[string]any{"ok": true, "thscode": "600519.SH"})
	ks := &recordingKnowledgeService{}
	code, _ := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)), archiveBody)
	assert.Equal(t, http.StatusBadRequest, code)
	assert.Empty(t, ks.created)
}

func TestArchiveRequestsAnnouncementsButNotExternal(t *testing.T) {
	// 两个 include_* 标志是这次设计的核心取舍，必须钉住：
	// 公告要（报告里需要，且只多一次巨潮请求）；external 不要（会打东财易封子域）。
	stub := newHaloStub(t, map[string]any{
		"ok": true, "thscode": "600519.SH", "period": "2025-12-31", "markdown": "x",
	})
	ks := &recordingKnowledgeService{}
	doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)), archiveBody)

	assert.Equal(t, true, stub.got["include_announcements"])
	assert.Equal(t, false, stub.got["include_external"])
	assert.Equal(t, "600519", stub.got["thscode"])
	assert.Equal(t, "annual", stub.got["report_type"], "未指定时必须补默认值")
}

func TestArchivePublishFlag(t *testing.T) {
	stub := newHaloStub(t, map[string]any{
		"ok": true, "thscode": "600519.SH", "period": "2025-12-31", "markdown": "x",
	})
	ks := &recordingKnowledgeService{}
	code, _ := doArchive(t, archiveRouter(ks, halo.NewHTTPClient(stub.server.URL)),
		`{"knowledge_base_id":"kb-1","thscode":"600519","publish":true}`)
	require.Equal(t, http.StatusOK, code)
	require.Len(t, ks.created, 1)
	assert.Equal(t, types.ManualKnowledgeStatusPublish, ks.created[0].Status)
}

// ---------------------------------------------------------------------------
// 预览端点：与归档的区别是「没数据不算错误」
// ---------------------------------------------------------------------------

func reportRouter(ks interfaces.KnowledgeService, client *halo.HTTPClient) *gin.Engine {
	gin.SetMode(gin.TestMode)
	h := NewHaloHandler(ks, &fakeKBService{}, nil, nil, client)
	r := gin.New()
	r.Use(middleware.ErrorHandler())
	r.Use(func(c *gin.Context) {
		c.Set(types.TenantIDContextKey.String(), uint64(10000))
		c.Next()
	})
	r.POST("/halo/report", h.ReportHaloReport)
	return r
}

func doReport(t *testing.T, r *gin.Engine, body string) (int, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/halo/report", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return w.Code, out
}

func TestReportReturnsPayloadForPanel(t *testing.T) {
	stub := newHaloStub(t, map[string]any{
		"ok": true, "thscode": "600519.SH", "period": "2025-12-31",
		"asset_type": "mixed", "markdown": "# HALO 600519.SH\n\n正文",
		"ai_slots": []any{},
	})
	ks := &recordingKnowledgeService{}
	code, out := doReport(t, reportRouter(ks, halo.NewHTTPClient(stub.server.URL)),
		`{"thscode":"600519"}`)

	require.Equal(t, http.StatusOK, code)
	data := out["data"].(map[string]any)
	assert.Equal(t, "# HALO 600519.SH\n\n正文", data["markdown"])
	assert.Equal(t, "mixed", data["asset_type"])
	assert.Empty(t, ks.created, "预览不得写知识库")
}

func TestReportTreatsNoDataAsSuccessNotError(t *testing.T) {
	// 与归档相反：归档必须拒绝（它要写库），预览必须让前端拿到 ok=false 去显示
	// 「先同步年报」，而不是弹一个失败提示。
	stub := newHaloStub(t, map[string]any{
		"ok": false, "thscode": "600519.SH",
		"reason": "600519.SH 没有已落库的年报事实。先调用 halo.filing.sync。",
	})
	code, out := doReport(t, reportRouter(&recordingKnowledgeService{}, halo.NewHTTPClient(stub.server.URL)),
		`{"thscode":"600519"}`)

	require.Equal(t, http.StatusOK, code, "没数据不是 HTTP 错误")
	data := out["data"].(map[string]any)
	assert.Equal(t, false, data["ok"])
	assert.Contains(t, data["reason"], "没有已落库的年报事实")
}

func TestReportRejectsEmptyThscode(t *testing.T) {
	stub := newHaloStub(t, map[string]any{"ok": true})
	code, _ := doReport(t, reportRouter(&recordingKnowledgeService{}, halo.NewHTTPClient(stub.server.URL)),
		`{"thscode":"   "}`)
	assert.Equal(t, http.StatusBadRequest, code)
}

func TestReportDefaultsAnnouncementsOnAndHonoursExplicitOff(t *testing.T) {
	stub := newHaloStub(t, map[string]any{"ok": true, "markdown": "x", "thscode": "600519.SH"})
	r := reportRouter(&recordingKnowledgeService{}, halo.NewHTTPClient(stub.server.URL))

	doReport(t, r, `{"thscode":"600519"}`)
	assert.Equal(t, true, stub.got["include_announcements"], "未传时默认取公告")

	doReport(t, r, `{"thscode":"600519","include_announcements":false}`)
	assert.Equal(t, false, stub.got["include_announcements"], "显式 false 必须被尊重")
}

func TestReportNeverRequestsExternal(t *testing.T) {
	stub := newHaloStub(t, map[string]any{"ok": true, "markdown": "x", "thscode": "600519.SH"})
	doReport(t, reportRouter(&recordingKnowledgeService{}, halo.NewHTTPClient(stub.server.URL)),
		`{"thscode":"600519"}`)
	assert.Equal(t, false, stub.got["include_external"],
		"预览不得打东财易封子域；要拉外网是分析时的显式动作")
}
