# WeKnora 架构学习笔记

> 基于 WeKnora v0.8.0 的 AI Agent 架构深度学习
> 学习时间：2026-09-24
> 学习重点：单引擎 ReAct 循环

---

## 📚 目录

### 基础篇

1. [项目启动和配置](01-project-setup.md)
   - 环境准备
   - Docker Compose 启动
   - LLM 配置
   - SSRF 白名单

2. [ReAct 循环概述](02-react-overview.md)
   - 什么是 ReAct
   - WeKnora 的单引擎设计
   - 状态机（iterOutcome）
   - 四阶段流程

### 核心篇：ReAct 四阶段

3. [Think 阶段详解](03-think-stage.md)
   - LLM 调用机制
   - 流式响应处理
   - 瞬态错误重试
   - 上下文溢出恢复
   - 优雅降级

4. [Analyze 阶段详解](04-analyze-stage.md)
   - 判断逻辑
   - 三种结束条件
   - 空内容处理
   - 输出截断处理

5. [Act 阶段详解](05-act-stage.md)
   - 串行/并行执行
   - JSON 参数修复
   - 超时控制
   - 截断检测
   - Langfuse 追踪

6. [Observe 阶段详解](06-observe-stage.md)
   - 结果回填机制
   - 图片处理（VLM）
   - 知识库 redact
   - 消息格式

### 进阶篇

7. [会话管理和历史记录](07-session-management.md)
   - Session 机制
   - 历史加载流程
   - Token 预算管理
   - Checkpoint 压缩

8. [状态机设计](08-state-machine.md)
   - iterOutcome 三种状态
   - Next vs Continue 的区别
   - 工程化价值

---

## 🎯 学习路径

```
基础篇（环境 + 概念）
    ↓
核心篇（ReAct 四阶段）
    ↓
进阶篇（会话管理 + 状态机）
```

## 📖 如何使用本笔记

1. **按顺序阅读**：从 01 到 08，循序渐进
2. **结合代码**：每个阶段都标注了对应的源码位置
3. **动手实践**：在 Web UI 上实际体验，观察日志
4. **提问讨论**：每个文档末尾都有思考题

## 🔗 相关资源

- **项目地址**：https://github.com/Tencent/WeKnora
- **官方文档**：`website-docs/`
- **架构全景图**：`LEARNING_GUIDE.md`（项目根目录）

---

> 💡 **学习建议**：先通读一遍建立全局观，再逐阶段深入代码细节。
