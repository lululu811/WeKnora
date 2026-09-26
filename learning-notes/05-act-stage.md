# 05 - Act 阶段详解

> ReAct 的第三步：执行工具调用

---

## 🎯 学习目标

- 理解 Act 阶段的核心流程
- 掌握串行/并行执行策略
- 了解 JSON 参数修复机制
- 理解超时控制
- 掌握截断检测
- 了解 Langfuse 追踪

---

## 📍 代码位置

```
文件：internal/agent/act.go
核心函数：
  - executeToolCalls() - 225-259 行（入口）
  - executeToolCallsParallel() - 292-333 行（并行执行）
  - executeSingleToolCall() - 379-386 行（串行执行）
  - runToolCall() - 390-622 行（单个工具执行核心）
```

---

## 🔄 Act 阶段核心流程

```
┌─────────────────────────────────────────────────────────────┐
│              executeToolCalls() 入口                          │
│  225-259 行                                                   │
│                                                               │
│  1. 检查 ToolCalls 是否为空                                   │
│  2. 检查 FinishReason 是否为 "length"（截断）                │
│     ├─ 是 → failTruncatedToolCalls() 拒绝所有工具           │
│     └─ 否 → 继续                                             │
│  3. 判断执行模式：                                           │
│     ├─ ParallelToolCalls && n >= 2 → 并行执行               │
│     └─ 否则 → 串行执行                                       │
└─────────────────────────────────────────────────────────────┘
                          ↓
          ┌───────────────┴───────────────┐
          ↓                               ↓
┌─────────────────────┐      ┌─────────────────────────┐
│ 串行执行             │      │ 并行执行                 │
│ executeSingleToolCall│      │ executeToolCallsParallel │
│ 256-258 行           │      │ 292-333 行               │
│                      │      │                          │
│ for tc in ToolCalls: │      │ errgroup + SetLimit(8)   │
│   runToolCall()      │      │ 并发执行所有工具         │
└─────────────────────┘      └─────────────────────────┘
          ↓                               ↓
          └───────────────┬───────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              runToolCall() 单个工具执行核心                   │
│  390-622 行                                                   │
│                                                               │
│  1. 参数解析 + JSON 修复 (399-461)                           │
│  2. 解析 ToolCallTarget（MCP 代理）(463-472)                 │
│  3. 发送 tool-hint 事件（UI 进度显示）(478-491)              │
│  4. 创建 Langfuse span (501-528)                             │
│  5. 设置超时 + 执行工具 (530-558)                            │
│  6. 记录结果 + 完成 span (561-621)                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 关键设计点

### 1. 串行 vs 并行执行

```go
// act.go 251-254 行
if e.config.ParallelToolCalls && n >= 2 {
    e.executeToolCallsParallel(ctx, response, step, iteration, sessionID, assistantMessageID)
    return
}

// 串行执行
for i, tc := range response.ToolCalls {
    e.executeSingleToolCall(ctx, tc, i, step, iteration, round, sessionID, assistantMessageID)
}
```

**为什么需要并行**：
- ✅ 多个独立工具可以并行（如同时查天气和搜索知识库）
- ✅ 减少总执行时间
- ⚠️ 需要处理并发安全

**并行实现**（292-333 行）：
```go
g, gCtx := errgroup.WithContext(ctx)
g.SetLimit(8)  // 最多 8 个并发

for i, tc := range response.ToolCalls {
    // 检查工具是否支持并发
    if !agenttools.CanRunConcurrently(tc.Function.Name) {
        _ = g.Wait()  // 等待前面的完成
        results[i] = e.runToolCall(...)  // 串行执行
        continue
    }
    
    g.Go(func() error {
        toolCall := e.runToolCall(...)
        results[i] = toolCall
        return nil
    })
}
_ = g.Wait()
```

**关键特性**：
- `SetLimit(8)`：限制最大并发数
- `CanRunConcurrently`：检查工具是否支持并发（写操作通常不支持）
- `errgroup`：Go 标准库的并发控制

**哪些工具不能并发**：
- 写操作（如 `write_sandbox_file`）
- 有副作用的操作
- 依赖顺序的操作

### 2. 截断检测（防止执行不完整的工具调用）

```go
// act.go 240-246 行
if isLengthFinishReason(response.FinishReason) {
    logger.Warnf(ctx, "[Agent][Round-%d] Response hit the completion-token cap; "+
        "refusing %d tool call(s) with possibly truncated arguments", ...)
    e.failTruncatedToolCalls(ctx, response, step, iteration, sessionID)
    return
}
```

**为什么需要这个检查**：
- LLM 输出被截断时，工具参数可能不完整
- 执行不完整的参数会导致错误（如写入半个文件）
- 直接拒绝，让 LLM 重新生成

**拒绝策略**（273-288 行）：
```go
func (e *AgentEngine) failTruncatedToolCalls(...) {
    for i, tc := range response.ToolCalls {
        toolCall := types.ToolCall{
            Result: &types.ToolResult{
                Success: false,
                Error: truncatedArgumentsError,  // "Tool call was not executed: ..."
            },
        }
        step.ToolCalls = append(step.ToolCalls, toolCall)
        e.emitToolOutcome(ctx, toolCall, iteration, sessionID)
    }
}
```

**错误信息**：
```
Tool call was not executed: the model output was cut off before the arguments 
finished, so they are incomplete rather than wrong. Re-issue the call with 
complete JSON object. If the payload is large, split it across several smaller calls.
```

### 3. JSON 参数解析 + 修复

```go
// act.go 399-461 行
var args map[string]any
argsStr := tc.Function.Arguments

if err := json.Unmarshal([]byte(argsStr), &args); err != nil {
    // 尝试修复 JSON
    repaired, truncated := agenttools.RepairJSONDetail(argsStr)
    if repairErr := json.Unmarshal([]byte(repaired), &args); repairErr != nil {
        // 修复失败，返回错误
        return types.ToolCall{
            Result: &types.ToolResult{
                Success: false,
                Error: "Failed to parse tool arguments: ...",
            },
        }
    }
    
    // 修复成功，但参数被截断
    if truncated {
        return types.ToolCall{
            Result: &types.ToolResult{
                Success: false,
                Error: truncatedArgumentsError,
            },
        }
    }
    
    // 修复成功，继续执行
    tc.Function.Arguments = repaired
}
```

**为什么要修复**：
- LLM 有时会生成格式错误的 JSON（如缺少引号、括号）
- 自动修复可以提高成功率
- 但如果参数被截断，拒绝执行

**修复示例**：
```json
// 错误 JSON（缺少引号）
{"location": 北京}

// 修复后
{"location": "北京"}
```

### 4. 超时控制

```go
// act.go 531-558 行
execTimeout := toolExecutionTimeout(tc.Function.Name, tc.Function.Arguments)
execCtx, toolCancel := context.WithTimeout(toolExecCtx, execTimeout)
result, err = e.toolRegistry.ExecuteTool(
    execCtx, tc.Function.Name,
    json.RawMessage(tc.Function.Arguments),
)
toolCancel()
```

**超时策略**：
- 不同工具有不同的超时时间
- 防止工具卡住导致整个 Agent 卡住
- 超时后返回错误，LLM 可以重试或换策略

**默认超时**：
```go
func toolExecutionTimeout(toolName, args string) time.Duration {
    // 根据工具类型返回不同超时
    // 如：shell_exec 可能 60s，search_knowledge 可能 30s
}
```

### 5. Langfuse 可观测性

```go
// act.go 501-528 行
toolCtx, toolSpan := mgr.StartSpan(ctx, langfuse.SpanOptions{
    Name:  "agent.tool." + executionName,
    Input: toolSpanInput,
    Metadata: map[string]interface{}{
        "iteration":    iteration,
        "round":        round,
        "tool_call_id": tc.ID,
        ...
    },
})

// 587 行
finishToolSpan(toolSpan, toolCall, err, duration)
```

**追踪内容**：
- 工具名称、参数、结果
- 执行时长
- 成功/失败状态
- MCP 代理信息（如果有）

**Langfuse UI 展示**：
```
trace
  └─ agent.execute
       └─ agent.round.1
            ├─ chat (LLM 调用)
            └─ agent.tool.weather_api (工具执行)
                 ├─ Input: {"location": "北京"}
                 ├─ Output: "晴天，25°C"
                 └─ Duration: 1234ms
```

### 6. 事件发射（UI 进度显示）

```go
// act.go 478-491 行 - 工具开始
e.eventBus.Emit(ctx, event.Event{
    Type: event.EventAgentToolCall,
    Data: event.AgentToolCallData{
        ToolName:  executionName,
        Arguments: executionArgs,
        Hint:      toolHint,  // 给 UI 显示的提示
    },
})

// 338-376 行 - 工具完成
e.emitToolOutcome(ctx, toolCall, iteration, sessionID)
```

**事件类型**：
- `EventAgentToolCall`：工具开始执行
- `EventAgentToolResult`：工具结果
- `EventAgentTool`：工具执行详情

**UI 展示**：
```
用户看到：
  🔧 正在查询天气...
  ✓ weather_api 执行成功（1.2s）
```

---

## 📊 Act 阶段的输入和输出

### 输入

```go
response.ToolCalls []types.LLMToolCall {
    ID:        "call_xxx"
    Function: {
        Name:      "weather_api"
        Arguments: `{"location": "北京"}`  // JSON 字符串
    }
}
```

### 输出

```go
step.ToolCalls []types.ToolCall {
    ID:       "call_xxx"
    Name:     "weather_api"
    Args:     map[string]any{"location": "北京"}
    Duration: 1234  // 毫秒
    Result:   &types.ToolResult{
        Success: true
        Output:  "晴天，25°C"
        Error:   ""
    }
}
```

---

## 🎯 Act 阶段的核心价值

| 特性 | 价值 |
|---|---|
| **串行/并行** | 根据工具特性选择最优执行策略 |
| **截断检测** | 防止执行不完整的工具调用 |
| **JSON 修复** | 提高成功率，减少 LLM 格式错误 |
| **超时控制** | 防止工具卡住整个 Agent |
| **Langfuse 追踪** | 完整的可观测性 |
| **事件发射** | UI 实时显示进度 |

---

## 💡 MCP 工具代理

### 什么是 MCP 工具？

**MCP (Model Context Protocol)** 是一个标准协议，允许 Agent 调用外部工具。

**WeKnora 的 MCP 实现**：
```go
// act.go 463-472 行
var target *types.ToolCallTarget
if len(tc.UnresolvedHandles) == 0 {
    target = e.toolRegistry.MCPCallTarget(ctx, tc.Function.Name, json.RawMessage(tc.Function.Arguments))
}
executionName, executionArgs := tc.Function.Name, args
if target != nil {
    executionName, executionArgs = target.Name, target.Args
}
```

**MCP 工具调用流程**：
```
LLM 决定调用：mcp_tool("search", {"query": "..."})
    ↓
ToolRegistry.MCPCallTarget()
    ├─ 解析目标：MCP service + tool name
    └─ 返回：ToolCallTarget{Name: "search", Args: {...}}
    ↓
执行：调用外部 MCP server
    ↓
返回结果
```

---

## 📝 小结

**Act 阶段做了什么**：
1. 检查工具调用是否被截断（拒绝不完整调用）
2. 选择串行或并行执行策略
3. 解析 JSON 参数（自动修复格式错误）
4. 创建 Langfuse span（可观测性）
5. 设置超时 + 执行工具
6. 记录结果 + 发送事件

**核心流程**：
```
LLM 决定调用工具
    ↓
检查截断 → 拒绝不完整调用
    ↓
选择执行模式（串行/并行）
    ↓
解析参数 + JSON 修复
    ↓
创建 Langfuse span
    ↓
设置超时 + 执行工具
    ↓
记录结果 + 发送事件
    ↓
返回 ToolCall（包含 Result）
```

**工程化亮点**：
- 并行执行 + 并发控制
- 截断检测 + 自动拒绝
- JSON 修复 + 容错
- 超时控制 + 防止卡住
- 完整追踪 + 可观测性

---

## 🎯 下一步

→ [06 - Observe 阶段详解](06-observe-stage.md)

---

## 💡 思考题

1. 为什么要区分串行和并行执行？哪些工具不能并发？
2. 截断检测的作用是什么？如果不检测会怎样？
3. JSON 修复的成功率和风险是什么？
4. 超时控制的意义是什么？如何选择合适的超时时间？
