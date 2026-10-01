# WeKnora 服务架构说明

> 本文档详细说明 WeKnora 项目的每个服务职责、服务间通信方式，并分析是否需要引入独立的消息服务中心。

---

## 一、整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                客户端层                                          │
├──────────────┬──────────────┬──────────────┬──────────────┬─────────────────────┤
│  frontend    │   cli/       │   client/    │  miniprogram │  cmd/desktop        │
│  (Vue 3)     │  (Go CLI)    │  (Go SDK)    │  (微信小程序) │  (Wails 桌面)        │
└──────┬───────┴──────┬───────┴──────┬───────┴──────┬───────┴──────────┬──────────┘
       │              │              │              │                  │
       │         HTTP/REST API   HTTP/REST    HTTP/REST           HTTP/REST
       │              │              │              │                  │
       ▼              ▼              ▼              ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     cmd/server (主 Go 服务器)                                    │
│                    :8080 HTTP · Gin + dig + Viper + GORM                        │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  internal/                                                                │  │
│  │  ├── handler/        HTTP 请求处理                                        │  │
│  │  ├── application/    业务逻辑 + RAG 检索管线                               │  │
│  │  ├── agent/          ReAct Agent 循环 + 工具执行                           │  │
│  │  ├── models/         多厂商 LLM/Embedding/Rerank/VLM/ASR 适配器           │  │
│  │  ├── infrastructure/ 文档解析引擎、Web 搜索                               │  │
│  │  ├── datasource/     数据源连接器 (飞书/GitLab/Notion/语雀/...)           │  │
│  │  ├── im/             IM 渠道适配 (企微/微信/飞书/钉钉/Slack/Telegram)     │  │
│  │  ├── mcpserver/      内置 MCP Server (Streamable HTTP)                    │  │
│  │  ├── sandbox/        远程沙箱 (Docker/E2B/Cube)                           │  │
│  │  ├── stream/         SSE 流管理器 (内存 + Redis)                          │  │
│  │  └── event/          进程内事件总线                                       │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │ gRPC :50051      │ Redis            │ Async Queue
              ▼                  ▼                  ▼
       ┌─────────────┐   ┌───────────┐     ┌──────────────┐
       │  docreader  │   │   Redis   │     │    asynq     │
       │  (Python)   │   │  (Pub/Sub │     │  (任务队列)   │
       │  文档解析    │   │  + Stream │     │              │
       └─────────────┘   │  + Lock)  │     └──────────────┘
                         └───────────┘

       ┌──────────────────────────────────────────────────────────────┐
       │  python-service (Python FastAPI) :50052                      │
       │  A 股金融数据分析服务                                         │
       │  ├── DuckDB 查询 (行情/财务/基金/特色数据/期货/指数/指标)     │
       │  ├── 技术指标计算 (MA/MACD/KDJ/RSI/BOLL/ATR/OBV/VWAP)      │
       │  ├── Zettaranc Z哥交易体系分析 (波浪/麒麟会/砖型图/战法)     │
       │  └── 选股策略                                                │
       │  数据来源: 本地 DuckDB 文件 (~/.hithink-finance/)            │
       │  调用方: 主服务器 Agent 工具 (hithink-finance-* skills)      │
       └──────────────────────────────────────────────────────────────┘
```

---

## 二、各服务详细说明

### 1. cmd/server — 主 Go 服务器（核心）

| 属性 | 值 |
|---|---|
| **语言/框架** | Go 1.26 / Gin |
| **端口** | `:8080` (HTTP) |
| **职责** | 整个系统的核心枢纽，承载全部业务逻辑 |
| **入口** | `cmd/server/main.go` |

**核心职责：**

- **HTTP API 网关** — 所有客户端（前端、CLI、SDK、小程序、桌面端、IM 渠道）通过 HTTP/REST + SSE 与此服务通信
- **RAG 检索引擎** — 混合检索（向量 + 全文 + 图谱）、Rerank、MMR，支持 11 种向量数据库驱动
- **ReAct Agent 引擎** — Think → Analyze → Act → Observe 循环，执行工具调用、MCP 工具、沙箱命令
- **LLM 多厂商适配** — 17+ 供应商（OpenAI / Anthropic / DeepSeek / Qwen / Gemini / ...）统一抽象
- **知识库管理** — 文档导入、分块、索引、FAQ、数据源同步
- **会话与记忆** — 对话会话管理、跨会话长期记忆
- **Wiki 模式** — 自动生成和策展长文档 Wiki
- **Skill 沙箱** — Docker / E2B / Cube 远程沙箱执行代码
- **MCP Server（内置）** — Streamable HTTP MCP 端点，替代旧的 Python MCP Server
- **IM 渠道层** — 企微/微信/飞书/钉钉/Slack/Telegram/Mattermost 消息接入
- **异步任务** — asynq 任务队列（Redis 后端），处理文档解析、数据源同步等异步工作
- **SSE 流管理** — 内存或 Redis 后端的服务器推送事件
- **多租户 RBAC** — 空间隔离、角色权限

**为什么是单体而非微服务拆分？**

WeKnora 的 `internal/` 虽然包罗万象，但本质上是一个**模块化单体（Modular Monolith）**。所有子模块通过 Go 接口解耦，通过 `uber/dig` 依赖注入组装。这种架构在当前规模下比拆成多个独立服务更高效——没有网络开销、没有分布式事务、没有服务编排复杂度。

---

### 2. docreader/ — 文档解析服务

| 属性 | 值 |
|---|---|
| **语言/框架** | Python / FastAPI + gRPC |
| **端口** | `:50051` (gRPC) |
| **职责** | 文档解析、OCR、多模态处理 |
| **通信方式** | gRPC（由主服务器调用） |

**核心职责：**

- 解析 10+ 文档格式：PDF / DOCX / PPT / Excel / Markdown / HTML / MHTML / EPUB / XMind / 图片 / URL
- 集成 LibreOffice（Office 文档转换）、Playwright WebKit（网页渲染）
- 扫描 PDF → 图片渲染 → 交由主服务侧 OCR/VLM 处理
- 支持多种存储后端：MinIO / S3 / 腾讯云 COS / 阿里云 OSS
- 可选集成 MinerU 高级文档解析引擎
- SSRF 安全的 HTTP 客户端
- gRPC 健康检查

**调用关系：**

```
主服务器 ──gRPC──▶ docreader:50051
                    ├── 解析文档 → 返回结构化内容
                    └── 上传/下载附件 → MinIO/S3
```

**为什么不内置到主服务器？**

文档解析是 **CPU 密集型 + 内存密集型** 工作（PDF 渲染、OCR、大文件处理），Python 生态的解析库也更丰富。独立部署可以：
- 单独扩缩容（解析高峰期水平扩展 docreader 实例）
- 故障隔离（解析崩溃不影响主 API 服务）
- 资源隔离（大文件解析不会耗尽主服务的内存）

---

### 3. python-service/ — A 股金融数据分析服务

| 属性 | 值 |
|---|---|
| **语言/框架** | Python / FastAPI + Uvicorn |
| **端口** | `:50052` (HTTP) |
| **职责** | A 股金融数据查询、技术指标计算、Zettaranc 交易体系分析 |
| **通信方式** | HTTP/REST（由主服务器 Agent 工具调用） |
| **数据来源** | 本地 DuckDB 文件（`~/.hithink-finance/`） |

**核心职责：**

- **DuckDB 只读查询接口** — 7 个本地数据库：
  - `market` — A 股 K 线、复权因子、标的目录
  - `financials` — 财务报表（三表）、财务指标、估值
  - `fund` — 基金档案、净值、ETF
  - `special` — 涨跌停、龙虎榜、热股
  - `futures` — 期货品种、合约、日 K
  - `index` — 指数目录、成分股、日 K
  - `indicators` — 60+ 种技术指标
- **技术指标计算** — MA / MACD / KDJ / RSI / BOLL / ATR / OBV / VWAP
- **Zettaranc（Z哥交易体系）综合分析**：
  - 技术指标：KDJ、MACD、RSI、BBI、白线黄线、布林带、砖型图
  - 波浪分析：三波理论阶段判断
  - 麒麟会：阶段和置信度
  - 战法信号：30+ 种交易战法检测
  - 综合诊断：买卖点判断 + 综合评分
- **选股策略** — 基于技术指标和量化条件的筛选

**浏览器直连的只读行情接口**（不经主服务器，nginx 用一条正则白名单转发）：

`/api/kline`、`/api/annotate`、`/api/indicators`、`/api/symbols/*`、`/api/stock-profile`、`/api/quotes`

这些路径**没有鉴权**，因为它们只转发只读行情、且不含任何用户数据；新增路径必须
同步加进 `frontend/nginx.conf` 的那条正则，否则会落进通用 `/api/` 规则、被主服务器
的鉴权拦掉（表现为 401，而 python-service 根本没收到请求）。
`/api/quotes` 是自选页的批量快照：一次请求取每只票的最后两根 K 线（最新价 + 前收盘），
避免按行 fan-out。

**调用关系：**

```
主服务器 Agent 工具 ──HTTP POST──▶ python-service:50052
  hithink-finance-market              ├── /query/       (DuckDB SQL)
  hithink-finance-financials          ├── /indicators/  (技术指标)
  hithink-finance-special-data        ├── /strategies/  (策略信号)
  hithink-finance-fund                ├── /screen       (选股)
  hithink-finance-index               └── /zettaranc/analyze  (综合分析)
  hithink-finance-valuation
浏览器 ──HTTP GET──▶ python-service:50052
                                      └── /api/kline | /api/quotes | /api/symbols/* …
```

**为什么不内置到主服务器？**

- **Python 数值计算生态** — DuckDB、pandas、numpy 等库在 Python 中使用更自然，且 zettaranc 分析模块本身是 Python 实现
- **数据本地性** — 数据存储在本地 DuckDB 文件中（`~/.hithink-finance/`），与主服务器的 PostgreSQL 完全独立
- **独立迭代** — 金融分析逻辑更新频繁，独立服务可以不依赖主服务器发版
- **资源隔离** — 大量数值计算不会影响主服务器的 API 响应性能

### 3.1 新功能该放哪一层（个股追踪 / 自选清单）

判据是**数据是谁的、可不可写、要不要鉴权**，不是"哪个语言写着顺手"：

| 面 | 持有者 | 可写 | 鉴权 |
|---|---|---|---|
| `/api/kline` `/api/quotes` 等行情读数 | python-service | 只读 | 无（nginx 白名单直通） |
| `/api/v1/**`（含 `/watchlist`） | 主服务器 Go | 读写 | 有（租户 + 角色） |

- **用户自己的可写状态**（自选清单、持仓台账…）→ **主服务器 Go**。python-service
  没有 user/tenant 概念，DuckDB 是只读挂载，个人状态落在那儿等于凭空造一套身份 +
  鉴权 + 迁移。
- **只读行情计算**（价格、指标、形态、批量快照）→ **python-service**，浏览器直连。
- 参考实现：`internal/types/stock_watch.go`（表 `stock_watches`，复合主键
  `(user_id, tenant_id, thscode)`）+ `internal/handler/stock_watch.go`
  + `frontend/src/views/watchlist/Watchlist.vue`（侧栏「个股追踪」菜单 → `/platform/watchlist`）。

---

### 4. frontend/ — Web 前端

| 属性 | 值 |
|---|---|
| **语言/框架** | TypeScript / Vue 3 + Vite + tdesign-vue-next |
| **包管理** | npm（非 pnpm） |
| **职责** | Web 用户界面 |
| **通信方式** | HTTP/REST + SSE（调用主服务器） |

**核心职责：**

- 知识库管理界面（创建/导入/搜索/预览）
- Agent 对话界面（流式 SSE 输出、工具调用可视化）
- 文档分块预览与编辑
- 模型配置管理
- 沙箱终端（noVNC + xterm.js）
- 多语言国际化（i18n）
- MCP 代理（前端 MCP 请求代理到后端）
- 嵌入式 widget（可嵌入第三方页面）

---

### 5. cli/ — 命令行工具

| 属性 | 值 |
|---|---|
| **语言/框架** | Go 1.26 / Cobra（独立 Go module） |
| **职责** | 命令行客户端，本地 Agent 技能 |
| **通信方式** | HTTP/REST（调用主服务器 API） |

**核心职责：**

- 提供 `weknora` 命令行工具，管理知识库、会话、模型、Agent 等
- 子命令：`agent`, `chat`, `kb`, `session`, `model`, `search`, `mcp`, `skills`, `doc` 等
- 内置本地 Agent Skills（`weknora-rag-search`, `weknora-shared`, `embed`）
- 验收测试套件（`acceptance/` 合约测试 + E2E 测试）
- 可通过 Homebrew 安装

---

### 6. client/ — Go SDK

| 属性 | 值 |
|---|---|
| **语言/框架** | Go 1.26（独立 Go module） |
| **职责** | REST API 的 Go 语言客户端库 |
| **通信方式** | HTTP/REST（调用主服务器 API） |

**核心职责：**

- 为第三方 Go 应用提供 WeKnora API 的编程接口
- 封装：会话管理、知识库操作、知识问答（含 SSE 流）、分块、模型、评估、沙箱、长期记忆、认证
- `client/cmd/agent_test/` 提供交互式 Agent-QA 测试 CLI

---

### 7. mcp-server/ — MCP Server（已弃用）

| 属性 | 值 |
|---|---|
| **语言/框架** | Python / MCP SDK |
| **状态** | **⚠️ 已弃用**，保留仅为兼容旧部署 |
| **替代方案** | 主服务器内置的 MCP Server（`internal/mcpserver/`） |

**历史职责：**

- 提供 Model Context Protocol 服务器，让 Claude Code 等 MCP 客户端可以访问 WeKnora 知识库
- 通过 stdio / SSE 传输暴露工具：知识库管理、知识 CRUD、聊天、搜索

**为什么弃用？**

主服务器已内置 Streamable HTTP MCP Server，支持多端点、按端点选择知识库范围和工具。无需再独立部署 Python 进程。

---

### 8. miniprogram/ — 微信小程序

| 属性 | 值 |
|---|---|
| **语言/框架** | JavaScript / 微信小程序框架 |
| **职责** | 微信小程序客户端 |
| **通信方式** | HTTP/REST（调用主服务器 API） |

**核心职责：**

- 移动端轻量对话界面
- 知识库列表浏览
- 通过 URL 导入文档
- 微信生态内的快速访问入口

---

### 9. cmd/desktop/ — 桌面应用

| 属性 | 值 |
|---|---|
| **语言/框架** | Go + Wails |
| **职责** | 桌面客户端 Shell |
| **通信方式** | 内嵌或连接主服务器 |

**核心职责：**

- 基于 Wails 框架的跨平台桌面应用
- 使用 `godotenv` 加载 `.env` 配置（仅桌面端使用，服务端不依赖 `.env`）
- 绑定主服务器的功能到桌面 UI

---

### 10. cmd/milvus-migrate/ — Milvus 迁移工具

| 属性 | 值 |
|---|---|
| **语言** | Go |
| **职责** | 一次性迁移工具 |

将 Milvus 集合迁移到多语言 BM25 格式。独立二进制，非长期运行的服务。

---

### 11. cmd/download/ — 模型下载工具

| 属性 | 值 |
|---|---|
| **语言** | Go |
| **职责** | 辅助工具 |

下载 DuckDB 相关的本地模型文件。一次性工具，非长期运行的服务。

---

## 三、服务间通信方式总结

| 通信路径 | 协议 | 方向 | 说明 |
|---|---|---|---|
| 客户端 → 主服务器 | HTTP/REST + SSE | 同步请求 + 流式推送 | 所有客户端通过 REST API 与主服务器交互 |
| 主服务器 → docreader | gRPC | 同步 RPC | 文档解析请求/响应 |
| 主服务器 → python-service | HTTP/REST | 同步 RPC | Agent 工具调用金融数据查询、Zettaranc 分析 |
| 主服务器 → Redis | Redis 协议 | 多用途 | SSE 流存储、分布式锁、asynq 任务队列、Pub/Sub |
| 主服务器 → LLM 供应商 | HTTP/REST/WebSocket | 同步 + 流式 | 调用 OpenAI / Anthropic / DeepSeek 等 API |
| 主服务器 → 向量数据库 | 各驱动协议 | 同步 RPC | Milvus / PostgreSQL / Qdrant / Weaviate / ... |
| 主服务器 → 对象存储 | S3 协议 | 同步 | MinIO / S3 / COS / OSS 文件存取 |
| 主服务器 → 外部数据源 | HTTP/REST | 同步 + 轮询 | 飞书 / GitLab / Notion / 语雀 / RSS 等同步 |
| 主服务器 → IM 平台 | HTTP Webhook + API | 双向 | 企微 / 飞书 / 钉钉 / Slack / Telegram 消息收发 |
| 主服务器内部 | Go 函数调用 + EventBus | 进程内 | 模块间通过接口 + 事件总线解耦 |

---

## 四、是否需要消息服务中心？

### 现状分析

当前 WeKnora 已经使用了多种异步通信和消息机制：

| 现有机制 | 位置 | 用途 |
|---|---|---|
| **EventBus（事件总线）** | `internal/event/` | 进程内发布/订阅，覆盖查询处理、检索、Agent 执行等事件 |
| **asynq 任务队列** | `internal/middleware/asynqdl/` | Redis 后端的异步任务队列，处理文档解析、数据源同步等延迟任务，带死信队列 |
| **Redis Pub/Sub** | `internal/stream/` | SSE 流式推送的多实例同步 |
| **Redis Stream** | `internal/stream/` | 流式事件存储和分发 |
| **gRPC** | docreader | 同步 RPC 调用文档解析 |
| **HTTP** | python-service | Agent 工具调用金融数据查询和分析 |

### 结论：当前阶段**不需要**引入独立的消息服务中心

**原因：**

#### 1. 架构本质是模块化单体，不是微服务

WeKnora 只有 **三个长期运行的进程**：主 Go 服务器 + docreader + python-service。它们之间的通信都是简单的 **同步调用**（gRPC / HTTP）。不存在多个服务之间复杂的异步编排需求。

```
微服务架构需要消息中心的场景：
  服务A → 消息中心 → 服务B → 消息中心 → 服务C → ...
  (10+ 个服务，跨服务事务，最终一致性)

WeKnora 的实际架构：
  客户端 → 主服务器 ←gRPC→ docreader
              ↕ HTTP
         python-service
  (3 个进程，主服务器内部全通过函数调用 + EventBus 解决)
```

#### 2. EventBus 已覆盖进程内事件解耦

主服务器内部的事件驱动需求（查询处理流水线、Agent 执行追踪、检索/排序/合并步骤通知）已经通过 `internal/event/EventBus` 实现。这是 Go 进程内的 pub/sub，零网络开销，性能远优于外部消息中心。

#### 3. asynq + Redis 已覆盖异步任务需求

文档解析、数据源同步、批量导入等"发射后不管"的异步任务通过 asynq（Redis 后端）处理，带死信队列和重试机制。这已经是一个轻量级的任务消息系统。

#### 4. 引入消息中心的成本远大于收益

引入 RabbitMQ / Kafka / NATS 等消息中心会带来：
- **额外的基础设施依赖** — 多一个需要部署、监控、维护的服务
- **分布式事务复杂度** — 消息确认、幂等性、顺序保证、死信处理
- **调试困难** — 消息流经过消息中心后难以追踪因果链
- **延迟增加** — 多一跳网络开销
- **运维负担** — Docker Compose 多一个组件、Helm chart 多一个依赖

#### 5. 什么情况下需要考虑引入

如果未来 WeKnora 演进到以下场景，可以重新评估：

| 触发条件 | 推荐方案 |
|---|---|
| **docreader 拆成多个独立服务**（OCR 服务、PDF 渲染服务、格式转换服务各自独立部署） | 考虑 NATS 或 Redis Streams 做服务间异步通信 |
| **多区域部署，需要跨区域事件同步** | 考虑 Kafka 或云厂商的托管消息服务 |
| **需要与外部系统做事件驱动集成**（如知识库变更通知 → 触发外部 CI/CD、审计系统） | 考虑 webhook 网关或轻量事件总线（如 CloudEvents + NATS） |
| **Agent 工具执行需要持久化工作流编排**（多步骤工具调用，中间状态需要持久化恢复） | 考虑 Temporal 或类似工作流引擎 |

### 建议

**保持现状，不做过度设计。** 当前的通信架构（HTTP/REST + gRPC + Redis + EventBus + asynq）完全能支撑 WeKnora 的规模和功能需求。如果未来确实需要服务拆分，再按需引入轻量级消息中间件（推荐 NATS 或直接用 Redis Streams，而非重量级的 Kafka）。

---

## 五、服务部署拓扑

### 最小部署（Lite 模式）

```
┌─────────────────────┐
│   主服务器 (all-in-one) │  ← sqlite + sqlite_fts5 + 本地 Ollama + 本地文件存储
│   内嵌前端静态文件      │     零外部依赖
└─────────────────────┘
```

### 标准部署

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐
│  主服务器      │    │  docreader   │    │  python-service  │    │  frontend    │
│  :8080        │───▶│  :50051      │    │  :50052          │    │  (nginx 静态) │
│               │───▶│              │    │                  │    │              │
└──────┬───────┘    └──────────────┘    └──────────────────┘    └──────────────┘
       │
       ├──▶ PostgreSQL
       ├──▶ Redis
       ├──▶ MinIO / S3
       └──▶ Milvus / Qdrant / ...
```

### 完整部署

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐
│  主服务器 ×N   │    │  docreader ×N │    │  python-service  │    │  frontend    │
│  (负载均衡)    │───▶│  (gRPC LB)   │    │  (金融数据分析)   │    │  (CDN)       │
│               │───▶│              │    │                  │    │              │
└──────┬───────┘    └──────────────┘    └──────────────────┘    └──────────────┘
       │
       ├──▶ PostgreSQL (主从)
       ├──▶ Redis Cluster
       ├──▶ MinIO / S3
       ├──▶ Milvus Cluster
       └──▶ asynq workers (可独立部署)
```

---

## 六、技术栈汇总

| 层 | 技术 |
|---|---|
| **主服务器** | Go 1.26, Gin, GORM, uber/dig, Viper, asynq |
| **文档解析** | Python, FastAPI, gRPC, MarkItDown, LibreOffice, Playwright |
| **金融数据分析** | Python, FastAPI, DuckDB, pandas, numpy, zettaranc |
| **前端** | Vue 3, Vite, TypeScript, tdesign-vue-next, xterm.js, noVNC |
| **CLI** | Go, Cobra |
| **SDK** | Go, net/http |
| **桌面** | Go, Wails |
| **小程序** | JavaScript, 微信小程序框架 |
| **消息/队列** | Redis (Pub/Sub + Stream), asynq (任务队列), EventBus (进程内) |
| **数据库** | PostgreSQL, SQLite, MySQL |
| **向量库** | Milvus, Qdrant, Weaviate, ParadeDB, OpenSearch, Elasticsearch, Tencent VectorDB, Doris, Volc VikingDB |
| **对象存储** | MinIO, S3, 腾讯云 COS, 阿里云 OSS, KS3, TOS |
| **MCP** | Streamable HTTP (内置), 旧版 Python stdio/SSE (已弃用) |
