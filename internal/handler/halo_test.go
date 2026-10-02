package handler

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

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
