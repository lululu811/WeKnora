package middleware

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/gin-gonic/gin"
)

// error_handler_test.go 守住响应体里不出现内部错误。
//
// 这一层是**160 处 WithDetails(err.Error()) 的唯一出口**——那些调用点把
// 原始错误塞进 Details，是各自写的；把它们变成"不下发"的是这里。所以这里
// 失守，160 处一起失守。

// runErrorHandler 跑一次 ErrorHandler 中间件，返回响应体。
func runErrorHandler(t *testing.T, err error) map[string]any {
	t.Helper()
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest(http.MethodGet, "/x", nil)
	c.Error(err)
	ErrorHandler()(c)

	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应体不是合法 JSON：%v（原文 %s）", err, w.Body.String())
	}
	return body
}

// detailsOf 取出响应体里的 details。
func detailsOf(t *testing.T, body map[string]any) any {
	t.Helper()
	errObj, ok := body["error"].(map[string]any)
	if !ok {
		t.Fatalf("响应体里没有 error 对象：%v", body)
	}
	return errObj["details"]
}

// TestErrorHandlerHidesInternalDetailsOn5xx 是核心的一条：
// 5xx 的 details 里不该出现 SQL 报错与内网地址。
func TestErrorHandlerHidesInternalDetailsOn5xx(t *testing.T) {
	internal := "Error 1146 (42S02): Table 'weknora.kb_shares' doesn't exist"
	body := runErrorHandler(t, apperrors.NewInternalServerError("处理知识库失败").WithDetails(internal))

	details, _ := detailsOf(t, body).(string)
	if details == internal {
		t.Fatalf("5xx 的 details 原样下发了内部错误：%q", details)
	}
	if details == "" {
		t.Fatalf("5xx 的 details 被清空了：客户端拿不到任何信息")
	}
}

// TestErrorHandlerKeepsUserFacingDetailsOn4xx 守住另一头：
// 4xx 的 details 是给用户看的，不能被藏起来。
func TestErrorHandlerKeepsUserFacingDetailsOn4xx(t *testing.T) {
	userFacing := "参数错误：thscode 不能为空"
	body := runErrorHandler(t, apperrors.NewBadRequestError("参数不合法").WithDetails(userFacing))

	if got, _ := detailsOf(t, body).(string); got != userFacing {
		t.Errorf("4xx 的 details 是 %q，期望原样保留 %q", got, userFacing)
	}
}

// TestErrorHandlerKeepsStructuredDetails 守住结构化 details：
// 那是给客户端看的字段值（哪个模型被占用、哪些字段冲突），不是错误原文。
func TestErrorHandlerKeepsStructuredDetails(t *testing.T) {
	structured := map[string]any{"model": "gpt-4", "used_by": []any{"kb-1"}}
	body := runErrorHandler(t, apperrors.NewInternalServerError("模型被占用").WithDetails(structured))

	got, ok := detailsOf(t, body).(map[string]any)
	if !ok {
		t.Fatalf("结构化 details 被改成了非结构化：%v", detailsOf(t, body))
	}
	if got["model"] != "gpt-4" {
		t.Errorf("结构化 details 的内容丢了：%v", got)
	}
}

// TestErrorHandlerHidesInternalDetailsOn4xx 说明净化看的是**内容**不是状态码：
// 一个 400 里也可能裹着 DB 错误，那同样不该下发。
func TestErrorHandlerHidesInternalDetailsOn4xx(t *testing.T) {
	internal := "dial tcp 10.0.3.17:5432: connection refused"
	body := runErrorHandler(t, apperrors.NewBadRequestError("参数不合法").WithDetails(internal))

	details, _ := detailsOf(t, body).(string)
	if details == internal {
		t.Fatalf("4xx 的 details 原样下发了内部错误：%q", details)
	}
}

// TestErrorHandlerPlainErrorStaysGeneric 守住另一条路径：
// 不是 AppError 的错误，响应体里只有一句通用文案。
func TestErrorHandlerPlainErrorStaysGeneric(t *testing.T) {
	body := runErrorHandler(t, errors.New("dial tcp 10.0.3.17:5432: connection refused"))

	errObj, ok := body["error"].(map[string]any)
	if !ok {
		t.Fatalf("响应体里没有 error 对象：%v", body)
	}
	if msg, _ := errObj["message"].(string); msg != "Internal server error" {
		t.Errorf("非 AppError 的 message 是 %q，期望 %q", msg, "Internal server error")
	}
}
