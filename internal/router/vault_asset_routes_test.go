package router

import (
	"net/http"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"

	"github.com/Tencent/WeKnora/internal/handler"
)

// TestVaultAssetRouteRegistered 断言 vault 资源路由真的挂上了。
//
// 为什么需要它：未认证探测分不出「路由存在但要鉴权」与「路由根本不存在」——
// 全局 Auth 中间件在 Gin 的 NoRoute 之前运行，两种情况都返回 401。所以只能
// 直接看路由表。
func TestVaultAssetRouteRegistered(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := NewRouter(RouterParams{
		KnowledgeHandler: &handler.KnowledgeHandler{},
		SystemHandler:    &handler.SystemHandler{},
	})

	registered := map[string]bool{}
	for _, route := range r.Routes() {
		registered[route.Method+" "+route.Path] = true
	}

	want := http.MethodGet + " /api/v1/knowledge/:id/vault-asset"
	assert.Truef(t, registered[want], "缺路由 %s；已注册的 vault 路由: %v",
		want, vaultAssetRoutes(r))
}

// vault-asset 与它的同门路由必须一起在册：它读的是同一条
// knowledge/:id 链上的资源，守卫也来自同一组 g.* 链。少了任何一条，都说明
// 这组路由的注册被改过，值得回头看一眼。
//
// 注意这里不套 halo 那套「nil handler 不注册路由」的断言：
// RegisterKnowledgeRoutes 本来就没有 nil 守卫，/preview 与 /download 都是
// 无条件注册的，所以「handler 为 nil 时本路由缺席」不是本组的契约，
// 照抄那个断言只会写出一个恒假的测试。
func TestVaultAssetRoutesRegisteredWithSiblings(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := NewRouter(RouterParams{
		KnowledgeHandler: &handler.KnowledgeHandler{},
		SystemHandler:    &handler.SystemHandler{},
	})

	registered := map[string]bool{}
	for _, route := range r.Routes() {
		registered[route.Method+" "+route.Path] = true
	}

	for _, want := range []string{
		http.MethodGet + " /api/v1/knowledge/:id/preview",
		http.MethodGet + " /api/v1/knowledge/:id/download",
		http.MethodGet + " /api/v1/knowledge/:id/vault-asset",
	} {
		assert.Truef(t, registered[want], "缺路由 %s；已注册的 vault 路由: %v",
			want, vaultAssetRoutes(r))
	}
}

func vaultAssetRoutes(r *gin.Engine) []string {
	var out []string
	for _, route := range r.Routes() {
		if route.Path == "/api/v1/knowledge/:id/vault-asset" {
			out = append(out, route.Method+" "+route.Path)
		}
	}
	return out
}
