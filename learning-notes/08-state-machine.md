# 08 - 状态机设计

> 理解 WeKnora ReAct 循环的控制流设计

---

## 学习目标

- 理解状态机的基本概念
- 掌握 iterOutcome 的三种状态
- 理解 Next vs Continue 的区别
- 了解状态机的工程化价值

---

## 代码位置

```
文件：internal/agent/engine.go
核心定义：
  - iterOutcome 枚举 - 666-679 行
  - executeLoop 状态转移 - 643-650 行
```

---

## 什么是状态机？

**状态机 = 状态 + 转移规则**

想象你在玩一个桌游：
```
[起点] → 掷骰子 → [移动] → 抽卡 → [执行效果] → 结束？
   ↑                                              │
   └──────────── 没结束，继续 ────────────────────┘
```

每一步都是一个**状态**，规则告诉你下一步去哪里。这就是状态机。

---

## WeKnora 的状态机

### iterOutcome 定义

```go
// engine.go 666-679 行
type iterOutcome int

const (
    iterOutcomeNext     iterOutcome = iota  // 推进轮次，继续
    iterOutcomeContinue                     // 重试当前轮次（空内容）
    iterOutcomeBreak                        // 退出循环（最终答案）
)
```

### 三种状态的含义

| 状态 | 含义 | 轮次计数 | 场景 |
|---|---|---|---|
| **Next** | 这一轮做完了，进入下一轮 | ✅ +1 | 正常执行、工具调用完成 |
| **Continue** | 这一轮没做好，重试当前轮 | ❌ 不变 | 空内容、可恢复的错误 |
| **Break** | 结束了，退出循环 | - | 最终答案、死循环检测触发 |

---

## 状态机在主循环中的应用

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

---

## 为什么要分 Next 和 Continue？

### 核心区别：轮次计数器

```go
switch outcome {
case iterOutcomeNext:
    state.CurrentRound++  // ← 轮次 +1
    continue loop
case iterOutcomeContinue:
    continue loop          // ← 轮次不变
}
```

### 场景：LLM 返回空内容

```
用户问："北京天气怎么样？"

第 1 轮：
  Think → LLM 返回 "" (空内容，可能是网络问题或模型抽风)
  Analyze → 检测到空内容
  怎么办？→ 重试！

第 2 轮：
  Think → LLM 返回 "北京今天晴，25°C"
  Act → 没有工具调用
  Observe → 完成
```

**问题**：这算 1 轮还是 2 轮？

### ❌ 如果用 Next（都算一轮）

```
用户配置 MaxIterations = 3

实际执行：
  轮次 1：空内容 → round++ → round=2
  轮次 2：空内容 → round++ → round=3
  轮次 3：空内容 → round++ → round=4 → 超过 MaxIterations → 停止！

结果：3 次全是空内容，用户一个问题都没解决
```

### ✅ 用 Continue（重试不算一轮）

```
用户配置 MaxIterations = 3

实际执行：
  轮次 1：空内容 → round 不变 → round=1（重试）
  轮次 1：空内容 → round 不变 → round=1（重试）
  轮次 1：正常回答 → round++ → round=2 → 完成

结果：1 轮成功，还有 2 轮预算
```

---

## 语义差异

| 状态 | 含义 | 轮次计数 | 场景 |
|---|---|---|---|
| **Next** | "这一轮做完了，进入下一轮" | ✅ +1 | 正常执行、工具调用完成 |
| **Continue** | "这一轮没做好，重试当前轮" | ❌ 不变 | 空内容、可恢复的错误 |
| **Break** | "结束了，退出循环" | - | 最终答案、死循环检测触发 |

---

## 类比：考试

想象你在考试，老师给你 **3 次机会**回答问题：

```
机会 1：你说"嗯..."（没回答出来）
  → 老师说："再给你一次机会"（不计入 3 次）
  
机会 1（重试）：你说"答案是 42"
  → 老师说："对了，下一题"（计入 1 次）
  
机会 2：你说"..."
  → 老师说："再给你一次机会"（不计入）
```

**关键**：重试是"给额外机会"，不应该消耗你的 3 次预算。

---

## 代码里的实际使用

### Next 的场景（正常推进）

```go
// engine.go 1034 行 - 工具执行完毕，进入下一轮
return iterOutcomeNext, nil

// engine.go 997 行 - Steer 消息注入后，继续执行
return iterOutcomeNext, nil
```

### Continue 的场景（重试当前轮）

```go
// engine.go 939 行 - 空内容，重试
*messagesPtr = append(*messagesPtr, chat.Message{
    Role:    "user",
    Content: "Please provide your complete answer now as plain text.",
})
return iterOutcomeContinue, nil
```

---

## 如果不区分会怎样？

假设只有 `Next` 和 `Break`，没有 `Continue`：

```go
// 空内容时
if resp.Content == "" {
    retryCount++
    if retryCount < 3 {
        // 怎么办？
        // 选项 1: continue loop → 但 round 已经 ++ 了
        // 选项 2: 不增加 round → 但代码逻辑复杂
    }
}
```

**问题**：
- 需要在多个地方判断"是否重试"
- round 计数逻辑变得复杂
- 难以追踪"真正执行了多少轮"

---

## 工程价值

| 维度 | 有 Continue | 无 Continue |
|---|---|---|
| **MaxIterations 语义** | ✅ "最多 N 次有效尝试" | ❌ "最多 N 次调用（含重试）" |
| **日志可读性** | ✅ round=1 (retry 2) | ❌ round=3 (实际只做了 1 轮) |
| **兜底机制** | ✅ 基于有效轮次计算 | ❌ 基于总调用次数计算 |
| **用户体验** | ✅ 重试不消耗预算 | ❌ 重试浪费预算 |

---

## 状态机 vs 裸控制流

### ❌ 朴素实现（裸的 break/continue）

```go
for round < maxIterations {
    resp := callLLM()
    
    if resp.HasToolCalls() {
        executeTools()
        continue  // ← 继续
    }
    
    if resp.Content == "" {
        retryCount++
        if retryCount < 3 {
            continue  // ← 重试
        }
    }
    
    // 最终答案
    break  // ← 结束
}
```

**问题**：
- `continue` 和 `break` 散落在各处，逻辑分散
- 新增情况（如"中途注入消息"）时，代码越来越乱
- 难以追踪"当前处于什么状态"

### ✅ 状态机实现（WeKnora）

```go
// 1. 定义状态（3 种结果）
type iterOutcome int

const (
    iterOutcomeNext     iterOutcome = iota  // 继续下一轮
    iterOutcomeContinue                     // 重试当前轮
    iterOutcomeBreak                        // 结束循环
)

// 2. 单步执行，返回状态
outcome := runReActIteration()

// 3. 状态机处理（集中在一处）
switch outcome {
case iterOutcomeNext:
    round++
    continue loop
case iterOutcomeContinue:
    continue loop  // 不增加 round
case iterOutcomeBreak:
    break loop
}
```

**优势**：
- ✅ 状态集中在一处处理（switch）
- ✅ 新增状态只需加一个 case
- ✅ 逻辑清晰，易于调试

### 对比表

| 维度 | 状态机 | 裸 break/continue |
|---|---|---|
| **可读性** | ✅ 状态清晰，逻辑集中 | ❌ 散落各处，难追踪 |
| **可扩展性** | ✅ 加一个 case 即可 | ❌ 需要改多处 |
| **调试** | ✅ 打印状态即可 | ❌ 需要加很多日志 |
| **测试** | ✅ 每个状态独立测试 | ❌ 难以隔离 |
| **复杂度** | ⚠️ 多一层抽象 | ✅ 简单直接 |

**取舍**：
- **小项目**：裸控制流够了
- **生产级 Agent**：状态机必要（WeKnora 有 80+ 工具、多种兜底逻辑）

---

## 类比：红绿灯状态机

```
[红灯] → 等待 → [绿灯] → 通行 → [黄灯] → 减速 → [红灯]
   ↑                                              │
   └──────────────────────────────────────────────┘
```

- **状态**：红灯、绿灯、黄灯
- **转移规则**：时间到了就切换
- **行为**：每个状态对应不同的动作（停/走/减速）

WeKnora 的 ReAct 循环类似：
- **状态**：Next、Continue、Break
- **转移规则**：LLM 返回什么决定下一步
- **行为**：每个状态对应不同的循环控制

---

## 小结

### 状态机的核心价值

1. **显式表达**：把"当前在干什么"变成代码里的显式概念
2. **集中控制**：所有状态转移逻辑在一处，不散落
3. **易于扩展**：新增情况只需加一个状态 + 一个 case

### WeKnora 的实践

- 用 `iterOutcome` 枚举表达 3 种状态
- `runReActIteration` 返回状态，而不是直接 break/continue
- 主循环的 switch 集中处理所有状态转移

### 为什么分 Next 和 Continue

1. **语义清晰**：
   - Next = "这一轮做完了"
   - Continue = "这一轮没做好，重来"

2. **预算公平**：
   - 重试不应该消耗 MaxIterations 预算
   - 用户配置的是"有效轮次"，不是"总调用次数"

3. **工程简洁**：
   - 状态机集中处理，不散落
   - round 计数器逻辑统一

**本质区别**：
- **Next** 是"推进"（progress）
- **Continue** 是"重试"（retry）

---

## 思考题

1. 状态机的三种状态分别对应什么场景？
2. 为什么要区分 Next 和 Continue？如果不区分会怎样？
3. 状态机模式相比裸 break/continue 有什么优势？
4. 在什么场景下，你会选择状态机模式？

---

## 延伸阅读

- **状态机模式**：https://en.wikipedia.org/wiki/Finite-state_machine
- **ReAct 论文**：https://arxiv.org/abs/2210.03629
- **WeKnora GitHub**：https://github.com/Tencent/WeKnora

---

> 恭喜你完成了 WeKnora 架构学习笔记的全部内容！
> 
> 接下来可以：
> - 回到 [00-overview.md](00-overview.md) 复习
> - 深入代码，结合笔记阅读源码
> - 在 Web UI 上实际体验，观察日志
