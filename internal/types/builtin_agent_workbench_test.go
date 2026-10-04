package types

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
	"gopkg.in/yaml.v3"
)

// config/builtin_agents.yaml 里的 `workbench` 标签必须能在注册表里查到。
//
// 为什么值得一条测试：拼错不会报任何错 —— `ResolveWorkbench` 刻意把未知值降级
// 成「无工作台」（否则会渲染一个永久空白的面板且没有任何诊断），于是前端只是
// 少了一块面板，日志里一行 warn 没人会看。而 workbench 决定的是**面板本身**：
// 比如 HALO 的报告对话框就挂在 finance 的 kline 面板里，标签丢了等于砍掉
// 半条分析流程，而 agent 照常回答问题，看不出哪里不对。
//
// 直接读 YAML 而不是走 LoadBuiltinAgentsConfig：后者带 sync.Once，在一个测试
// 二进制里只能生效一次，会把这个包其余用例的全局状态一起改掉。
func TestBuiltinAgentsDeclareRegisteredWorkbenches(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(repoRoot(t), "config", "builtin_agents.yaml"))
	require.NoError(t, err, "读不到 builtin_agents.yaml")

	var file builtinAgentsFile
	require.NoError(t, yaml.Unmarshal(raw, &file), "builtin_agents.yaml 解析失败")
	require.NotEmpty(t, file.BuiltinAgents, "一个内置 agent 都没解析出来 —— 结构或版式变了")

	declared := 0
	for _, entry := range file.BuiltinAgents {
		rawWorkbench := entry.Config.Workbench
		if rawWorkbench == "" {
			continue
		}
		declared++
		if _, downgraded := ResolveWorkbench(rawWorkbench); downgraded {
			t.Errorf("builtin agent %q 声明了未注册的工作台 %q —— "+
				"它会被静默降级成「无工作台」，前端一块面板都不渲染。"+
				"要新增工作台，先注册到 types/workbench.go 并在前端实现其组件",
				entry.ID, rawWorkbench)
		}
	}
	// 一个都没声明说明解析已经失效（字段改名/缩进变了），而不是「恰好没人用」。
	require.NotZero(t, declared, "没有任何 agent 声明 workbench —— 解析器可能已随 yaml 版式失效")
}

func repoRoot(t *testing.T) string {
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
