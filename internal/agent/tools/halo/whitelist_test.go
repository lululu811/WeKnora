package halo_test

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/agent"
)

// builtin_agents.yaml 里 builtin-halo 的工具白名单，和代码里真正注册的工具名是
// 两处独立维护的文本。写错一个名字既不会编译失败，也不会在启动时报错：白名单
// 决定注册遍历范围（agent_service.go 的 switch），不在名单里的工具连模型的
// tool schema 都进不去，调用时只会得到 "tool not found"。
//
// 这不是假想。本测试落地前，这份白名单里的资产负债表工具写成了
// `hittink.finance.financial.statement.balance`（多一个 t），于是它一直是死的；
// 而 HALO 的六维与七个定性锚点全在 Python 侧算，面板与 agent 都不报错，
// 没有任何现象会暴露它 —— 直到有人对着工具清单逐条核对。
//
// 同族还有 internal/agent/tools/zettaranc/whitelist_test.go 守 builtin-zettaranc
// 那一份。两处分开写是因为两个 agent 的家族不同：halo.* 只属于 builtin-halo，
// 而 zettaranc 那边不列 hithink 全量工具。
var (
	// builtin_agents.yaml 里形如  - "hithink.finance.analysis.trend"  的条目
	whitelistEntryRe = regexp.MustCompile(`-\s*"([a-z][\w.]*)"`)
)

// haloWhitelist 取出 builtin-halo 的 allowed_tools 列表。
func haloWhitelist(t *testing.T) []string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(findRepoRoot(t), "config", "builtin_agents.yaml"))
	require.NoError(t, err, "读不到 builtin_agents.yaml")

	const marker = `- id: "builtin-halo"`
	yaml := string(raw)
	start := strings.Index(yaml, marker)
	require.GreaterOrEqual(t, start, 0, "builtin_agents.yaml 里找不到 builtin-halo")

	// 截到下一个 agent 定义为止。builtin-halo 目前是最后一个，那时 Index 返回
	// -1，就该用整段而不是空段。
	rest := yaml[start+len(marker):]
	if end := strings.Index(rest, "\n  - id:"); end > 0 {
		rest = rest[:end]
	}

	var out []string
	for _, m := range whitelistEntryRe.FindAllStringSubmatch(rest, -1) {
		out = append(out, m[1])
	}
	require.NotEmpty(t, out,
		"builtin-halo 块里没解析出任何工具名 —— 解析器可能已随 yaml 版式失效")
	return out
}

// TestHaloWhitelistMatchesRegisteredTools 把白名单与注册表对齐，双向都查：
// 白名单多一个（调不到）与少一个（模型不知道它存在）都是错。
func TestHaloWhitelistMatchesRegisteredTools(t *testing.T) {
	registered := agent.RegisteredToolNames()
	whitelist := haloWhitelist(t)

	inWhitelist := map[string]bool{}
	for _, n := range whitelist {
		inWhitelist[n] = true
	}

	var missing []string
	for _, n := range whitelist {
		if !registered[n] {
			missing = append(missing, n)
		}
	}

	// 反向只在 halo 这一族上做：hithink.finance.* 是 HALO 与 Z哥共用的数据源，
	// 两边各有取舍（HALO 不列形态识别那一组），拿注册表反过来要求它会误报。
	var unclaimed []string
	for n := range registered {
		if strings.HasPrefix(n, "halo.") && !inWhitelist[n] {
			unclaimed = append(unclaimed, n)
		}
	}

	assert.Empty(t, missing,
		"白名单里有这些工具但代码里没有，模型调用时才会报「工具不存在」：%v", missing)
	assert.Empty(t, unclaimed,
		"halo.* 工具没有出现在 builtin-halo 的白名单里，模型根本不会去调：%v", unclaimed)
}

func TestHaloWhitelistHasNoDuplicates(t *testing.T) {
	seen := map[string]bool{}
	for _, n := range haloWhitelist(t) {
		assert.False(t, seen[n], "工具 %q 在白名单里重复出现", n)
		seen[n] = true
	}
}

func findRepoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	require.NoError(t, err, "Getwd")
	for range 8 {
		if _, err := os.Stat(filepath.Join(dir, "config", "builtin_agents.yaml")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("找不到仓库根（config/builtin_agents.yaml）")
	return ""
}
