# 04 - Analyze 阶段详解

> ReAct 的第二步：判断 LLM 是否完成

---

## 🎯 学习目标

- 理解 Analyze 阶段的核心判断逻辑
- 掌握三种结束条件
- 了解空内容处理机制
- 理解输出截断处理
- 掌握流式输出优化

---

## 📍 代码位置

```
文件：internal/agent/observe.go
核心函数：analyzeResponse() - 376-565 行
```

---

## 🔄 Analyze 阶段核心流程

```
┌─────────────────────────────────────────────────────────────┐
│  Analyze 阶段                                                 │
│  analyzeResponse() - 376-565 行                               │
│                                                               │
│  输入：                                                       │
│    - response: LLM 的响应（Content + ToolCalls + FinishReason）│
│    - step: 当前轮次的 AgentStep                              │
│                                                               │
│  判断逻辑：                                                   │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ Case 0: 内容被安全过滤？                               │  │
│    │  ├─ FinishReason == "content_filter"                  │  │
│    │  ├─ ToolCalls 为空                                     │  │
│    │  └─ 是 → 返回 isDone=true, finalAnswer=安全提示       │  │
│    └──────────────────────────────────────────────────────┘  │
│                          ↓ 否                                │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ Case 1: LLM 自然停止？                                │  │
│    │  ├─ isNaturalStopFinishReason(FinishReason)           │  │
│    │  ├─ ToolCalls 为空                                     │  │
│    │  ├─ Content 为空？                                     │  │
│    │  │   ├─ 是 → emptyContent=true (触发重试)             │  │
│    │  │   └─ 否 → 返回 isDone=true, finalAnswer=Content   │  │
│    │  └─ 流式输出处理（避免重复）                           │  │
│    └──────────────────────────────────────────────────────┘  │
│                          ↓ 否                                │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ Case 2: 输出被截断？                                   │  │
│    │  ├─ isLengthFinishReason(FinishReason)                │  │
│    │  ├─ ToolCalls 为空                                     │  │
│    │  ├─ Content 为空？                                     │  │
│    │  │   ├─ 是 → emptyContent=true (触发重试)             │  │
│    │  │   └─ 否 → truncated=true, 返回已有内容             │  │
│    │  └─ 避免"从头重写"的死循环                            │  │
│    └──────────────────────────────────────────────────────┘  │
│                          ↓ 否                                │
│    ┌──────────────────────────────────────────────────────┐  │
│    │ 默认：有工具调用                                       │  │
│    │  └─ 返回 isDone=false (继续执行工具)                  │  │
│    └──────────────────────────────────────────────────────┘  │
│                                                               │
│  输出：                                                       │
│    - responseVerdict: 判定结果（isDone + finalAnswer + ...）  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 关键设计点

### 1. 三种结束条件

| Case | FinishReason | ToolCalls | 含义 | 处理 |
|---|---|---|---|---|
| **Case 0** | `content_filter` | 空 | 内容被安全过滤 | 返回安全提示 |
| **Case 1** | `stop` / 自然停止 | 空 | LLM 认为完成了 | 返回最终答案 |
| **Case 2** | `length` | 空 | 输出被截断 | 返回已有内容（标记 truncated） |
| **默认** | 任意 | 非空 | 需要调用工具 | 继续执行工具 |

### 2. responseVerdict 结构

```go
type responseVerdict struct {
    isDone       bool       // 是否完成
    finalAnswer  string     // 最终答案
    emptyContent bool       // 空内容（触发重试）
    truncated    bool       // 输出被截断
    step         AgentStep  // 当前轮次步骤
    answerID     string     // 答案事件 ID（流式输出用）
}
```

**字段含义**：
- `isDone=true` → 结束循环
- `isDone=false` → 继续执行工具
- `emptyContent=true` → 空内容，触发重试
- `truncated=true` → 输出被截断，但仍然返回

### 3. 自然停止的判断

```go
// observe.go 425 行
if isNaturalStopFinishReason(response.FinishReason) && len(response.ToolCalls) == 0 {
    // LLM 自然停止 + 没有工具调用 → 最终答案
}
```

**什么是自然停止**：
- `FinishReason == "stop"`：LLM 主动停止
- `FinishReason == "end_turn"`：某些模型的结束标记
- `FinishReason == "complete"`：完成标记

**关键条件**：
- ✅ FinishReason 是自然停止
- ✅ ToolCalls 为空（没有工具调用）
- ✅ 两者都满足才认为是"最终答案"

### 4. 空内容处理

```go
// observe.go 443-450 行
if response.Content == "" {
    return responseVerdict{
        isDone:       true,
        finalAnswer:  "",
        emptyContent: true,  // ← 标记为空内容
        step:         step,
    }
}
```

**为什么 special case**：
- LLM 有时会返回空内容（网络问题、模型抽风）
- 不应该直接结束，而是触发重试
- 在 engine.go 的 929-972 行，会发送 nudge 消息让 LLM 重新回答

**重试逻辑**（engine.go 929-972 行）：
```go
if verdict.emptyContent {
    guards.emptyRetries++
    if guards.emptyRetries <= maxEmptyResponseRetries {
        logger.Warnf(ctx, "[Agent][Round-%d] Empty content with stop - retrying (%d/%d)",
            round, guards.emptyRetries, maxEmptyResponseRetries)
        *messagesPtr = append(*messagesPtr, chat.Message{
            Role:    "user",
            Content: "Please provide your complete answer now as plain text.",
        })
        return iterOutcomeContinue, nil
    }
    // 重试耗尽，使用 fallback
    fallback := stalledAnswerFallback
    state.FinalAnswer = fallback
    state.IsComplete = true
}
```

### 5. 输出截断处理

```go
// observe.go 511-558 行
if isLengthFinishReason(response.FinishReason) && len(response.ToolCalls) == 0 {
    // 避免"从头重写"的死循环
    response.Content = agenttools.StripThinkBlocks(response.Content)
    
    if strings.TrimSpace(response.Content) == "" {
        // 只有思考内容，没有实际答案 → 触发重试
        return responseVerdict{isDone: true, finalAnswer: "", emptyContent: true, step: step}
    }
    
    // 有部分答案 → 返回已有内容，标记 truncated
    step.Truncated = true
    return responseVerdict{
        isDone:      true,
        finalAnswer: response.Content,
        truncated:   true,  // ← 标记为截断
        step:        step,
    }
}
```

**为什么这样处理**：
- 避免"从头重写"的死循环（#3446 bug）
- 如果只有思考内容，没有答案 → 重试
- 如果有部分答案 → 返回已有内容，标记截断

**历史 bug**：
```
原来的逻辑：
  length + 无工具调用 →  fall through → 当作非终态
  → 下一轮从头重写答案 → 又触发 length → 无限循环

修复后：
  length + 无工具调用 → 直接结束（truncated=true）
```

### 6. 流式输出处理

```go
// observe.go 464-479 行
var answerID string
if response.AnswerStreamed && response.AnswerEventID != "" {
    answerID = response.AnswerEventID  // 复用已有的流式事件 ID
} else {
    answerID = generateEventID("answer")
    // 发送完整内容
    e.eventBus.Emit(ctx, event.Event{
        ID:   answerID,
        Type: event.EventAgentFinalAnswer,
        Data: event.AgentFinalAnswerData{
            Content: response.Content,
            Done:    false,
        },
    })
}
```

**为什么 special case**：
- 流式输出时，Token 已经逐个发送给前端
- 不应该再发送一次完整内容（会重复显示）
- 只需要关闭流（Done=true）

**两种情况**：
| 情况 | 处理 |
|---|---|
| **已流式输出** | 复用 answerID，只发送 Done=true |
| **未流式输出** | 发送完整内容 + Done=true |

---

## 📊 Analyze 阶段的输入和输出

### 输入

```go
response *types.ChatResponse {
    Content          string              // LLM 返回的文本
    ToolCalls        []types.LLMToolCall // 工具调用列表
    FinishReason     string              // "stop" / "tool_calls" / "length" / "content_filter"
    AnswerStreamed   bool                // 是否已经流式输出
    AnswerEventID    string              // 流式事件 ID
}
```

### 输出

```go
responseVerdict {
    isDone:       true/false   // 是否结束循环
    finalAnswer:  "..."        // 最终答案
    emptyContent: true/false   // 空内容
    truncated:    true/false   // 截断
    step:         AgentStep    // 当前轮次
    answerID:     "evt_xxx"    // 答案事件 ID
}
```

---

## 🎯 完整判断逻辑

```
LLM 响应
    ↓
FinishReason == "content_filter" && ToolCalls 为空？
    ├─ 是 → 安全过滤，返回安全提示
    └─ 否 ↓
    ↓
isNaturalStopFinishReason(FinishReason) && ToolCalls 为空？
    ├─ 是 → 自然停止
    │   ├─ Content 为空？
    │   │   ├─ 是 → emptyContent=true（触发重试）
    │   │   └─ 否 → 返回最终答案
    │   └─ 流式输出处理
    └─ 否 ↓
    ↓
isLengthFinishReason(FinishReason) && ToolCalls 为空？
    ├─ 是 → 输出截断
    │   ├─ Content 为空（只有思考）？
    │   │   └─ 是 → emptyContent=true（触发重试）
    │   └─ 否 → 返回已有内容（truncated=true）
    └─ 否 ↓
    ↓
默认：有工具调用
    └─ isDone=false（继续执行工具）
```

---

## 💡 Analyze 阶段的核心价值

| 特性 | 价值 |
|---|---|
| **多条件判断** | 区分自然停止、截断、安全过滤 |
| **空内容处理** | 避免空回答，触发重试 |
| **截断处理** | 避免"从头重写"的死循环 |
| **流式输出优化** | 避免重复显示 |
| **安全过滤** | 内容安全兜底 |

---

## 📝 小结

**Analyze 阶段做了什么**：
1. 检查 LLM 响应是否被安全过滤
2. 判断 LLM 是否自然停止（没有工具调用）
3. 处理空内容（触发重试）
4. 处理输出截断（避免死循环）
5. 处理流式输出（避免重复）
6. 返回 verdict（isDone + finalAnswer）

**核心逻辑**：
```
有工具调用？ → 继续执行工具（isDone=false）
无工具调用？ → 检查 FinishReason
  ├─ "stop" → 最终答案（isDone=true）
  ├─ "length" → 截断答案（isDone=true, truncated=true）
  ├─ "content_filter" → 安全提示（isDone=true）
  └─ 空内容 → 触发重试（emptyContent=true）
```

**工程化亮点**：
- 多条件判断，覆盖各种边界情况
- 空内容和截断的特殊处理
- 流式输出优化
- 安全过滤兜底

---

## 🎯 下一步

→ [05 - Act 阶段详解](05-act-stage.md)

---

## 💡 思考题

1. Analyze 阶段的三种结束条件分别是什么？
2. 为什么要 special case 处理空内容？
3. 输出截断的"从头重写"死循环是怎么产生的？如何避免？
