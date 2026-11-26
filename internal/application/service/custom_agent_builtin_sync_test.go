package service

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// builtinSyncRepo 是只关心内置 agent 同步的假仓储。
type builtinSyncRepo struct {
	interfaces.CustomAgentRepository
	agent   *types.CustomAgent
	updates int
}

func (r *builtinSyncRepo) GetAgentByID(_ context.Context, _ string, _ uint64) (*types.CustomAgent, error) {
	return r.agent, nil
}

func (r *builtinSyncRepo) UpdateAgent(_ context.Context, agent *types.CustomAgent) error {
	r.updates++
	r.agent = agent
	return nil
}

// loadBuiltinAgentsForSyncTest 加载出厂内置 agent 定义。
//
// 用真实的 config/builtin_agents.yaml 而不是编一份假的出厂清单：这条测试要
// 证明的是"出厂扩充后老租户能拿到"，而出厂清单本身就是被扩充的那一份。
func loadBuiltinAgentsForSyncTest(t *testing.T) {
	t.Helper()
	require.NoError(t, types.LoadBuiltinAgentsConfig(filepath.Join("..", "..", "..", "config")))
}

// factoryAllowedTools 返回出厂内置 agent 的工具白名单。
func factoryAllowedTools(t *testing.T, builtinID string) []string {
	t.Helper()
	factory := types.GetBuiltinAgentWithContext(context.Background(), builtinID, 1)
	require.NotNil(t, factory, "出厂内置 agent %q 取不到", builtinID)
	require.NotEmpty(t, factory.Config.AllowedTools, "出厂内置 agent %q 的白名单是空的", builtinID)
	return factory.Config.AllowedTools
}

// TestSyncBuiltinAllowedToolsKeepsTenantToolsAndAddsFactoryTools 是这条机制
// 的核心断言：出厂新增的工具被补上，**租户自己加的工具一个都不能丢**。
//
// 丢租户的工具比"少几个内置工具"严重得多：前者是租户明确要的能力消失了，
// 而且没有任何提示。
func TestSyncBuiltinAllowedToolsKeepsTenantToolsAndAddsFactoryTools(t *testing.T) {
	loadBuiltinAgentsForSyncTest(t)

	const tenantTool = "tenant_only_tool_that_factory_never_declared"
	factoryTools := factoryAllowedTools(t, "builtin-zettaranc")

	repo := &builtinSyncRepo{agent: &types.CustomAgent{
		ID:       "builtin-zettaranc",
		TenantID: 1,
		Config: types.CustomAgentConfig{
			AllowedTools: []string{tenantTool, factoryTools[0]},
		},
	}}
	svc := &customAgentService{repo: repo}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))

	agent, err := svc.GetAgentByID(ctx, "builtin-zettaranc")
	require.NoError(t, err)

	require.Contains(t, agent.Config.AllowedTools, tenantTool,
		"租户自己加的工具被出厂同步冲掉了")
	for _, name := range factoryTools {
		require.Contains(t, agent.Config.AllowedTools, name,
			"出厂工具 %q 没被同步进来——模型会照着系统提示调它，然后拿到 tool not found", name)
	}
	require.Equal(t, 1, repo.updates, "并集变了却没有落库")
}

// TestSyncBuiltinAllowedToolsIsIdempotent 守住"只写一次"。
//
// 同步在读路径上，每次读都写库会把一次同步放大成每次读一次写。
func TestSyncBuiltinAllowedToolsIsIdempotent(t *testing.T) {
	loadBuiltinAgentsForSyncTest(t)

	factoryTools := factoryAllowedTools(t, "builtin-zettaranc")
	repo := &builtinSyncRepo{agent: &types.CustomAgent{
		ID:       "builtin-zettaranc",
		TenantID: 1,
		Config: types.CustomAgentConfig{
			AllowedTools: append([]string{}, factoryTools...),
		},
	}}
	svc := &customAgentService{repo: repo}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))

	_, err := svc.GetAgentByID(ctx, "builtin-zettaranc")
	require.NoError(t, err)
	require.Equal(t, 0, repo.updates, "白名单已经与出厂一致，却又写了一次")
}

// TestSyncBuiltinAllowedToolsKeepsNilUnset 守住 nil 的语义。
//
// nil 的意思是"从没设过白名单"，registerTools 会回落到 DefaultAllowedTools()；
// 往里塞出厂清单会把那条语义改掉。
func TestSyncBuiltinAllowedToolsKeepsNilUnset(t *testing.T) {
	loadBuiltinAgentsForSyncTest(t)

	repo := &builtinSyncRepo{agent: &types.CustomAgent{
		ID:       "builtin-zettaranc",
		TenantID: 1,
	}}
	svc := &customAgentService{repo: repo}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))

	agent, err := svc.GetAgentByID(ctx, "builtin-zettaranc")
	require.NoError(t, err)
	require.Nil(t, agent.Config.AllowedTools, "从没设过白名单的 agent 被塞进了出厂清单")
	require.Equal(t, 0, repo.updates)
}

// TestSyncBuiltinAllowedToolsIgnoresCustomAgents 守住"只动内置 agent"。
//
// 自建 agent 的白名单是租户自己的，出厂清单跟它无关。
func TestSyncBuiltinAllowedToolsIgnoresCustomAgents(t *testing.T) {
	loadBuiltinAgentsForSyncTest(t)

	repo := &builtinSyncRepo{agent: &types.CustomAgent{
		ID:       "my-own-agent",
		TenantID: 1,
		Config: types.CustomAgentConfig{
			AllowedTools: []string{"only_mine"},
		},
	}}
	svc := &customAgentService{repo: repo}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))

	agent, err := svc.GetAgentByID(ctx, "my-own-agent")
	require.NoError(t, err)
	require.Equal(t, []string{"only_mine"}, agent.Config.AllowedTools)
	require.Equal(t, 0, repo.updates)
}
