# 02 - ReAct 循环概述

> 理解 WeKnora Agent 的核心引擎：单引擎 ReAct 循环

---

## 🎯 学习目标

- 理解 ReAct 的基本概念
- 掌握 WeKnora 的单引擎设计
- 理解状态机（iterOutcome）的作用
- 建立 ReAct 四阶段的全局观

---

## 🤔 什么是 ReAct？

**ReAct = Reasoning + Acting**

这是 2022 年由 Google 和普林斯顿大学提出的 Agent 范式。

**核心思想**：
```
用户问题 → [Think → Act → Observe] → 最终答案
              ↑         ↓
              └─────────┘ (循环直到完成)
```

- **Think（思考）**：LLM 分析问题，决定下一步做什么
- **Act（行动）**：调用工具执行具体操作
- **Observe（观察）**：查看工具返回结果，更新上下文
- **循环**：重复以上步骤，直到 LLM 认为可以给出最终答案

**论文**：https://arxiv.org/abs/2210.03629

---

## 🏗️ WeKnora 的单引擎设计

### 对比：多 Agent vs 单引擎

| 框架 | 模型 | 特点 |
|---|---|---|
| **CrewAI / AutoGen** | 多 Agent 协作 | 多个 Agent 互相对话 |
| **LangGraph** | 图编排 | 显式 DAG 编排 |
| **WeKnora** | 单引擎 ReAct | 一个 Agent + 动态工具作用域 |

### WeKnora 的设计选择

```
┌─────────────────────────────────────────────────────────┐
│  WeKnora 单引擎设计                                      │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │           AgentEngine (单实例)                    │  │
│  │                                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐              │  │
│  │  │  Tool A      │  │  Tool B      │              │  │
│  │  │ (weather)    │  │ (search_kb)  │              │  │
│  │  └──────────────┘  └──────────────┘              │  │
│  │                                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐              │  │
│  │  │  Tool C      │  │  Tool D      │              │  │
│  │  │ (shell_exec) │  │ (mcp_tool)   │              │  │
│  │  └──────────────┘  └──────────────┘              │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  通过工具作用域动态调整能力，而不是多个 Agent            │
└─────────────────────────────────────────────────────────┘
```

**优势**：
- ✅ 状态收敛（一个 Agent + 工具执行历史）
- ✅ 可观测性统一（Langfuse 一条 trace）
- ✅ 调试简单（没有 Agent 间消息传递）
- ✅ 90% 的企业场景够用

**取舍**：
- ⚠️ 复杂多步任务靠 LLM 自己规划，没有显式 DAG

---

## 🔄 ReAct 四阶段

```
┌─────────────────────────────────────────────────────────┐
│  ReAct 循环                                               │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  1. Think 阶段 (think.go)                        │   │
│  │     - 调用 LLM                                   │   │
│  │     - LLM 推理：是否需要工具？                    │   │
│  │     - 输出：ToolCalls 或 最终答案                 │   │
│  └─────────────────────────────────────────────────┘   │
│                          ↓                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │  2. Analyze 阶段 (observe.go)                    │   │
│  │     - 判断 LLM 是否完成                          │   │
│  │     - 有 ToolCalls → 未完成，继续                 │   │
│  │     - 无 ToolCalls + "stop" → 完成，结束         │   │
│  └─────────────────────────────────────────────────┘   │
│                          ↓                               │
│            ┌─────────┴─────────┐                        │
│            │                   │                        │
│        isDone=true        isDone=false                  │
│            │                   │                        │
│            ↓                   ↓                        │
│    ┌───────────────┐  ┌─────────────────────────────┐ │
│    │ 返回最终答案   │  │  3. Act 阶段 (act.go)        │ │
│    │ 结束循环       │  │     - 执行工具               │ │
│    └───────────────┘  │     - API 调用 / 脚本执行    │ │
│                        │     - 返回结果               │ │
│                        └─────────────────────────────┘ │
│                                    ↓                     │
│                        ┌─────────────────────────────┐ │
│                        │  4. Observe 阶段             │ │
│                        │     - 结果回填到 messages    │ │
│                        │     - 下一轮 Think 可见      │ │
│                        └─────────────────────────────┘ │
│                                    ↓                     │
│                            回到 Think 阶段              │
└─────────────────────────────────────────────────────────┘
```

---

## 🎛️ 状态机：iterOutcome

### 什么是状态机？

**状态机 = 状态 + 转移规则**

WeKnora 用 `iterOutcome` 枚举表达 ReAct 循环的三种状态：

```go
// engine.go 666-679 行
type iterOutcome int

const (
    iterOutcomeNext     iterOutcome = iota  // 继续下一轮
    iterOutcomeContinue                     // 重试当前轮
    iterOutcomeBreak                        // 结束循环
)
```

### 三种状态的含义

| 状态 | 含义 | 轮次计数 | 场景 |
|---|---|---|---|
| **Next** | 这一轮做完了，进入下一轮 | ✅ +1 | 正常执行、工具调用完成 |
| **Continue** | 这一轮没做好，重试当前轮 | ❌ 不变 | 空内容、可恢复的错误 |
| **Break** | 结束了，退出循环 | - | 最终答案、死循环检测触发 |

### 状态机在主循环中的应用

```go
// engine.go 643-650 行
switch outcome {
case iterOutcomeNext:
    state.CurrentRound++  // 轮次 +1
    continue loop
case iterOutcomeContinue:
    continue loop          // 轮次不变
case iterOutcomeBreak:
    break loop
}
```

### 为什么要分 Next 和 Continue？

**关键区别**：轮次计数器

```
场景：LLM 返回空内容

第 1 轮：空内容 → Continue（重试）→ round=1
第 1 轮：空内容 → Continue（重试）→ round=1
第 1 轮：正常回答 → Next → round=2 → 完成

如果用 Next：
第 1 轮：空内容 → round++ → round=2
第 2 轮：空内容 → round++ → round=3
第 3 轮：空内容 → round++ → round=4 → 超过 MaxIterations → 停止

结果：3 次全是空内容，用户一个问题都没解决
```

**工程价值**：
- ✅ MaxIterations 语义清晰（"最多 N 次有效尝试"）
- ✅ 重试不消耗预算
- ✅ 日志可读（round=1 retry 2 vs round=3）

---

## 📊 核心代码位置

| 模块 | 文件 | 关键函数 |
|---|---|---|
| **主循环** | `internal/agent/engine.go` | `Execute()` 294 行 |
| | | `executeLoop()` 575 行 |
| | | `runReActIteration()` 688 行 |
| **Think** | `internal/agent/think.go` | `callLLMWithRetry()` 495 行 |
| **Analyze** | `internal/agent/observe.go` | `analyzeResponse()` 376 行 |
| **Act** | `internal/agent/act.go` | `executeToolCalls()` 225 行 |
| | | `runToolCall()` 390 行 |
| **Observe** | `internal/agent/observe.go` | `appendToolResults()` 913 行 |

---

## 🎯 完整流程示例

**用户问**："北京明天天气怎么样？"

```
第 1 轮 Think：
  LLM 推理：我想知道北京明天的天气，需要调用 weather_api
  输出：
    Content: "让我查一下北京明天的天气..."
    ToolCalls: [{Function: "weather_api", Arguments: {"location": "北京"}}]
    FinishReason: "tool_calls"

第 1 轮 Analyze：
  判断：ToolCalls 非空 → isDone=false
  输出：iterOutcomeNext（继续执行工具）

第 1 轮 Act：
  执行：weather_api(location="北京")
  结果：{weather: "晴天", temperature: "25°C"}

第 1 轮 Observe：
  回填到 messages：
    assistant: "让我查一下北京明天的天气..." + ToolCalls
    tool: {weather: "晴天", temperature: "25°C"}

第 2 轮 Think：
  LLM 看到工具结果，推理：我有足够信息了
  输出：
    Content: "北京明天是晴天，温度 25°C"
    ToolCalls: []
    FinishReason: "stop"

第 2 轮 Analyze：
  判断：ToolCalls 为空 + FinishReason="stop" → isDone=true
  输出：iterOutcomeBreak（结束循环）

最终答案：北京明天是晴天，温度 25°C
```

---

## 💡 关键设计点

### 1. 无状态引擎

```go
// engine.go 28-34 行注释
// History persistence note: the engine is stateless across turns.
// Conversation history is rebuilt from the DB once per turn by the caller
// (see service.LoadAgentHistory) and passed into Execute as llmContext.
```

**含义**：
- AgentEngine 不缓存历史
- 每次对话从数据库重建历史
- 重启不丢数据

### 2. Langfuse 可观测性

```
trace
  └─ agent.execute (顶层 span)
       ├─ agent.round.1
       │    ├─ chat (LLM 调用)
       │    └─ tool.weather_api (工具执行)
       ├─ agent.round.2
       │    ├─ chat
       │    └─ (无工具)
       └─ ...
```

**价值**：一次对话的完整 trace，调试友好

### 3. 循环兜底机制

```go
type loopGuards struct {
    emptyRetries             int  // 空内容重试次数
    consecutiveSameContent   int  // 重复内容检测
    consecutiveLength        int  // 输出截断检测
}
```

**解决的问题**：
- 死循环（LLM 反复输出相同内容）
- Token 爆炸（输出被截断但不知道）
- 空响应（LLM 返回空内容）

---

## 📝 小结

**ReAct 循环的核心**：
1. **Think**：LLM 决策（选择工具或给出答案）
2. **Analyze**：判断是否完成（isDone）
3. **Act**：执行工具（如果有）
4. **Observe**：结果回填（供下一轮使用）

**状态机的价值**：
- 显式表达控制流
- Next/Continue/Break 三种状态
- 重试不消耗轮次预算

**单引擎的优势**：
- 状态收敛
- 可观测性统一
- 调试简单

---

## 🎯 下一步

→ [03 - Think 阶段详解](03-think-stage.md)

---

## 💡 思考题

1. 为什么 WeKnora 选择单引擎而不是多 Agent？
2. 状态机的三种状态分别对应什么场景？
3. 为什么要区分 Next 和 Continue？
