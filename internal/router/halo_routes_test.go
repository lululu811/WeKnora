package router

import (
	"net/http"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/handler"
)

// TestHaloRoutesRegistered 断言三条 HALO 路由真的挂上了。
//
// 为什么需要它：未认证探测分不出「路由存在但要鉴权」与「路由根本不存在」——
// 全局 Auth 中间件在 Gin 的 NoRoute 之前运行，两种情况都返回 401。所以只能
// 直接看路由表。
func TestHaloRoutesRegistered(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := NewRouter(RouterParams{
		HaloHandler:   &handler.HaloHandler{},
		SystemHandler: &handler.SystemHandler{},
	})

	registered := map[string]bool{}
	for _, route := range r.Routes() {
		registered[route.Method+" "+route.Path] = true
	}

	for _, want := range []string{
		http.MethodPost + " /api/v1/halo/report",
		http.MethodPost + " /api/v1/halo/sync",
		http.MethodPost + " /api/v1/halo/archive",
	} {
		assert.Truef(t, registered[want], "缺路由 %s；已注册的 halo 路由: %v",
			want, haloRoutes(r))
	}
}

// TestHaloRoutesAbsentWithoutHandler 守住「nil handler 不注册路由」的契约：
// 没有金融栈的部署必须照常启动，而不是因为缺依赖崩掉。
func TestHaloRoutesAbsentWithoutHandler(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := NewRouter(RouterParams{SystemHandler: &handler.SystemHandler{}})
	require.Empty(t, haloRoutes(r), "HaloHandler 为 nil 时不该注册任何 halo 路由")
}

func haloRoutes(r *gin.Engine) []string {
	var out []string
	for _, route := range r.Routes() {
		if len(route.Path) >= 11 && route.Path[:11] == "/api/v1/hal" {
			out = append(out, route.Method+" "+route.Path)
		}
	}
	return out
}
