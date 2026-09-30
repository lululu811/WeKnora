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

// deliberatelyNotWhitelisted 是"故意存在但不授予 agent"的工具。
//
// 这份表只允许收**桩工具**：调用必然失败、且失败对模型有信息量的工具。
// 把它交给模型是净损失——白白消耗迭代预算，污染工具调用记录。
//
// registeredToolNames 扫的是源码里的 Name() 字面量，不是运行时注册表，
// 所以「从 register.go 里摘掉注册」并不足以让本测试变绿：文件还在，
// 名字还在，测试就仍然认为「代码里注册了」。这不是测试的缺陷，是它的
// 前提——它守的是「不存在无人认领的 zettaranc.* 工具」。
//
// 上限设为 1 是故意的：新增条目必须同时改这里的数字，说明新增者真的想过
// 「这个工具为什么不该给模型」，而不是随手往垃圾桶里丢。
var deliberatelyNotWhitelisted = map[string]string{
	"zettaranc.backtest": "回测桩，Execute 恒返回 Success:false；真回测落地后连同本条目一起删除",
}

const deliberatelyNotWhitelistedLimit = 1

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
		if _, excused := deliberatelyNotWhitelisted[n]; excused {
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
	// 豁免表本身也要被守：它一旦能随便增长，就变成了「没人认领的工具」的垃圾桶。
	if len(deliberatelyNotWhitelisted) > deliberatelyNotWhitelistedLimit {
		t.Errorf("故意不授予 agent 的工具已有 %d 个（上限 %d）：%v\n"+
			"新增前请先回答——为什么模型不该调它？为什么要留着它？",
			len(deliberatelyNotWhitelisted), deliberatelyNotWhitelistedLimit, keysOf(deliberatelyNotWhitelisted))
	}
	// 豁免的必须是真桩：真做了的工具藏在这里，等于既不注册也不承认。
	for n := range deliberatelyNotWhitelisted {
		if !strings.Contains(deliberatelyNotWhitelisted[n], "桩") {
			t.Errorf("%s 在豁免表里但理由不像桩工具：%q", n, deliberatelyNotWhitelisted[n])
		}
	}
}

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
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
