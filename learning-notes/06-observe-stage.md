# 06 - Observe 阶段详解

> ReAct 的第四步：结果回填

---

## 学习目标

- 理解 Observe 阶段的核心流程
- 掌握工具结果回填机制
- 了解图片处理（VLM vs 非 VLM）
- 理解知识库工具的特殊处理（redact）
- 掌握消息格式（OpenAI 标准）

---

## 代码位置

```
文件：internal/agent/observe.go
核心函数：
  - appendToolResults() - 913-968 行（回填工具结果）
  
文件：internal/agent/tool_images.go
核心函数：
  - appendToolImages() - 15-48 行（处理工具图片）
```

---

## Observe 阶段核心流程

```
Observe 阶段 - engine.go 1023-1026 行

*messagesPtr = e.appendToolResults(*messagesPtr, step)
*messagesPtr = e.appendToolImages(ctx, *messagesPtr, step)

输入：
  - messages: 当前对话历史
  - step: 当前轮次的 AgentStep（包含工具执行结果）

输出：
  - messages: 更新后的对话历史（包含工具结果）
```

### 核心流程图

```
┌─────────────────────────────────────────────────────────────┐
│              appendToolResults() 回填工具结果                │
│                                                               │
│  1. 添加 assistant 消息                                       │
│     - Role: "assistant"                                       │
│     - Content: step.Thought（LLM 的思考）                    │
│     - ToolCalls: 工具调用列表                                │
│                                                               │
│  2. 添加 tool 消息                                            │
│     - Role: "tool"                                            │
│     - Content: 工具执行结果                                  │
│     - ToolCallID: 关联到具体的工具调用                       │
│     - Name: 工具名称                                         │
│                                                               │
│  输出：messages 包含完整的工具调用和结果                      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              appendToolImages() 处理工具图片                 │
│                                                               │
│  遍历 step.ToolCalls:                                        │
│    - 检查是否有图片 (call.Result.Images)                     │
│    - 如果模型支持 VLM:                                        │
│        - 添加 user 消息，包含图片                            │
│    - 如果模型不支持 VLM 但有 imageDescriber:                 │
│        - 调用 VLM 描述图片，附加到 tool 消息                │
│    - 否则：添加提示"模型无法查看图片"                        │
│                                                               │
│  输出：messages 包含图片（或图片描述）                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 关键设计点

### 1. 回填 Assistant 消息

```go
// observe.go 921-951 行
if step.Thought != "" || len(step.ToolCalls) > 0 || step.ReasoningContent != "" {
    assistantMsg := chat.Message{
        Role:               "assistant",
        Content:            step.Thought,           // LLM 的思考
        ReasoningContent:   step.ReasoningContent,  // 推理过程
        ReasoningSignature: step.ReasoningSignature,
    }
    
    // 添加工具调用
    if len(step.ToolCalls) > 0 {
        assistantMsg.ToolCalls = make([]chat.ToolCall, 0, len(step.ToolCalls))
        for _, tc := range step.ToolCalls {
            argsJSON, _ := json.Marshal(tc.Args)
            assistantMsg.ToolCalls = append(assistantMsg.ToolCalls, chat.ToolCall{
                ID:       tc.ID,
                Type:     "function",
                Function: chat.FunctionCall{
                    Name:      tc.Name,
                    Arguments: string(argsJSON),
                },
            })
        }
    }
    
    messages = append(messages, assistantMsg)
}
```

**为什么要添加 assistant 消息**：
- LLM 需要看到"我之前说了什么"
- 工具调用需要和结果关联
- 保持对话历史的完整性

**消息格式**（OpenAI 标准）：
```json
{
  "role": "assistant",
  "content": "让我查一下北京的天气...",
  "tool_calls": [
    {
      "id": "call_xxx",
      "type": "function",
      "function": {
        "name": "weather_api",
        "arguments": "{\"location\": \"北京\"}"
      }
    }
  ]
}
```

### 2. 回填 Tool 消息

```go
// observe.go 953-965 行
for _, toolCall := range step.ToolCalls {
    resultContent := e.modelContext.ModelToolResultForTool(toolCall.Name, toolCall.Result)
    
    toolMsg := chat.Message{
        Role:       "tool",
        Content:    resultContent,      // 工具执行结果
        ToolCallID: toolCall.ID,        // 关联到 assistant 的 tool_call
        Name:       toolCall.Name,      // 工具名称
    }
    
    messages = append(messages, toolMsg)
}
```

**为什么要添加 tool 消息**：
- LLM 需要看到"工具返回了什么"
- 通过 ToolCallID 关联到具体的工具调用
- 下一轮 Think 可以基于工具结果继续推理

**消息格式**（OpenAI 标准）：
```json
{
  "role": "tool",
  "content": "晴天，25°C",
  "tool_call_id": "call_xxx",
  "name": "weather_api"
}
```

### 3. 图片处理（VLM vs 非 VLM）

```go
// tool_images.go 18-46 行
for _, call := range step.ToolCalls {
    if call.Result == nil || !call.Result.Success || len(call.Result.Images) == 0 {
        continue
    }
    
    // 情况 1：模型支持 VLM（视觉语言模型）
    if e.config != nil && e.config.ChatModelSupportsVision {
        messages = append(messages, chat.Message{
            Role:    "user",
            Images:  call.Result.Images,  // 直接传入图片
            Content: "Images returned by tool ...",
        })
        continue
    }
    
    // 情况 2：模型不支持 VLM，但有 imageDescriber
    if e.imageDescriber != nil {
        descriptions := e.describeImages(imageCtx, call.Result.Images)
        note := "Tool image descriptions:\n" + strings.Join(descriptions, "\n")
        // 附加到 tool 消息
        messages[i].Content += "\n" + note
    }
    
    // 情况 3：模型不支持 VLM，也没有 imageDescriber
    note := "Images were captured, but this model cannot view them..."
    messages[i].Content += "\n" + note
}
```

**三种情况**：
| 情况 | 处理方式 | 适用场景 |
|---|---|---|
| **模型支持 VLM** | 直接传图片 | GPT-4V、Claude 3 等 |
| **不支持 VLM 但有 imageDescriber** | 调用 VLM 描述图片 | 非 VLM 模型 + VLM 服务 |
| **都不支持** | 提示"无法查看图片" | 纯文本模型 |

### 4. 知识库工具结果的特殊处理

```go
// observe.go 979-1012 行
var kbToolNames = map[string]bool{
    "search_knowledge":      true,
    "read_document":         true,
    "list_documents":        true,
    "query_knowledge_graph": true,
    // ...
}

func redactHistoryKBResults(llmContext []chat.Message) []chat.Message {
    for _, msg := range llmContext {
        if msg.Role == "tool" && kbToolNames[msg.Name] {
            // 替换为标记，强制重新检索
            redacted = append(redacted, chat.Message{
                Role:    msg.Role,
                Content: "[Previous retrieval result omitted — knowledge base may have changed. Please perform a fresh search.]",
            })
        }
    }
}
```

**为什么要 redact**：
- 知识库可能被修改、切换、删除
- 历史检索结果可能过时
- 强制 LLM 重新检索，避免使用过时信息

---

## Observe 阶段的完整示例

**场景**：用户问"北京天气怎么样？"

### 第 1 轮 Think 后

```
LLM 输出：
  Content: "让我查一下北京的天气..."
  ToolCalls: [{Function: "weather_api", Arguments: {"location": "北京"}}]
```

### 第 1 轮 Act 后

```
step.ToolCalls: [{
    ID: "call_xxx",
    Name: "weather_api",
    Args: {"location": "北京"},
    Result: {
        Success: true,
        Output: "晴天，25°C"
    }
}]
```

### 第 1 轮 Observe 后

```
messages 新增：

{
  "role": "assistant",
  "content": "让我查一下北京的天气...",
  "tool_calls": [{
    "id": "call_xxx",
    "function": {
      "name": "weather_api",
      "arguments": "{\"location\": \"北京\"}"
    }
  }]
}

{
  "role": "tool",
  "content": "晴天，25°C",
  "tool_call_id": "call_xxx",
  "name": "weather_api"
}
```

### 第 2 轮 Think

```
LLM 看到：
  - 之前说了"让我查一下天气"
  - 工具返回了"晴天，25°C"
  
LLM 推理：
  - 我有足够信息了
  - 直接回答用户
  
LLM 输出：
  Content: "北京明天是晴天，温度 25°C"
  ToolCalls: []
  FinishReason: "stop"
```

---

## Observe 阶段的核心价值

| 特性 | 价值 |
|---|---|
| **回填 assistant 消息** | 保持对话历史完整，LLM 能看到自己之前的思考 |
| **回填 tool 消息** | LLM 能看到工具结果，基于结果继续推理 |
| **ToolCallID 关联** | 工具结果和调用精确关联 |
| **图片处理** | 支持 VLM、非 VLM、图片描述三种模式 |
| **知识库 redact** | 避免使用过时的检索结果 |

---

## 小结

**Observe 阶段做了什么**：
1. 回填 assistant 消息（Thought + ToolCalls）
2. 回填 tool 消息（工具结果）
3. 处理工具图片（VLM / 图片描述 / 提示）
4. 特殊处理知识库工具结果（redact）

**核心流程**：
```
Act 阶段执行工具
    ↓
appendToolResults()
    - 添加 assistant 消息（Thought + ToolCalls）
    - 添加 tool 消息（工具结果）
    ↓
appendToolImages()
    - VLM 模型：直接传图片
    - 非 VLM + imageDescriber：描述图片
    - 纯文本：提示无法查看
    ↓
下一轮 Think 可以看到工具结果
```

**关键设计**：
- **消息格式遵循 OpenAI 标准**（assistant + tool）
- **ToolCallID 关联**确保结果和调用对应
- **图片处理灵活**支持多种模型能力
- **知识库 redact**避免过时信息

---

## 下一步

→ [07 - 会话管理和历史记录](07-session-management.md)

---

## 思考题

1. 为什么要同时添加 assistant 和 tool 两种消息？
2. ToolCallID 的作用是什么？
3. 为什么要对知识库工具结果进行 redact？
4. VLM 模型和非 VLM 模型处理图片的差异是什么？
