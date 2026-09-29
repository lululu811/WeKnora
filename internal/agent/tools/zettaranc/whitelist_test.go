package zettaranc_test

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// builtin_agents.yaml 里 zettaranc 的工具白名单，和代码里真正注册的工具名
// 是两处独立维护的文本。写错一个名字不会编译失败，运行时才报
// "工具不存在"，而那个工具往往正是模型最想调的那个。
//
// 本测试把两边对齐：不一致就在 CI 里变红，并打印差集。
//
// 这里的"对齐"是**双向**的：白名单多一个（调不到）与少一个
// （新工具白名单没跟上，模型根本不知道它存在）都是错。

var (
	toolNameRe = regexp.MustCompile(
		`func\s*\(\s*\w+\s+\*?\w+\s*\)\s*Name\(\)\s*string\s*\{\s*return\s+"([\w.]+)"`)

	// builtin_agents.yaml 里形如  - "hithink.finance.analysis.trend"  的条目
	whitelistEntryRe = regexp.MustCompile(`-\s*"([a-z][\w.]*)"`)
)

// registeredToolNames 扫 internal/agent/tools 下所有 Go 文件，取出 Name() 返回值。
func registeredToolNames(t *testing.T) map[string]bool {
	t.Helper()
	root := findRepoRoot(t)
	toolsDir := filepath.Join(root, "internal", "agent", "tools")

	out := map[string]bool{}
	err := filepath.Walk(toolsDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, m := range toolNameRe.FindAllStringSubmatch(string(raw), -1) {
			out[m[1]] = true
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫描工具目录失败：%v", err)
	}
	if len(out) == 0 {
		t.Fatal("一个工具名都没扫到 —— 正则可能已随重构失效")
	}
	return out
}

// zettarancWhitelist 取出 builtin-zettaranc 的 tools 列表。
func zettarancWhitelist(t *testing.T) []string {
	t.Helper()
	root := findRepoRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "config", "builtin_agents.yaml"))
	if err != nil {
		t.Fatalf("读不到 builtin_agents.yaml：%v", err)
	}
	yaml := string(raw)

	start := strings.Index(yaml, "builtin-zettaranc")
	if start < 0 {
		t.Fatal("builtin_agents.yaml 里找不到 builtin-zettaranc")
	}
	// 截到下一个 agent 定义为止。zettaranc 是 yaml 里最后一个 agent，
	// strings.Index 会返回 -1，那时就该用整段而不是空段。
	rest := yaml[start+len("builtin-zettaranc"):]
	if end := strings.Index(rest, "\n  - id:"); end > 0 {
		rest = rest[:end]
	}

	var out []string
	for _, m := range whitelistEntryRe.FindAllStringSubmatch(rest, -1) {
		out = append(out, m[1])
	}
	if len(out) == 0 {
		t.Fatal("builtin-zettaranc 块里没解析出任何工具名")
	}
	return out
}

func TestZettarancWhitelistMatchesRegisteredTools(t *testing.T) {
	registered := registeredToolNames(t)
	whitelist := zettarancWhitelist(t)

	inWhitelist := map[string]bool{}
	for _, n := range whitelist {
		inWhitelist[n] = true
	}

	var missing, extra []string
	for _, n := range whitelist {
		if !registered[n] {
			missing = append(missing, n)
		}
	}
	for n := range registered {
		// 只比对金融/战法这一族：web_search、knowledge 那些不归这里管
		if !strings.HasPrefix(n, "hithink.") && !strings.HasPrefix(n, "zettaranc.") {
			continue
		}
		if !inWhitelist[n] {
			extra = append(extra, n)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)

	if len(missing) > 0 {
		t.Errorf("白名单里有这些工具但代码里没有，模型调用时才会报「工具不存在」：%v", missing)
	}
	if len(extra) > 0 {
		t.Errorf("代码里注册了这些工具但 Z哥白名单没列，模型根本不会去调：%v", extra)
	}
}

func TestZettarancWhitelistHasNoDuplicates(t *testing.T) {
	seen := map[string]bool{}
	for _, n := range zettarancWhitelist(t) {
		if seen[n] {
			t.Errorf("工具 %q 在白名单里重复出现", n)
		}
		seen[n] = true
	}
}

func findRepoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd: %v", err)
	}
	for i := 0; i < 8; i++ {
		if _, err := os.Stat(filepath.Join(dir, "config", "builtin_agents.yaml")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("找不到仓库根（config/builtin_agents.yaml）")
	return ""
}
