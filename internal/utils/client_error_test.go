package utils

import (
	"errors"
	"net/http"
	"strings"
	"testing"
)

// TestSanitizeForClientKeepsUserFacingMessages 守住「用户面向的文案原样保留」。
//
// 净化器最容易犯的错不是漏掉内部痕迹，而是**把该给用户看的话也藏起来**：
// 「参数错误：thscode 不能为空」被换成「请求无法处理」，调用方与用户就都
// 不知道该改什么了。
func TestSanitizeForClientKeepsUserFacingMessages(t *testing.T) {
	cases := []string{
		"参数错误：thscode 不能为空",
		"Invalid email or password",
		"用户不存在",
		"密码不符合安全策略",
		"当前登录用户不存在",
	}
	for _, msg := range cases {
		if got := SanitizeForClientString(http.StatusBadRequest, msg); got != msg {
			t.Errorf("用户面向的文案被改了：%q → %q", msg, got)
		}
	}
}

// TestSanitizeForClientHidesInternalTraces 守住「内部痕迹不下发」。
//
// 这些字符串每一个都把内部信息交出去了：表名、内网地址、驱动名、绝对路径、
// Go 运行时的类型断言。出现在响应体里就等于把内部拓扑送给客户端。
func TestSanitizeForClientHidesInternalTraces(t *testing.T) {
	internal := []string{
		"Error 1146 (42S02): Table 'weknora.kb_shares' doesn't exist",
		"Error 1062 (23000): Duplicate entry 'a@b.com' for key 'users.email'",
		"dial tcp 10.0.3.17:5432: connection refused",
		"pq: relation \"users\" does not exist",
		"sqlite3: no such table: knowledge_bases",
		"open /Users/chenlei/007_DB/WeKnora/data/duckdb/a.duckdb: no such file",
		"interface conversion: interface {} is nil, not string",
		"runtime error: invalid memory address or nil pointer dereference",
		"UNIQUE constraint failed: knowledge_bases.name",
	}
	for _, msg := range internal {
		if HasInternalTrace(msg) != true {
			t.Errorf("没认出内部痕迹：%q", msg)
			continue
		}
		got := SanitizeForClientString(http.StatusInternalServerError, msg)
		if got != ClientInternalErrorMessage {
			t.Errorf("5xx 的内部痕迹没被换成通用文案：%q → %q", msg, got)
		}
		if strings.Contains(got, "weknora") || strings.Contains(got, "10.0.3.17") {
			t.Errorf("通用文案里还带着内部信息：%q", got)
		}
	}
}

// TestSanitizeForClientMessageDependsOnStatus 说明通用文案按状态码分。
//
// 5xx 是服务端的问题，4xx 是请求处理不了——但两者的原因都不该外泄。
func TestSanitizeForClientMessageDependsOnStatus(t *testing.T) {
	msg := "dial tcp 10.0.3.17:5432: connection refused"
	if got := SanitizeForClientString(http.StatusInternalServerError, msg); got != ClientInternalErrorMessage {
		t.Errorf("5xx 的文案是 %q，期望 %q", got, ClientInternalErrorMessage)
	}
	if got := SanitizeForClientString(http.StatusBadRequest, msg); got != ClientRequestErrorMessage {
		t.Errorf("4xx 的文案是 %q，期望 %q", got, ClientRequestErrorMessage)
	}
}

// TestSanitizeForClientStripsControlChars 守住控制字符不进响应体。
//
// 换行与制表符在响应体里没有意义，却能把日志与前端显示撑开。
func TestSanitizeForClientStripsControlChars(t *testing.T) {
	got := SanitizeForClientString(http.StatusBadRequest, "第一行\n第二行\t制表\x00")
	if strings.ContainsAny(got, "\n\t\x00") {
		t.Errorf("控制字符没被清掉：%q", got)
	}
	if !strings.Contains(got, "第一行") {
		t.Errorf("正文被误删：%q", got)
	}
}

// TestSanitizeForClientNilError 是最平凡的一条：没有错误就没有文案。
func TestSanitizeForClientNilError(t *testing.T) {
	if got := SanitizeForClient(http.StatusBadRequest, nil); got != "" {
		t.Errorf("nil 错误得到 %q，期望空串", got)
	}
	if got := SanitizeForClient(http.StatusBadRequest, errors.New("参数错误：参数非法")); got != "参数错误：参数非法" {
		t.Errorf("普通错误被改了：%q", got)
	}
}
