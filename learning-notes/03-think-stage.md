# 03 - Think 阶段详解

> ReAct 的第一步：LLM 推理与决策

---

## 🎯 学习目标

- 理解 Think 阶段的核心流程
- 掌握 LLM 调用机制（流式响应）
- 了解瞬态错误重试策略
- 理解上下文溢出恢复
- 掌握优雅降级机制

---

## 📍 代码位置

```
文件：internal/agent/think.go
核心函数：callLLMWithRetry() - 495-641 行
流式处理：streamLLMToEventBus() - 37-100 行
```

---

## 🔄 Think 阶段核心流程

```
┌─────────────────────────────────────────────────────────────┐
│  Think 阶段                                                   │
│  callLLMWithRetry() - 495-641 行                              │
│                                                               │
│  输入：                                                       │
│    - messages: [system + history + current_query]            │
│    - tools: [工具定义列表]                                    │
│                                                               │
│  流程：                                                       │
│    1. 日志记录 + Pipeline 指标                               │
│    2. 消息清洗（SanitizeMessages）                           │
│    3. 流式 LLM 调用（streamThinkingToEventBus）              │
│    4. 溢出恢复（如果触发）                                   │
│    5. 瞬态错误重试                                           │
│    6. 优雅降级（如果彻底失败）                               │
│    7. 响应日志                                               │
│                                                               │
│  输出：                                                       │
│    - response: ChatResponse (Content + ToolCalls + Usage)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 关键设计点

### 1. 流式响应（Streaming）

```go
// think.go 542 行
response, err := e.streamThinkingToEventBus(ctx, messages, tools, iteration, sessionID)
```

**为什么用流式**：
- ✅ **实时反馈**：Token 逐个返回，用户不用干等
- ✅ **节省首字延迟**：第一个 Token 到达就可以开始显示
- ✅ **支持 Function Calling**：工具调用参数也是流式返回

**流式处理的核心**（37-100 行）：
```go
for chunk := range stream {
    // 每个 chunk 包含一部分内容
    // 实时通过 EventBus 推送给前端
    // 累积到 result.Content 和 result.ToolCalls
}
```

**流式响应结构**：
```go
type streamLLMResult struct {
    Content          string              // 累积的文本内容
    ReasoningContent string              // 思考过程（部分模型支持）
    ToolCalls        []types.LLMToolCall // 工具调用
    Usage            *types.TokenUsage   // Token 使用统计
    FinishReason     string              // 结束原因
    StreamError      string              // 流错误（如果有）
}
```

### 2. 瞬态错误重试

```go
// think.go 565-578 行
if err != nil && isTransientError(err) {
    for retry := 1; retry <= maxLLMRetries; retry++ {
        retryDelay := time.Duration(retry) * time.Second  // 指数退避
        time.Sleep(retryDelay)
        response, err = e.streamThinkingToEventBus(...)
        if err == nil || !isTransientError(err) {
            break
        }
    }
}
```

**什么是瞬态错误**：
- 网络超时
- 限流（rate limit）
- 服务端临时错误（5xx）

**为什么自动重试**：
- 生产环境网络不稳定，一次失败不代表永远失败
- 指数退避（1s, 2s, 3s...）避免加重服务端压力

### 3. 上下文溢出恢复

```go
// think.go 548-563 行
if err != nil && !e.overflowRecovered && compaction.IsOverflowError(err) {
    e.overflowRecovered = true
    logger.Warnf(ctx, "[Agent][Round-%d] Provider rejected the request as too large; "+
        "compacting and retrying once", round)
    compacted := e.forceCompaction(ctx, messages, round)
    *messagesPtr = compacted
    messages = agenttools.SanitizeMessages(compacted)
    response, err = e.streamThinkingToEventBus(ctx, messages, tools, iteration, sessionID)
}
```

**场景**：对话太长，LLM 拒绝请求（"context too large"）

**解决**：
1. 检测到溢出错误
2. 强制压缩历史（`forceCompaction`）
3. 重试一次

**为什么只重试一次**：
- 如果压缩后还溢出，说明不是历史长度的问题
- 避免无限重试浪费资源

### 4. 优雅降级

```go
// think.go 586-602 行
if totalTC := countTotalToolCalls(state.RoundSteps); totalTC > 0 {
    logger.Warnf(ctx, "[Agent] LLM failed but have %d steps with %d tool calls — "+
        "attempting final answer synthesis from existing results",
        len(state.RoundSteps), totalTC)
    if synthErr := e.streamFinalAnswerToEventBus(ctx, query, state, sessionID, messages); synthErr != nil {
        return nil, fmt.Errorf("LLM call failed: %w (synthesis also failed: %v)", err, synthErr)
    }
    state.IsComplete = true
    return nil, nil // graceful degradation succeeded
}
```

**场景**：LLM 调用彻底失败（重试 N 次后仍然失败）

**降级策略**：
- 如果之前有工具调用结果，尝试用这些结果合成最终答案
- 而不是直接报错，让用户看到"抱歉，出错了"

**价值**：
- 用户体验更好（至少能看到部分结果）
- 避免"全有或全无"的失败

### 5. 消息清洗

```go
// think.go 540 行
messages = agenttools.SanitizeMessages(messages)
```

**为什么要清洗**：
- LLM 对消息格式有严格要求（如不能有两个连续的 `assistant` 消息）
- 工具结果必须紧跟在工具调用后面
- 清洗后避免 LLM 报错

---

## 📊 Think 阶段的输入和输出

### 输入

```go
messages []chat.Message  // 对话历史（包含 system prompt + 历史消息 + 当前查询）
tools []chat.Tool        // 工具定义（给 LLM 的 function calling schema）
```

**messages 示例**：
```json
[
  {
    "role": "system",
    "content": "你是一个智能助手，可以调用工具帮助用户..."
  },
  {
    "role": "user",
    "content": "北京天气怎么样？"
  }
]
```

**tools 示例**：
```json
[
  {
    "name": "weather_api",
    "description": "查询指定城市的天气",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {
          "type": "string",
          "description": "城市名称"
        }
      },
      "required": ["location"]
    }
  }
]
```

### 输出

```go
response *types.ChatResponse {
    Content          string              // LLM 返回的文本
    ToolCalls        []types.LLMToolCall // LLM 要调用的工具
    FinishReason     string              // 结束原因（stop/tool_calls/length...）
    Usage            types.TokenUsage    // Token 使用统计
    ReasoningContent string              // 思考过程（如果模型支持）
}
```

**输出示例 1：直接回答**
```json
{
  "Content": "北京明天晴天，25°C",
  "ToolCalls": [],
  "FinishReason": "stop",
  "Usage": {"PromptTokens": 100, "CompletionTokens": 20, "TotalTokens": 120}
}
```

**输出示例 2：调用工具**
```json
{
  "Content": "让我查一下北京明天的天气...",
  "ToolCalls": [
    {
      "ID": "call_xxx",
      "Function": {
        "Name": "weather_api",
        "Arguments": "{\"location\": \"北京\"}"
      }
    }
  ],
  "FinishReason": "tool_calls",
  "Usage": {"PromptTokens": 100, "CompletionTokens": 30, "TotalTokens": 130}
}
```

---

## 🎯 Think 阶段的核心价值

| 特性 | 价值 |
|---|---|
| **流式响应** | 实时反馈，用户体验好 |
| **自动重试** | 生产环境稳定性 |
| **溢出恢复** | 长对话不会崩溃 |
| **优雅降级** | 失败时也有部分结果 |
| **消息清洗** | 避免 LLM 格式错误 |

---

## 💡 LLM 的决策机制

### LLM 怎么选择工具？

**Function Calling 机制**：

```
LLM 收到：
  - 用户问题："北京天气怎么样？"
  - 工具定义：weather_api、search_kb 等
  
LLM 内部推理：
  1. 用户想知道天气
  2. 我没有实时天气数据
  3. weather_api 可以获取天气
  4. 决策：调用 weather_api

LLM 输出：
  ToolCalls: [{Function: "weather_api", Arguments: {"location": "北京"}}]
  FinishReason: "tool_calls"
```

### LLM 怎么判断"我回答完了"？

**两种方式**：
1. **不调用工具**（ToolCalls 为空）
2. **FinishReason = "stop"**（自然停止）

**判断依据**：
- System Prompt 的规则（"如果有足够信息，直接回答"）
- 对话历史
- 工具描述

---

## 📝 小结

**Think 阶段做了什么**：
1. 把对话历史 + 工具定义发给 LLM
2. 流式接收响应，实时推送给前端
3. 处理各种异常（瞬态错误、溢出、彻底失败）
4. 返回结构化的响应（文本 + 工具调用 + Token 统计）

**工程化亮点**：
- 不是简单的 `LLM.Call()`，而是有重试、恢复、降级
- 流式处理让用户不用干等
- 优雅降级避免"全有或全无"的失败

---

## 🎯 下一步

→ [04 - Analyze 阶段详解](04-analyze-stage.md)

---

## 💡 思考题

1. 为什么 Think 阶段要用流式响应？
2. 瞬态错误重试的指数退避是什么意思？
3. 优雅降级的场景是什么？如何合成最终答案？
