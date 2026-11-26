---
status: accepted
---

# 内置 agent 的工具白名单：出厂清单自动并入已存配置

内置 agent（`config/builtin_agents.yaml` 里定义的那些）的工具白名单有**两个来源**：

- **出厂的**——写在 `config/builtin_agents.yaml`；
- **租户改的**——落在 `custom_agents` 表里。

改动之前，读路径一律是「**DB 里有行就用 DB 的，没有才用 yaml 的**」
（`ListAgents` 与 `GetAgentByID` 两处都是这个判断）。于是出厂清单扩充之后，
**已存在的 DB 行永远停在旧版**：出厂新增的工具，模型在系统提示里看得见
（提示也来自 yaml），却调不到——表现为 `tool not found: <名字>`。

2026-10-19 那次提交（`1fd105a19`「补齐 Z哥白名单」）只改了 yaml，没有配套的
同步机制，所以那次扩充对所有已有租户都是无效的。

## 决定

**读内置 agent 时，把出厂白名单与已存白名单求并集，并把并集落库。**

- 并集而不是覆盖：租户可以在 UI 里给内置 agent 加自己的工具，那些不能被出厂
  配置抹掉。丢租户的工具比"少几个内置工具"严重得多——前者是租户明确要的
  能力消失了，而且没有任何提示。
- 落库而不是只在内存里合并：其它读路径（分享、嵌入通道等）直接查仓储，
  只在内存里合并的话它们拿到的还是旧清单。
- 幂等：并集与已存相同时不写库。同步落在读路径上，每次读都写会把一次同步
  放大成每次读一次写。
- 写失败只记日志：读取路径不该因为一次同步失败而失败，那会把"白名单少了几个
  工具"升级成"agent 加载不出来"。

## 代价

**出厂新增的工具会自动勾上**，租户手动取消勾选的内置工具会被重新勾回。这是
"自动同步"的定义，不是副作用——要改的是"出厂清单变更后老租户能用到新工具"。

`AllowedTools` 为 **nil**（从没设过）的 agent 不参与合并：nil 的语义是
"回落到 `DefaultAllowedTools()`"，往里塞出厂清单会把那条语义改掉。

## 位置

- `internal/types/builtin_agent_config.go`：`MergeBuiltinAllowedTools`（纯函数，
  并集 + 保序 + 去重）。
- `internal/application/service/custom_agent.go`：`syncBuiltinAllowedTools`，
  在 `GetAgentByID` 与 `ListAgents` 的内置 agent 分支上调用。
