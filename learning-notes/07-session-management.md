# 07 - 会话管理和历史记录

> 理解 WeKnora 的会话持久化和历史加载机制

---

## 学习目标

- 理解 Session 的概念和作用
- 掌握历史记录的加载流程
- 了解 Token 预算管理
- 理解 Checkpoint 压缩机制
- 掌握无状态引擎设计

---

## 代码位置

```
核心文件：
  - internal/application/service/agent_history.go
      LoadAgentHistory() - 72-150 行
  
  - internal/types/
      session.go, message.go - Session 和 Message 模型
```

---

## Session 的概念

### 什么是 Session？

**Session = 一次对话的容器**

```go
type Session struct {
    ID        string    // session_id (UUID)
    AgentID   string    // 关联的 Agent
    TenantID  string    // 租户 ID
    CreatedAt time.Time
}

type Message struct {
    ID          string  // message_id
    SessionID   string  // 关联到 session
    Role        string  // user / assistant / tool / system
    Content     string  // 消息内容
    AgentSteps  []Step  // ReAct 步骤（JSON）
    ToolCalls   []Call  // 工具调用（JSON）
}
```

### Session 的作用

- 一个 Session 对应一次对话（用户可以开多个 Session）
- 所有消息都属于某个 Session
- Session 持久化在数据库（不是内存）

---

## 数据库结构

```
┌─────────────────────────────────────────────────────────────┐
│                    数据库（Single Source of Truth）           │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ sessions 表                                            │  │
│  │  ├─ id (session_id)                                    │  │
│  │  ├─ agent_id                                           │  │
│  │  ├─ tenant_id                                          │  │
│  │  └─ created_at, updated_at                             │  │
│  └───────────────────────────────────────────────────────┘  │
│                          ↓                                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ messages 表                                            │  │
│  │  ├─ id (message_id)                                    │  │
│  │  ├─ session_id 关联到 session                          │  │
│  │  ├─ role (user/assistant/tool/system)                  │  │
│  │  ├─ content (消息内容)                                 │  │
│  │  ├─ agent_steps (JSON - ReAct 步骤)                    │  │
│  │  ├─ tool_calls (JSON - 工具调用)                       │  │
│  │  └─ created_at, updated_at                             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 历史记录加载流程

### 核心函数：LoadAgentHistory

```go
// agent_history.go 72-150 行
func LoadAgentHistory(
    ctx context.Context,
    messageRepo interfaces.MessageRepository,
    sessionID string,         // 当前 session
    tokenBudget int,          // Token 预算（如 100k）
    retainRetrievalHistory bool,
) ([]chat.Message, float64, error) {
    // 1. 从数据库分页查询（每页 200 条）
    page, err := messageRepo.ListMessagesBySessionBeforeCursor(
        ctx, sessionID, before, beforeID, agentHistoryPageSize,
    )
    
    // 2. 估算 Token，直到达到预算
    used = estimator.EstimateMessages(out)
    if used >= tokenBudget {
        break
    }
    
    // 3. 返回历史消息列表
    return out, scale, nil
}
```

### 加载流程图

```
用户打开页面
    ↓
前端请求 /api/sessions/{session_id}/messages
    ↓
后端从 messages 表查询历史
    ↓
LoadAgentHistory() 加载历史（按 Token 预算）
    ↓
AgentEngine.Execute(llmContext=历史消息)
    ↓
buildMessagesWithLLMContext() 构建完整消息
    ↓
ReAct 循环（Think → Act → Observe）
    ↓
LLM 返回最终答案
    ↓
保存新的 Message 到 messages 表
    ↓
返回给前端显示
```

---

## 为什么从数据库加载？

**核心设计**：无状态引擎

```go
// engine.go 28-34 行注释
// History persistence note: the engine is stateless across turns.
// Conversation history is rebuilt from the DB once per turn by the caller
// (see service.LoadAgentHistory) and passed into Execute as llmContext.
```

**优势**：
- ✅ **无状态引擎**：AgentEngine 不缓存历史，重启不丢数据
- ✅ **多实例部署**：多个后端实例共享同一个数据库
- ✅ **持久化**：用户刷新页面，历史还在

---

## Token 预算管理

### 按 Token 预算加载

```
用户配置：tokenBudget = 100,000 (100k)

Session 历史：
  Turn 1: 5k tokens
  Turn 2: 8k tokens
  Turn 3: 12k tokens
  Turn 4: 15k tokens
  Turn 5: 20k tokens
  Turn 6: 25k tokens
  Turn 7: 30k tokens  ← 累计 115k，超过预算

加载结果：
  ✓ Turn 1-6 (85k tokens)
  ✗ Turn 7 (被截断，不加载)
```

### 为什么按 Token 而不是轮次？

- 不同轮次的 Token 数差异很大（有的带工具调用，有的只是闲聊）
- Token 预算直接对应 LLM 的上下文窗口
- 避免"加载了 10 轮但 Token 爆炸"的问题

---

## Checkpoint 机制（上下文压缩）

### 什么是 Checkpoint？

**长对话的压缩摘要**

```go
// agent_history.go 87-91 行
checkpoint := loadContextCheckpoint(ctx, messageRepo, sessionID)
if checkpoint != nil {
    out = append(out, compaction.SummaryMessage(checkpoint.ContextCheckpoint.Summary))
}
```

### 场景

对话太长，历史超过 Token 预算

### 解决

1. 早期历史被压缩成摘要（checkpoint）
2. 摘要保存在数据库
3. 下次加载时，先加载摘要，再加载最近的轮次

### 压缩示例

```
原始历史：
  Turn 1-10: 50k tokens
  Turn 11-20: 60k tokens
  
压缩后：
  Checkpoint: "用户问了天气，Agent 调用了 weather_api..." (2k tokens)
  Turn 11-20: 60k tokens
  
总 Token: 62k（节省了 48k）
```

---

## 消息构建流程

### 从历史到 LLM 输入

```go
// engine.go 370 行
messages := e.buildMessagesWithLLMContext(systemPrompt, query, sessionID, llmContext, imgs)
```

### 构建过程

```
┌─────────────────────────────────────┐
│ 1. System Prompt                    │ ← 系统提示词
│    "你是一个智能助手，可以..."       │
├─────────────────────────────────────┤
│ 2. Checkpoint (如果有)              │ ← 压缩摘要
│    "之前的对话中，用户问了..."       │
├─────────────────────────────────────┤
│ 3. 历史 Turn 1                      │
│    User: "北京天气怎么样？"          │
│    Assistant: [Thought + ToolCall]   │
│    Tool: "晴天，25°C"               │
│    Assistant: "北京今天晴天..."      │
├─────────────────────────────────────┤
│ 4. 历史 Turn 2                      │
│    User: "上海呢？"                  │
│    Assistant: "上海多云，22°C"       │
├─────────────────────────────────────┤
│ 5. 当前查询                         │
│    User: "深圳呢？"                  │ ← 当前问题
└─────────────────────────────────────┘
```

---

## 完整流程：一次对话的生命周期

```
用户打开页面
    ↓
前端请求 /api/sessions/{session_id}/messages
    ↓
后端从 messages 表查询历史
    ↓
LoadAgentHistory() 加载历史（按 Token 预算）
    ↓
AgentEngine.Execute(llmContext=历史消息)
    ↓
buildMessagesWithLLMContext() 构建完整消息
    ↓
ReAct 循环（Think → Act → Observe）
    ↓
LLM 返回最终答案
    ↓
保存新的 Message 到 messages 表
    ↓
返回给前端显示
```

---

## 小结

### Session 是什么？

- 每个会话有一个 session_id（UUID）
- 所有消息都关联到这个 session
- Session 持久化在数据库，用户可以继续之前的对话

### 如何记录之前的对话？

**数据库 + 按需加载**：
- 每条消息保存在 messages 表（包含 session_id）
- 每次对话前，LoadAgentHistory() 从数据库加载历史
- 按 Token 预算截断（不是按轮次）
- 长对话通过 Checkpoint 机制压缩

**核心优势**：
- ✅ 无状态引擎（重启不丢数据）
- ✅ 多实例部署（共享数据库）
- ✅ Token 预算精确控制
- ✅ 长对话自动压缩

---

## 下一步

→ [08 - 状态机设计](08-state-machine.md)

---

## 思考题

1. 为什么 AgentEngine 要设计成无状态的？
2. Token 预算管理的作用是什么？
3. Checkpoint 压缩是怎么工作的？
4. 多实例部署时，会话管理需要注意什么？
