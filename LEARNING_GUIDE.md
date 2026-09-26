# WeKnora 架构学习指南

> 本文档面向希望通过 WeKnora 项目系统学习 AI Agent 架构的开发者。
> 项目地址：https://github.com/Tencent/WeKnora
> 版本基线：v0.8.0 | 语言：Go 1.26 | 协议：MIT

---

## 目录

- [一、项目定位与全局观](#一项目定位与全局观)
- [二、架构全景图](#二架构全景图)
- [三、七大核心设计深度解析](#三七大核心设计深度解析)
- [四、与主流框架深度对比](#四与主流框架深度对比)
- [五、分层学习路径（4 周计划）](#五分层学习路径4-周计划)
- [六、关键代码导读](#六关键代码导读)
- [七、实战练习](#七实战练习)
- [八、反常识设计点](#八反常识设计点)
- [九、延伸阅读与社区资源](#九延伸阅读与社区资源)

---

## 一、项目定位与全局观

### 1.1 WeKnora 是什么

WeKnora 是腾讯开源的 **企业级 RAG + Agent + Auto-Wiki 知识框架**。它不是玩具 demo，而是真实在腾讯内部大规模使用的产品。

核心能力矩阵：

| 能力维度 | 具体特性 |
|---|---|
| **RAG** | 多向量库（Milvus/Qdrant/Weaviate/pgvector）、多路召回、Rerank、MMR、知识图谱 |
| **Agent** | ReAct 引擎、80+ 内置工具、Skill 系统、MCP 协议双向支持 |
| **Auto-Wiki** | Agent 自动维护的知识库，可持续沉淀和演化 |
| **企业治理** | RBAC、多租户、API Key Scope、Approval Gate、审计追踪 |
| **多模态** | VLM 图片理解、ASR 语音识别、浏览器自动化 |
| **全渠道** | Web / 微信小程序 / 嵌入 Widget / IM（飞书、企微、Slack、Telegram 等 9 个） |

### 1.2 技术栈

```
语言：Go 1.26
HTTP：Gin
gRPC：connectrpc（文档解析服务）
前端：Vue / React（frontend/）
CLI：Cobra（cli/cmd/）
数据库：MySQL / SQLite（双轨迁移）
向量库：Milvus / Qdrant / Weaviate / pgvector / OpenSearch / Tencent VectorDB
对象存储：S3 / MinIO / COS / TOS / OSS / KS3 / OBS
可观测：Langfuse
```

### 1.3 顶层目录结构

```
WeKnora/
├── cmd/
│   ├── server/         # HTTP 服务入口（main / bootstrap / listen / signals）
│   ├── desktop/        # 桌面端
│   ├── download/       # 下载器
│   └── milvus-migrate/ # Milvus 数据迁移
├── cli/                # weknora CLI（Cobra 子命令：agent/chat/kb/mcp/skills/...）
├── internal/
│   ├── agent/          # ★ Agent 引擎核心（engine + tools + skills + compaction）
│   ├── mcp/            # ★ MCP Client（OAuth / SSRF 防护 / 多 server 管理）
│   ├── mcpserver/      # ★ MCP Server（对外暴露 WeKnora 能力）
│   ├── models/         # LLM/Embedding/Rerank/VLM/ASR 多厂商抽象
│   ├── application/    # 领域服务层（agent_service / chat_pipeline / evaluation）
│   ├── handler/        # HTTP handlers
│   ├── types/          # 核心领域模型（agent.go / memory.go / tenant.go 等）
│   ├── runtime/        # 运行期编排（server / startup / container）
│   ├── sandbox/        # 沙箱运行时（Docker / E2B / Cube）
│   ├── tracing/        # Langfuse 可观测性
│   └── infrastructure/ # 基础设施适配
├── docreader/          # 独立文档解析 gRPC 服务（parser/splitter/models/client/proto）
├── mcp-server/         # 独立 MCP Server（Python 侧，对应 tencent-weknora-mcp PyPI 包）
├── frontend/           # Web UI
├── miniprogram/        # 微信小程序
├── migrations/         # MySQL / SQLite / ParadeDB / versioned SQL
├── config/             # prompt_templates 等
├── examples/           # 示例（mcp-demo / skills）
├── website-docs/       # VitePress 文档站
├── helm/ docker/ deploy/ scripts/  # 部署与运维
└── packages/dsh-weknora # DeepSeek 官方 Harness 插件（npm）
```

---

## 二、架构全景图

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     Access Layer（接入层）                    │
│  Web UI │ MiniProgram │ Embed Widget │ IM Channels │ CLI    │
│         │             │              │(WeCom/Feishu/│        │
│         │             │              │ Slack/TG/...) │        │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP / WebSocket / gRPC
┌────────────────────────────▼────────────────────────────────┐
│                    Handler Layer（HTTP 处理层）               │
│  knowledge │ message │ mcp │ sandbox │ skill │ memory │ wiki │
│            │         │     │         │       │        │      │
│            custom_agent │ rbac │ evaluation │ embedding       │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                Application Layer（领域服务层）                 │
│  agent_service  │  chat_pipeline  │  custom_agent            │
│  datasource     │  evaluation     │  agent_history           │
│  repository     │  access         │                          │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                 Agent Engine Layer（★ 核心）                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │            AgentEngine.Execute()                     │   │
│  │         ┌──────────────────────────┐                 │   │
│  │         │  executeLoop (ReAct 主循环)│                │   │
│  │         │    ├─ runReActIteration   │                 │   │
│  │         │    │    ├─ Think (think.go)   │             │   │
│  │         │    │    ├─ Analyze          │               │   │
│  │         │    │    ├─ Act   (act.go)   │               │   │
│  │         │    │    └─ Observe          │               │   │
│  │         │    ├─ iterOutcome 状态机     │               │   │
│  │         │    └─ loopGuards 兜底        │               │   │
│  │         └──────────────────────────┘                 │   │
│  │                                                      │   │
│  │  ToolRegistry ──► Tool (80+ 实现)                     │   │
│  │  SkillManager ──► Skill (声明式剧本)                  │   │
│  │  MCPManager   ──► MCP Tools (外部能力)                │   │
│  │  Compactor    ──► Context 压缩                        │   │
│  │  Memory       ──► 分层记忆 (Resident + 召回)          │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                  Provider Layer（能力提供商层）                │
│  LLM │ Embedding │ Rerank │ VLM │ ASR │ WebSearch │ Storage │
│  (vendors/ catalog/ parity/ limiter 多厂商可插拔)            │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                Infrastructure Layer（基础设施层）              │
│  MySQL/SQLite │ Milvus/Qdrant/... │ S3/MinIO/COS/...        │
│  Langfuse     │ Redis             │ Chromedp (浏览器)        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 一次对话的完整数据流

```
用户输入 → Handler
   │
   ├─► AgentService.CreateSession / AppendMessage
   │
   ├─► AgentEngine.Execute()
   │     │
   │     ├─► System Prompt 组装（prompts.go + memory resident 注入）
   │     ├─► ToolRegistry.GetModelFunctionDefinitions() → 工具 schema
   │     │
   │     └─► executeLoop:
   │           │
   │           ├─► LLM 调用（provider catalog → vendor → HTTP）
   │           │
   │           ├─► 若返回 ToolCall:
   │           │     ├─► ToolCallTarget 解析（保留模型原始调用）
   │           │     ├─► Registry.ExecuteTool（含 deferred 懒加载）
   │           │     ├─► ToolResult 回填到 AgentStep
   │           │     └─► Langfuse span 记录
   │           │
   │           ├─► 若返回 Text:
   │           │     ├─► iterOutcome 判断是否终止
   │           │     └─► 流式推送给 Handler → 用户
   │           │
   │           └─► loopGuards 检查（迭代预算 / 循环检测 / token 预算）
   │
   ├─► Memory 提取（异步 / 同步）→ MemoryItem 写入
   │
   └─► History 持久化 + 响应返回
```


## 三、七大核心设计深度解析

### 3.1 单引擎 ReAct + 动态能力作用域

#### 设计要点

整个系统只有一个 `AgentEngine` 实例在跑 ReAct 主循环。它不像 CrewAI 那样有多个 Agent 互相对话，而是通过**工具/技能/MCP 作用域的动态切换**来实现不同场景的能力适配。

关键代码位置：
- `internal/agent/engine.go` — `AgentEngine` 主结构
- `internal/agent/engine.go#Execute` — 入口
- `internal/agent/engine.go#executeLoop` — ReAct 主循环
- `internal/agent/engine.go#runReActIteration` — 单步 think→analyze→act→observe

#### 为什么这样设计

多 Agent DAG 的优势是任务分解清晰，但代价是：
1. **状态爆炸**：N 个 Agent 的组合状态是指数级
2. **调试困难**：消息在 Agent 间传递，trace 很难拼起来
3. **延迟不可控**：Agent 间多轮对话的 token 消耗难预测

单引擎 + 作用域的取舍：
- ✅ 状态收敛到一个 Agent + 工具执行历史
- ✅ 可观测性天然统一（Langfuse 一条 trace 搞定）
- ✅ 通过 `@mention` 路由（`SetPinnedMentions` / `SetPinnedSkills`）动态调整能力范围
- ⚠️ 复杂多步任务靠 LLM 自己规划，没有显式 DAG

#### 适用场景

- ✅ 客服/问答/知识检索类（90% 的企业场景）
- ✅ 单用户对话式任务
- ⚠️ 需要严格多角色协作的复杂工作流（这种场景 CrewAI/LangGraph 更合适）

---

### 3.2 Tool 三层一致性抽象

#### 三层结构

```
┌──────────────────────────────────────────────────────┐
│  Layer 1: types.Tool（接口契约）                      │
│  ─────────────────────────────────                    │
│  type Tool interface {                                │
│      Name() string                                    │
│      Description() string                             │
│      Parameters() json.RawMessage  // JSON Schema     │
│      Execute(ctx, args) (*ToolResult, error)          │
│  }                                                    │
│  + Cleanable interface { Cleanup(ctx) }  // 可选      │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Layer 2: ToolRegistry（注册 + 调度）                  │
│  ─────────────────────────────────                    │
│  • RegisterTool / RegisterDeferredTool                │
│    - Deferred: schema 按需加载，启动零成本             │
│    - First-wins: 重复注册取第一个，避免歧义             │
│  • GetFunctionDefinitions → 给 LLM 的 schema 列表     │
│  • ExecuteTool → 查表 + 执行 + 最大输出截断            │
│  • ToolExecutor 接口解耦执行                           │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Layer 3: FunctionDefinition（LLM 视角）              │
│  ─────────────────────────────────                    │
│  输出给 LLM 的 OpenAI 兼容 function calling schema    │
│  ToolCallTarget 保留模型原始调用，便于重放/审计         │
└──────────────────────────────────────────────────────┘
```

#### 关键设计亮点

1. **Deferred 注册**：80+ 工具不需要启动时全部加载 schema，第一次用到才解析。启动时间从 O(N) 降到 O(1)
2. **First-wins 策略**：MCP 工具和内置工具同名时，先注册的赢。避免了"后注册的覆盖前面的"这种隐蔽 bug
3. **MCP 透明适配**：外部 MCP 工具被 `mcp_tool` 适配成普通 `types.Tool`，LLM 视角完全一致。这是 MCP 协议落地的最佳实践
4. **ToolCallTarget 解耦**：保留模型原始调用 vs 实际代理目标，便于调试和重放

#### 对比 LangChain

LangChain 的 Tool 抽象有 StructuredTool / Tool / BaseTool / @tool 装饰器等多种形式，心智负担高。WeKnora 的三层一致性更干净。

---

### 3.3 Memory 分层治理

#### 三层结构

```
┌──────────────────────────────────────────────────────┐
│  Layer 1: Resident 常驻层                             │
│  ─────────────────────────────────                    │
│  种类：profile / preference / interest                │
│  行为：直接注入到 System Prompt                        │
│  特点：零延迟、每次对话都可见                          │
│  例子："用户偏好简洁回答"、"用户是工程师"              │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  Layer 2: 召回层                                       │
│  ─────────────────────────────────                    │
│  种类：fact / task                                    │
│  行为：通过 search_memory 工具按需检索                 │
│  特点：需要 LLM 主动调用工具才能拿到                   │
│  例子："用户上周让我跟进 A 项目"、"用户说过喜欢 Python" │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  Layer 3: Context Compaction 层                       │
│  ─────────────────────────────────                    │
│  位置：internal/agent/compaction/                     │
│  行为：上下文膨胀时做 checkpoint + cutpoint + overflow │
│  特点：保留 file-ops 语义，不是简单截断                │
│  例子：编辑文件的 ops 合并、长对话摘要                 │
└──────────────────────────────────────────────────────┘
```

#### 关键领域模型

`internal/types/memory.go` 定义了核心结构：
- `MemoryKind` — 5 种记忆类型枚举
- `MemoryItem` — 单条记忆
- `MemoryConfig` — 记忆配置
- `MemorySubject` — 记忆主题
- `MemoryTopicStat/Aliases` — 主题统计与别名
- `MemoryDocAffinity` — 文档亲和度
- `MemoryTombstone` — 记忆墓碑（软删除）
- `MemorySettings` — 全局设置

#### 为什么分层

Mem0/MemGPT 的做法是把所有 memory 都塞进向量库检索，问题是：
1. **高频信息延迟高**：用户偏好每次都要检索，浪费 token 和时间
2. **检索不稳定**：向量化后可能有召回偏差

WeKnora 的分层方案：
- ✅ Resident 层保证高频信息零延迟
- ✅ 召回层处理低频但重要的事实
- ✅ Compaction 层处理上下文膨胀

#### 数据流

```
用户输入 → Agent 执行 → 异步/同步 Memory 提取
                              │
                              ├─► profile/preference/interest → Resident 注入
                              └─► fact/task → 向量库存储
                                             ↓
                              下次对话时 LLM 主动调用 search_memory → 召回
```

---

### 3.4 Skill 声明式剧本

#### 什么是 Skill

Skill 不是代码插件，而是**带上下文的声明式任务剧本**。一个 Skill 包含：
- `SKILL.md` — 主指令文件（frontmatter + 自然语言指令）
- 可选的资源文件（references/、templates/、tools/ 等）
- 沙箱隔离 + 环境变量注入

#### 安装源

```
ClawHub（官方 Skill 市场）
    ↓
SkillHub（企业私有 Skill 仓库）
    ↓
Git 仓库（直接 clone）
    ↓
ZIP 包（离线分发）
```

#### 关键模块

- `internal/agent/skills/` — Skill 子系统
  - `Skill` / `SkillMetadata` — 数据结构
  - `Loader` — 加载器（frontmatter 解析）
  - `Manager` — 管理器（安装/卸载/升级）
  - `SkillSource` / `TenantSource` — 来源抽象
  - 滚动升级策略（tenant 级可配置）

#### 与 OpenAI GPTs Actions 的对比

| 维度 | GPTs Actions | WeKnora Skill |
|---|---|---|
| 表达形式 | REST API OpenAPI spec | 自然语言 + frontmatter |
| 能力边界 | 单一 HTTP 调用 | 任意组合工具 + 上下文 |
| 复杂度 | 简单 | 可承载复杂任务剧本 |
| 版本管理 | 弱 | 强（rolling upgrade） |
| 隔离 | 弱 | Sandbox 隔离 |

---


### 3.5 MCP 双向能力

#### Client 端：internal/mcp/

调用外部 MCP 服务器，关键组件：
- `manager.go` — 多 server 管理
- `oauth_*` — 完整 OAuth 生命周期（auth code / device code / refresh）
- `security.go` — SSRF 防护（URL 白名单、IP 校验）
- `client/` — 协议实现

#### Server 端：internal/mcpserver/ + mcp-server/

对外暴露 WeKnora 能力，29 个内置工具：
- ask / retrieve / ingest — RAG 能力
- wiki_* — Wiki 能力
- scope — 作用域控制

Go 端走 MCP 2.x 高层 API；Python 端打包为 `tencent-weknora-mcp` PyPI 包。

#### 为什么双向都要做

只做 Client：WeKnora 是 MCP 消费者，无法让其他 Agent 调用 WeKnora 的 RAG 能力
只做 Server：WeKnora 无法扩展外部工具生态

双向实现：
- ✅ WeKnora 可以调用任意 MCP 服务器的工具
- ✅ 其他 MCP Client 也可以调用 WeKnora 的 RAG 能力
- ✅ 形成双向生态

---

### 3.6 无 SDK 依赖的 Provider 抽象

#### 核心事实

go.mod 里 **没有** `openai-go`、`anthropic-sdk-go` 等官方 SDK。全部走自研：

```
internal/models/
├── api/          # 通用 API 抽象
├── catalog/      # 模型目录（能力矩阵）
├── vendors/      # 多厂商适配（每个厂商一个实现）
├── parity/       # 能力对齐（让不同厂商能力对 LLM 透明）
├── limiter/      # 并发控制
├── embedding/    # 嵌入模型
├── rerank/       # 重排模型
├── vlm/          # 视觉语言模型
└── asr/          # 语音识别
```

#### 设计优势

1. **厂商 API 漂移时改一个 vendor 文件**，不污染上层
2. **能力对齐（parity）**：让 LLM 切换对 Agent 完全透明
3. **Limiter 在 provider 层做**：避免每个 tool 自己处理并发

#### 代价

1. 需要自己维护协议细节（流式、tool calling、多模态）
2. 新功能（如 Claude 的 prompt caching）需要自己实现
3. 测试矩阵大（每个厂商 × 每种能力）

#### 对比用官方 SDK

用官方 SDK：
- ✅ 开箱即用，新功能跟进快
- ❌ 多厂商时要维护多套 SDK 依赖
- ❌ SDK 间的抽象不一致（OpenAI SDK vs Anthropic SDK 差异大）

WeKnora 的方案：
- ✅ 统一抽象，上层无感
- ✅ 依赖少，升级可控
- ❌ 维护成本高

---

### 3.7 ReAct 循环的工程化兜底

#### 核心问题

ReAct 循环的朴素实现容易遇到：
1. **死循环**：LLM 反复调用同一个工具
2. **Token 爆炸**：工具输出太大，上下文爆炸
3. **延迟不可控**：不知道何时该停

#### WeKnora 的兜底机制

| 机制 | 文件 | 作用 |
|---|---|---|
| `loopGuards` | engine.go | 检测循环停滞（同样的 tool call 反复出现） |
| `allowSteerOverrun` | steer.go | 控制 steer 超出预算时的行为 |
| `withinIterationBudget` | engine.go | 迭代预算检查 |
| `maxIterationsDisplay` | engine.go | 显示给用户的最大迭代数 |
| `calibrationRun` | calibration.go | 校准 token 估算（避免估算偏差累积） |
| `iterOutcome` | engine.go | 状态机控制流（continue / stop / error / overrun） |
| `MaxToolOutputSize` | registry.go | 工具输出最大长度截断 |

#### 对比 LangChain AgentExecutor

LangChain 的 `max_iterations` 是粗暴截断——到了次数就停，不管任务有没有完成。

WeKnora 的兜底是精细控制：
- 循环检测（同样的 tool call 出现 N 次就打断）
- Token 预算（估算下一轮的 token，超了就 steer）
- 状态机（iterOutcome 显式表达每轮结果）

---

## 四、与主流框架深度对比

### 4.1 对比矩阵

| 框架 | 核心模型 | 多 Agent | 工具抽象 | Memory | 企业治理 | 语言 |
|---|---|---|---|---|---|---|
| **WeKnora** | 单引擎 ReAct + 作用域 | 否（靠作用域切换） | 三层一致性 | 三层分层 | ✅ 完整 | Go |
| **LangChain/LangGraph** | 链式 + 图编排 | ✅ 强 | 多形态混乱 | 弱 | ❌ 弱 | Python/JS |
| **AutoGPT/OpenManus** | 自主 Agent | 弱 | 简单 | 弱 | ❌ 无 | Python |
| **CrewAI** | 多 Agent 角色协作 | ✅ 强 | 简单 | 弱 | ❌ 无 | Python |
| **AutoGen** | 多 Agent 对话 | ✅ 强 | 简单 | 弱 | ❌ 无 | Python |
| **Dify** | 可视化编排 | 弱 | 简单 | 弱 | ✅ 中 | Python |
| **Coze** | 可视化编排 | 弱 | 简单 | 弱 | ✅ 中 | 闭源 |
| **Mem0** | Memory 专精 | ❌ | ❌ | ✅ 强 | ❌ 无 | Python |
| **MemGPT/Letta** | Working Memory | ❌ | ❌ | ✅ 强 | ❌ 无 | Python |
| **Claude Code** | IDE Agent | ❌ | 强 | 中 | ❌ 弱 | TS |

### 4.2 选型建议

| 场景 | 推荐框架 |
|---|---|
| 企业知识库 + 客服 | **WeKnora** / Dify |
| 通用 Agent prototype | LangChain / LangGraph |
| 复杂多角色协作 | CrewAI / AutoGen |
| 开放域自主任务 | AutoGPT / OpenManus |
| 低代码快速上线 | Dify / Coze |
| 长期记忆研究 | Mem0 / MemGPT |
| IDE 编码辅助 | Claude Code / Cursor |
| 企业级 RAG + Agent + 长期维护 | **WeKnora** |

### 4.3 WeKnora 的差异化价值

1. **Go 实现**：性能好，部署简单（单二进制），适合企业级服务
2. **企业治理完整**：RBAC、多租户、审计、API Key Scope 一应俱全
3. **全栈能力**：RAG + Agent + Wiki + IM 多渠道，不是单点工具
4. **生产验证**：腾讯内部大规模使用，不是研究原型
5. **MIT 协议**：商用友好

---


## 五、分层学习路径（4 周计划）

### Week 1：建立骨架认知

**目标**：搞懂 Agent 引擎的骨架，能画出 ReAct 主循环的数据流

| 天数 | 任务 | 关键文件 | 产出 |
|---|---|---|---|
| Day 1 | 配置 dev 环境，跑通 CLI | `cli/cmd/agent.go`、`cmd/server/main.go` | 本地能对话 |
| Day 2 | 跑通 HTTP 服务，Web UI 对话 | `cmd/server/bootstrap.go`、`frontend/` | Web UI 能聊 |
| Day 3 | 精读 AgentEngine 主循环 | `internal/agent/engine.go` | 画出 executeLoop 流程图 |
| Day 4 | 跟一次 Think 阶段 | `internal/agent/think.go`、`steer.go` | 理解 LLM 调用细节 |
| Day 5 | 跟一次 Act 阶段 | `internal/agent/act.go` | 理解 tool 执行链路 |
| Day 6 | 看 Observe 阶段 + Langfuse | `internal/agent/observe.go`、`tracing/` | 看一次完整 trace |
| Day 7 | 复盘，画数据流图 | 全部 | 一张完整的单步 ReAct 数据流图 |

**自检题**：
- [ ] 能说出 `runReActIteration` 的 4 个阶段
- [ ] 能解释 `iterOutcome` 的几种状态
- [ ] 能在 Langfuse 里找到一次完整对话的 trace

---

### Week 2：吃透 Tool + Memory 抽象

**目标**：理解 Tool 三层一致性和 Memory 分层

| 天数 | 任务 | 关键文件 | 产出 |
|---|---|---|---|
| Day 1 | 精读 types.Tool 接口 | `internal/types/agent.go` | 接口记忆 |
| Day 2 | 精读 ToolRegistry | `internal/agent/tools/registry.go` | 注册/执行流程 |
| Day 3 | 看一个具体 Tool 实现 | `tools/web_search.go` 或 `tools/shell_exec.go` | Tool 编写模式 |
| Day 4 | 看 deferred 注册机制 | `tools/registry.go` 的 RegisterDeferredTool | 启动优化原理 |
| Day 5 | 精读 Memory 领域模型 | `types/memory.go`、`memory_extraction.go` | 5 种 MemoryKind |
| Day 6 | 跟一次 search_memory 调用 | `tools/search_memory.go` | 召回链路 |
| Day 7 | 看 context compaction | `internal/agent/compaction/` | 压缩策略 |

**实战任务**：
- 实现一个自定义 Tool（如 `get_weather`），挂到 registry，让 Agent 能调用
- 给这个 Tool 写单元测试

**自检题**：
- [ ] 能写出 `types.Tool` 接口的 4 个方法
- [ ] 能解释 first-wins 策略
- [ ] 能区分 Resident 和召回层 Memory

---

### Week 3：打通 MCP + Skill + Sandbox

**目标**：理解三大扩展点

| 天数 | 任务 | 关键文件 | 产出 |
|---|---|---|---|
| Day 1 | 配置一个外部 MCP server | `examples/mcp-demo/` | MCP 联调跑通 |
| Day 2 | 精读 MCP Client | `internal/mcp/manager.go` | 多 server 管理 |
| Day 3 | 精读 MCP OAuth + Security | `internal/mcp/oauth_*.go`、`security.go` | SSRF 防护 |
| Day 4 | 精读 MCP Server | `internal/mcpserver/` | 暴露能力 |
| Day 5 | 写一个 SKILL.md | `internal/agent/skills/` | 声明式剧本 |
| Day 6 | 看 sandbox 隔离 | `internal/sandbox/`、`localsandbox/` | 沙箱机制 |
| Day 7 | Skill + MCP 联调 | 综合 | 完整扩展链路 |

**实战任务**：
- 写一个 Skill（如 `daily-report`），定义 frontmatter + 指令
- 配置一个外部 MCP server，让 Agent 调用

**自检题**：
- [ ] 能解释 MCP Client 和 Server 的差异
- [ ] 能写出 SKILL.md 的 frontmatter 字段
- [ ] 能说明 sandbox 如何隔离执行

---

### Week 4：企业级特性 + 贡献

**目标**：理解企业级治理 + 准备贡献

| 天数 | 任务 | 关键文件 | 产出 |
|---|---|---|---|
| Day 1 | RBAC 机制 | `handler/rbac*.go` | 权限模型 |
| Day 2 | API Key Scope | `handler/custom_agent_api_key_scope*.go` | 能力级授权 |
| Day 3 | Approval Gate | `internal/agent/approval/gate.go` | Human-in-the-loop |
| Day 4 | Provider 抽象 | `internal/models/vendors/` | 加一个新 vendor |
| Day 5 | Langfuse tracing | `internal/tracing/` | 给 tool 加 span |
| Day 6 | 多租户 | `types/tenant.go` | Tenant 隔离 |
| Day 7 | 选一个小 issue 提 PR | GitHub | 首次贡献 |

**自检题**：
- [ ] 能画出 RBAC 权限模型
- [ ] 能解释 Approval Gate 的触发时机
- [ ] 能添加一个新 LLM vendor

---


## 六、关键代码导读

### 6.1 Agent 引擎核心（必读）

| 文件 | 关键概念 | 阅读要点 |
|---|---|---|
| `internal/agent/engine.go` | AgentEngine 主结构 | Execute 入口、executeLoop 主循环、iterOutcome 状态机、loopGuards |
| `internal/agent/act.go` | Act 阶段 | executeToolCalls、并行执行、ToolCallTarget 解析、Langfuse span |
| `internal/agent/think.go` | Think 阶段 | LLM 调用、流式响应处理 |
| `internal/agent/steer.go` | Steer 控制 | allowSteerOverrun、预算控制 |
| `internal/agent/prompts.go` | System Prompt 组装 | 模板 + Memory Resident 注入 |
| `internal/agent/checkpoint.go` | 检查点 | 状态持久化 |
| `internal/agent/finalize.go` | 终态汇总 | AgentSteps 汇总 |
| `internal/agent/observe.go` | 观测 | Langfuse 集成 |

### 6.2 Tool 子系统（必读）

| 文件 | 关键概念 |
|---|---|
| `internal/types/agent.go` | Tool 接口、ToolResult、LLMToolCall |
| `internal/agent/tools/registry.go` | ToolRegistry、Deferred 注册、First-wins、ExecuteTool |
| `internal/agent/tools/tool.go` | BaseTool 公共骨架、ToolExecutor 接口 |
| `internal/agent/tools/web_search.go` | Web 搜索工具实现样例 |
| `internal/agent/tools/shell_exec.go` | Shell 执行工具实现 |
| `internal/agent/tools/mcp_tool.go` | MCP 工具适配器 |
| `internal/agent/tools/search_memory.go` | Memory 召回工具 |
| `internal/agent/tools/scope_authorization.go` | 工具作用域授权 |

### 6.3 Memory 子系统（必读）

| 文件 | 关键概念 |
|---|---|
| `internal/types/memory.go` | MemoryKind、MemoryItem、MemoryConfig |
| `internal/types/memory_extraction.go` | 记忆提取逻辑 |
| `internal/handler/memory.go` | Memory HTTP Handler |
| `internal/tracing/langfuse/memory_obs.go` | Memory 可观测 |
| `internal/application/service/agent_history*.go` | 历史管理 |

### 6.4 Context 压缩（进阶）

| 文件 | 关键概念 |
|---|---|
| `internal/agent/compaction/compactor.go` | 压缩主逻辑 |
| `internal/agent/compaction/cutpoint.go` | 切点策略 |
| `internal/agent/compaction/overflow.go` | 溢出处理 |
| `internal/agent/compaction/checkpoint.go` | 检查点 |
| `internal/agent/compaction/fileops.go` | File-ops 语义保留 |

### 6.5 MCP 子系统（扩展）

| 文件 | 关键概念 |
|---|---|
| `internal/mcp/manager.go` | 多 server 管理 |
| `internal/mcp/oauth_*.go` | OAuth 生命周期 |
| `internal/mcp/security.go` | SSRF 防护 |
| `internal/mcpserver/tools_*.go` | Server 端工具实现 |

### 6.6 Skill 子系统（扩展）

| 文件 | 关键概念 |
|---|---|
| `internal/agent/skills/loader.go` | SKILL.md 加载、frontmatter 解析 |
| `internal/agent/skills/manager.go` | 安装/卸载/升级 |
| `internal/agent/skills/source.go` | 多源抽象 |

### 6.7 Provider 抽象（扩展）

| 文件 | 关键概念 |
|---|---|
| `internal/models/api/` | 通用 API 抽象 |
| `internal/models/catalog/` | 模型目录 |
| `internal/models/vendors/` | 多厂商实现 |
| `internal/models/parity/` | 能力对齐 |
| `internal/models/limiter/` | 并发控制 |

---

## 七、实战练习

### 练习 1：实现自定义 Tool（入门）

**目标**：写一个 `get_weather` 工具，让 Agent 能查询天气

**步骤**：
1. 在 `internal/agent/tools/` 下创建 `weather.go`
2. 实现 `types.Tool` 接口
3. 在 registry 注册
4. 写单元测试 `weather_test.go`
5. 在 CLI 测试对话

**参考答案骨架**：
```go
// internal/agent/tools/weather.go
package tools

import (
    "context"
    "encoding/json"
    "github.com/Tencent/WeKnora/internal/types"
)

type WeatherTool struct {
    BaseTool
}

func NewWeatherTool() *WeatherTool {
    return &WeatherTool{
        BaseTool: NewBaseTool(
            "get_weather",
            "Get current weather for a location",
            json.RawMessage(`{
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                },
                "required": ["location"]
            }`),
        ),
    }
}

func (t *WeatherTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
    var params struct {
        Location string `json:"location"`
        Unit     string `json:"unit"`
    }
    if err := json.Unmarshal(args, &params); err != nil {
        return nil, err
    }
    // 调用真实天气 API
    return &types.ToolResult{
        Content: "Sunny, 25°C",
    }, nil
}
```

### 练习 2：实现 MCP Server（中级）

**目标**：写一个 Python MCP Server，暴露自定义工具

**步骤**：
1. 参考 `examples/mcp-demo/server.py`
2. 实现一个工具（如 `calculate` 数学计算）
3. 在 WeKnora 中配置这个 MCP server
4. 让 Agent 通过 MCP 调用

### 练习 3：写一个 Skill（中级）

**目标**：写一个 SKILL.md，让 Agent 按剧本执行

**步骤**：
1. 创建 `skills/daily-report/SKILL.md`
2. 定义 frontmatter（name、description、tools）
3. 写自然语言指令
4. 安装并测试

### 练习 4：添加新 LLM Vendor（高级）

**目标**：添加一个新的 LLM 厂商（如 DeepSeek、Moonshot）

**步骤**：
1. 在 `internal/models/vendors/` 下创建新目录
2. 实现 vendor 接口
3. 在 catalog 注册模型
4. 配置 parity（能力对齐）
5. 测试调用

### 练习 5：添加 Langfuse Span（高级）

**目标**：给一个自定义工具加完整的 Langfuse 追踪

**步骤**：
1. 参考 `internal/agent/act.go` 的 buildToolSpanInput / finishToolSpan
2. 在你的工具里创建 span
3. 在 Langfuse UI 查看 trace

---


## 八、反常识设计点

学习 WeKnora 时，以下设计可能违反直觉，值得特别关注：

### 8.1 没有多 Agent DAG

**反常识**：大多数 Agent 框架都在做多 Agent 协作，WeKnora 却只用单引擎。

**为什么**：
- 多 Agent 的状态空间是指数级（N 个 Agent 的组合）
- 调试困难（消息在 Agent 间传递，trace 难拼）
- 90% 的企业场景不需要多 Agent，单 Agent + 丰富工具就够了

**取舍**：
- ✅ 单点可观测、状态收敛、调试简单
- ❌ 复杂多步任务靠 LLM 自己规划

---

### 8.2 不用官方 LLM SDK

**反常识**：Go 生态有 `openai-go`、`anthropic-sdk-go`，WeKnora 却全部自研。

**为什么**：
- 多厂商时，SDK 间的抽象不一致（OpenAI SDK vs Anthropic SDK 差异大）
- 官方 SDK 跟进新功能快，但企业需要稳定性
- 自研可以统一抽象，上层无感

**取舍**：
- ✅ 统一抽象、依赖少、升级可控
- ❌ 维护成本高、需要自己实现协议细节

---

### 8.3 Memory 不全是向量检索

**反常识**：Mem0/MemGPT 把所有 memory 都塞进向量库，WeKnora 却分三层。

**为什么**：
- 高频信息（用户偏好）每次检索浪费 token 和时间
- 向量化有召回偏差，不稳定
- Resident 层直接注入 system prompt，零延迟

**取舍**：
- ✅ 高频信息零延迟、召回层处理低频事实
- ❌ 需要区分哪些信息该进哪一层

---

### 8.4 Tool Schema 可以 Deferred

**反常识**：80+ 工具启动时不全部加载 schema，而是按需加载。

**为什么**：
- 启动时间从 O(N) 降到 O(1)
- 大部分工具一次对话根本用不到

**取舍**：
- ✅ 启动快、内存省
- ⚠️ 第一次用某个工具时有轻微延迟

---

### 8.5 Context Compaction 不是简单截断

**反常识**：上下文膨胀时，不是简单截断历史，而是保留 file-ops 语义。

**为什么**：
- 简单截断会丢失关键信息（如编辑文件的 ops）
- File-ops 合并可以保留语义，压缩率更高

**取舍**：
- ✅ 信息保留完整、压缩率高
- ❌ 实现复杂

---

### 8.6 MCP 双向都要做

**反常识**：大多数项目只做 MCP Client，WeKnora 却 Client + Server 都做。

**为什么**：
- 只做 Client：WeKnora 是消费者，无法让其他 Agent 调用 WeKnora 的 RAG 能力
- 只做 Server：WeKnora 无法扩展外部工具生态
- 双向才能形成生态

**取舍**：
- ✅ 双向生态、能力复用
- ❌ 实现成本高

---

### 8.7 Skill 不是代码插件

**反常识**：Skill 不是代码插件，而是声明式剧本（SKILL.md + 自然语言指令）。

**为什么**：
- 代码插件耦合深，难维护
- 声明式剧本灵活，可以承载复杂任务
- 沙箱隔离，安全

**取舍**：
- ✅ 灵活、安全、易维护
- ❌ 不如代码插件性能好

---

## 九、延伸阅读与社区资源

### 9.1 官方资源

- **GitHub**：https://github.com/Tencent/WeKnora
- **文档站**：`website-docs/`（VitePress）
- **CHANGELOG**：`CHANGELOG.md`（版本演进记录）
- **示例**：`examples/`（mcp-demo、skills）

### 9.2 相关技术

| 技术 | 说明 | 学习价值 |
|---|---|---|
| **ReAct 论文** | Synergizing Reasoning and Acting in Language Models | Agent 理论基础 |
| **MCP 协议** | Model Context Protocol | Agent 工具扩展标准 |
| **Langfuse** | Open-source LLM engineering platform | 可观测性实践 |
| **Milvus/Qdrant** | Vector databases | RAG 基础设施 |
| **Go Gin** | HTTP framework | Web 开发 |
| **connectrpc** | gRPC for Go | RPC 实践 |

### 9.3 对比阅读

| 项目 | 对比价值 |
|---|---|
| **LangChain** | 对比 Tool 抽象、Agent 模型 |
| **CrewAI** | 对比多 Agent 协作模型 |
| **Dify** | 对比低代码 vs 代码优先 |
| **Mem0** | 对比 Memory 设计 |
| **Claude Code** | 对比单 Agent 设计 |

### 9.4 论文与文章

- **ReAct**: https://arxiv.org/abs/2210.03629
- **Toolformer**: https://arxiv.org/abs/2302.04761
- **HuggingGPT**: https://arxiv.org/abs/2303.17580
- **Voyager**: https://arxiv.org/abs/2305.16291
- **MCP Specification**: https://modelcontextprotocol.io/

### 9.5 社区与讨论

- **GitHub Issues**：功能请求、bug 报告
- **GitHub Discussions**：架构讨论、使用问题
- **Discord/Slack**：实时交流（如果有）
- **Twitter/X**：关注项目动态

---

## 附录 A：快速上手命令

```bash
# 克隆项目
git clone https://github.com/Tencent/WeKnora.git
cd WeKnora

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key、数据库配置等

# 启动服务
make build
./bin/weknora-server

# 或使用 CLI
go run cli/main.go agent chat --agent-id <agent-id>

# 运行测试
make test

# 启动前端（开发模式）
cd frontend
npm install
npm run dev
```

---

## 附录 B：常见问题

**Q1：WeKnora 和 Dify 的区别是什么？**

A：Dify 是低代码编排平台，WeKnora 是代码优先的企业级框架。Dify 适合快速上线，WeKnora 适合需要深度定制和长期维护的场景。

**Q2：为什么选择 Go 而不是 Python？**

A：Go 性能好（编译型语言）、部署简单（单二进制）、并发模型优秀（goroutine）。Python 生态更丰富，但性能和部署是短板。

**Q3：如何贡献代码？**

A：参考 GitHub 的 CONTRIBUTING.md，通常流程：Fork → Branch → Commit → PR → Review → Merge。

**Q4：如何添加新的 LLM 厂商？**

A：在 `internal/models/vendors/` 下创建新目录，实现 vendor 接口，在 catalog 注册模型，配置 parity。

**Q5：如何写一个 Skill？**

A：创建 SKILL.md，定义 frontmatter（name、description、tools），写自然语言指令，安装到 Skill 管理器。

---

## 附录 C：术语表

| 术语 | 说明 |
|---|---|
| **ReAct** | Reasoning + Acting，Agent 的核心循环模式 |
| **Tool** | Agent 可以调用的工具，有 schema 和 Execute 方法 |
| **Skill** | 声明式任务剧本，带上下文的指令集 |
| **MCP** | Model Context Protocol，Agent 工具扩展标准协议 |
| **RAG** | Retrieval-Augmented Generation，检索增强生成 |
| **Memory** | Agent 的记忆系统，分 Resident 和召回层 |
| **Compaction** | 上下文压缩，保留语义的裁剪 |
| **Vendor** | LLM 厂商（OpenAI、Anthropic、DeepSeek 等） |
| **Provider** | 能力提供商（LLM、Embedding、Rerank、VLM、ASR 等） |
| **Sandbox** | 隔离执行环境（Docker / E2B / Cube） |
| **RBAC** | Role-Based Access Control，基于角色的访问控制 |
| **Langfuse** | 开源 LLM 可观测性平台 |

---

## 附录 D：学习检查清单

完成以下所有项目，恭喜你成为 WeKnora 专家！

### Week 1 检查
- [ ] 本地跑通 CLI 和 Web UI
- [ ] 能画出 ReAct 主循环数据流
- [ ] 能在 Langfuse 看完整 trace

### Week 2 检查
- [ ] 实现一个自定义 Tool
- [ ] 能区分 5 种 MemoryKind
- [ ] 能解释 Context Compaction 原理

### Week 3 检查
- [ ] 配置一个 MCP Server
- [ ] 写一个 SKILL.md
- [ ] 能解释 Sandbox 隔离机制

### Week 4 检查
- [ ] 能画出 RBAC 权限模型
- [ ] 添加一个新 LLM Vendor
- [ ] 提交一个 PR

---

> 文档版本：v1.0
> 最后更新：2026-09-24
> 维护者：WeKnora 社区
> 许可证：MIT

**祝学习愉快！** 🚀

