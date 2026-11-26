package handler

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// 守卫生效：handler 层不许再出现「把 err.Error() 原样塞进响应体」。
//
// 净化已经收在一处（utils.WriteError 与 utils.SanitizeForClient），但收在一处
// 不等于不会有人再绕过去——绕过去的那一处，SQL 报错与内网地址就又出去了。
// 所以这条测试把「绕过」这件事本身变成不可能：命中即红。
//
// 它守的是**形状**（错误怎么进响应体），不是具体某一条错误消息。消息会变，
// 形状不该变。

// rawErrorEchoRe 抓「err.Error() 直接进了响应体」的两种形状：
//   - gin.H{... err.Error() ...}（绝大多数）
//   - c.String / c.Data / c.JSONP 的响应体参数
//
// 已经净化过的写法（utils.WriteError(c, status, err) 与
// utils.SanitizeForClient(status, err)）不含 err.Error()，不会被误伤。
var rawErrorEchoRe = regexp.MustCompile(`(?s)gin\.H\{[^}]*err\.Error\(\)|c\.(?:String|Data|JSONP)\([^)]*err\.Error\(\)`)

func TestNoRawErrorEchoIntoResponse(t *testing.T) {
	root := findRepoRootForGuard(t)
	dir := filepath.Join(root, "internal", "handler")

	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		if loc := rawErrorEchoRe.FindIndex(raw); loc != nil {
			line := 1 + strings.Count(string(raw[:loc[0]]), "\n")
			t.Errorf("%s:%d：把 err.Error() 原样塞进了响应体。"+
				"内部错误（SQL 报错、连接失败、绝对路径）不能下发——"+
				"请改用 utils.WriteError(c, status, err) 或 utils.SanitizeForClient(status, err)",
				relToRepo(t, root, path), line)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫描 handler 目录失败：%v", err)
	}
}

// findRepoRootForGuard 向上找 go.mod。
func findRepoRootForGuard(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("取工作目录失败：%v", err)
	}
	for {
		if _, statErr := os.Stat(filepath.Join(dir, "go.mod")); statErr == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("没找到 go.mod")
		}
		dir = parent
	}
}

func relToRepo(t *testing.T, root, path string) string {
	t.Helper()
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return path
	}
	return rel
}
