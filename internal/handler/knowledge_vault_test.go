package handler

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"

	"github.com/Tencent/WeKnora/internal/application/service"
	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/middleware"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

type vaultAssetServiceStub struct {
	interfaces.KnowledgeService

	knowledge *types.Knowledge
	lookupErr error

	absPath  string
	ctype    string
	assetErr error

	gotRef string
}

func (s *vaultAssetServiceStub) GetKnowledgeByID(context.Context, string) (*types.Knowledge, error) {
	return s.knowledge, s.lookupErr
}

func (s *vaultAssetServiceStub) VaultAsset(_ context.Context, _ *types.Knowledge, ref string) (string, string, error) {
	s.gotRef = ref
	return s.absPath, s.ctype, s.assetErr
}

func newVaultAssetRouter(svc interfaces.KnowledgeService) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.ErrorHandler())
	router.Use(func(c *gin.Context) {
		c.Set(types.TenantIDContextKey.String(), uint64(42))
		c.Next()
	})
	h := &KnowledgeHandler{kgService: svc}
	router.GET("/knowledge/:id/vault-asset", h.GetKnowledgeVaultAsset)
	return router
}

func manualKnowledge() *types.Knowledge {
	k := &types.Knowledge{
		ID:          "k1",
		TenantID:    42,
		Type:        types.KnowledgeTypeManual,
		FileType:    types.KnowledgeTypeManual,
		Source:      types.KnowledgeTypeManual,
		ParseStatus: "completed",
	}
	_ = k.SetManualMetadata(&types.ManualKnowledgeMetadata{
		Content:   "# 标题\n\n![fig](images/fig.png)",
		Format:    types.ManualKnowledgeFormatMarkdown,
		Status:    types.ManualKnowledgeStatusPublish,
		Version:   1,
		VaultPath: filepath.Join("某公众号", "某文章", "某文章.md"),
	})
	return k
}

// 成功路径要把三件事钉住：字节真的送出去了、Content-Type 来自服务端白名单、
// 以及那组防 XSS 的响应头在位 —— 这些字节来自上传路径之外，没经过内容审查。
func TestVaultAssetServesImage(t *testing.T) {
	dir := t.TempDir()
	abs := filepath.Join(dir, "fig.png")
	payload := []byte("\x89PNG\r\n\x1a\nnot-really")
	if err := os.WriteFile(abs, payload, 0o644); err != nil {
		t.Fatal(err)
	}

	stub := &vaultAssetServiceStub{
		knowledge: manualKnowledge(),
		absPath:   abs,
		ctype:     "image/png",
	}
	router := newVaultAssetRouter(stub)

	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
		"/knowledge/k1/vault-asset?ref=images%2Ffig.png", nil))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	if got := w.Body.String(); got != string(payload) {
		t.Fatalf("body = %q, want %q", got, string(payload))
	}
	// The reference must reach the service unmangled — the whole design rests
	// on the client never sending a path.
	if stub.gotRef != "images/fig.png" {
		t.Fatalf("ref passed through = %q, want %q", stub.gotRef, "images/fig.png")
	}
	if got := w.Header().Get("Content-Type"); got != "image/png" {
		t.Errorf("Content-Type = %q, want image/png", got)
	}
	if got := w.Header().Get("X-Content-Type-Options"); got != "nosniff" {
		t.Errorf("X-Content-Type-Options = %q, want nosniff", got)
	}
	if got := w.Header().Get("Content-Security-Policy"); got == "" {
		t.Error("Content-Security-Policy 未设置，SVG 可携带脚本")
	}
}

// 路径穿越全部收敛到同一个 404。区分「条目没记 vault_path」与「图不存在」
// 等于用状态码给文件系统做测绘，所以两种情况必须长得一模一样。
func TestVaultAssetMissIsIndistinguishable(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  error
	}{
		{"no vault path recorded", service.ErrVaultAssetNotFound},
		{"image absent", os.ErrNotExist},
	} {
		t.Run(tc.name, func(t *testing.T) {
			stub := &vaultAssetServiceStub{
				knowledge: manualKnowledge(),
				assetErr:  tc.err,
			}
			router := newVaultAssetRouter(stub)

			w := httptest.NewRecorder()
			router.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
				"/knowledge/k1/vault-asset?ref=images%2Ffig.png", nil))

			if w.Code != http.StatusNotFound {
				t.Fatalf("status = %d, want 404", w.Code)
			}
			if body := w.Body.String(); body != `{"error":"资源不存在"}` {
				t.Fatalf("body = %s", body)
			}
		})
	}
}

// vault 没配置是部署状态，不是这次请求的错，单独用 501 表示，前端据此决定
// 要不要还去重写图片地址。
func TestVaultAssetNotConfiguredIsDistinct(t *testing.T) {
	stub := &vaultAssetServiceStub{
		knowledge: manualKnowledge(),
		assetErr:  service.ErrVaultNotConfigured,
	}
	router := newVaultAssetRouter(stub)

	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
		"/knowledge/k1/vault-asset?ref=images%2Ffig.png", nil))

	if w.Code != http.StatusNotImplemented {
		t.Fatalf("status = %d, want 501", w.Code)
	}
}

func TestVaultAssetRejectsBadRequests(t *testing.T) {
	t.Run("missing ref", func(t *testing.T) {
		stub := &vaultAssetServiceStub{knowledge: manualKnowledge()}
		router := newVaultAssetRouter(stub)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/knowledge/k1/vault-asset", nil))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400; body=%s", w.Code, w.Body.String())
		}
	})

	// 非手工条目没有 vault_path 可言，直接在 handler 层挡掉，不去问磁盘。
	t.Run("not a manual entry", func(t *testing.T) {
		stub := &vaultAssetServiceStub{
			knowledge: &types.Knowledge{ID: "k1", Type: "file", FileType: "pdf"},
		}
		router := newVaultAssetRouter(stub)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
			"/knowledge/k1/vault-asset?ref=images%2Ffig.png", nil))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400; body=%s", w.Code, w.Body.String())
		}
		if stub.gotRef != "" {
			t.Error("非手工条目不该走到磁盘解析")
		}
	})

	t.Run("knowledge not found", func(t *testing.T) {
		stub := &vaultAssetServiceStub{lookupErr: apperrors.NewNotFoundError("nope")}
		router := newVaultAssetRouter(stub)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
			"/knowledge/k1/vault-asset?ref=images%2Ffig.png", nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("status = %d, want 404; body=%s", w.Code, w.Body.String())
		}
	})
}
