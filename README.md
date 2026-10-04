# WeKnora · 社区增强版（Fork）

> **这是社区 fork，不是腾讯官方版本。** 上游为 [Tencent/WeKnora](https://github.com/Tencent/WeKnora)，
> 本仓库在其之上增加一套 **A 股投研工具链**。上游的 README 原文见 [README-upstream.md](README-upstream.md)，
> 许可与第三方组件声明见 [NOTICE.md](NOTICE.md)。
>
> 只想要一个通用知识库问答系统 → 直接用上游 Tencent/WeKnora，本仓库的默认部署虽然也够用，
> 但你不需要承担这套增量功能的维护面。

基于 [Tencent/WeKnora](https://github.com/Tencent/WeKnora) 的二次开发，额外提供一套 **A 股投研工具链**。

> ⚠️ 本仓库是社区 fork，非腾讯官方版本。上游功能与许可完全保留，详见 [NOTICE.md](NOTICE.md)。

---

## 30 秒判断：你需要哪个版本

| 你的情况 | 怎么做 |
|---|---|
| 想要一个本地知识库问答系统 | 👉 **默认部署**，三条命令，见下文 |
| 想用它做 A 股投研分析 | 👉 需要额外准备数据，见 [投研栈](#投研栈可选) |
| 只想改代码学习 | 建议先 fork 上游，定制部分可参考 `python-service/zettaranc/` |

**绝大多数用户不需要读下文第 3 节。** 默认部署不涉及任何金融数据、不需要 DuckDB、不构建 Python 服务。

## 文档索引

| 文档 | 内容 |
|---|---|
| 本文件 | 快速开始与投研栈说明（含第七节「配置速查」） |
| [NOTICE.md](NOTICE.md) | 许可、第三方组件、商标声明 |
| [DEPLOY.md](DEPLOY.md) | 部署与排障手册 |
| [docs/DATABASE.md](docs/DATABASE.md) | **数据库表结构**：80 张表总览、本 fork 新增的自选股表 |
| [docs/hithink_finance_tools.md](docs/hithink_finance_tools.md) | 投研工具族详解 |

---

## 一、快速开始（默认部署）

需要 [Docker](https://www.docker.com/) 与 [Docker Compose](https://docs.docker.com/compose/)。

```bash
git clone https://github.com/lululu811/weknora-chenliitaz.git
cd weknora-chenliitaz
cp .env.example .env
docker compose up -d
```

然后打开 **http://localhost**，跟随引导流程创建账号与知识库。

停止：`docker compose down`　｜　查看日志：`docker compose logs -f app`

启动的服务只有 5 个：

| 服务 | 说明 |
|---|---|
| `frontend` | Web 界面 |
| `app` | 后端 API |
| `docreader` | 文档解析 |
| `postgres` | 数据库 |
| `redis` | 缓存与队列 |

**起不来？** 先跑自检脚本，它会告诉你缺什么：

```bash
./scripts/doctor.sh
```

排障细节见 [DEPLOY.md](DEPLOY.md)。

---

## 二、配置模型

绝大多数配置项在 `.env` 中，已按用途分组（A 部署 / B 存储 / C 检索 / D 模型 / …）并附中文注释。**默认值可直接跑通**，常见场景只需改：

```bash
# 模型（必填，二选一）
# 用云端 API：
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com/v1

# 或用本地 Ollama（先 `ollama serve`）：
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

| 想要 | 配置 |
|---|---|
| 换模型 | 在 Web 界面「模型管理」中添加，或编辑 `config/models.json` |
| 换向量库 | `RETRIEVE_DRIVER=sqlite`（默认，零依赖）/ `milvus` / `qdrant` 等 |
| 换存储 | `STORAGE_TYPE=local`（默认）/ `minio` / `s3` |
| 加检索后端 | `docker compose --profile full up -d` |

---

## 三、投研栈（可选）

> **这一节是本 fork 的增量功能。** 不需要 A 股投研能力的话，完全可以忽略本节。

### 它提供什么

- **行情 / 财务 / 指数 / 板块数据查询**（`hithink.*` 工具族）
- **HALO 11 维基本面评分**（`halo.*` 工具族）
- **技术形态与波浪识别、选股器**（`zettaranc.*` 工具族，Python 实现在 `python-service/zettaranc/`）
- **K 线复盘终端**（前端内置组件，无需独立服务）

### 前置条件：数据

投研工具读取**本地 hithink-finance DuckDB 库**。**该数据不随本仓库分发**，需自行获取准备，然后通过 `HITHINK_DB_DIR` 指定目录。

未配置时：容器正常启动，工具调用返回明确的中文错误提示，**主流程不受任何影响**（这是有意的设计——工具注册是懒加载的）。

### 启用

```bash
# 1. 准备数据目录（含 *.duckdb 文件）
export HITHINK_DB_DIR=/path/to/your/hithink-db

# 2. 启动（加 finance profile）
docker compose --profile finance up -d
```

### 数据未就绪时会怎样

这是**预期行为**，不是故障：

```
工具 hithink.finance.query.sql 查询失败：底层数据源不可用。
可行做法：HITHINK_DB_DIR 未配置或目录中没有 *.duckdb 文件。
不启用 finance profile 不影响知识库与对话功能。
```

### 其他 profile

```bash
docker compose --profile full up -d     # 全部后端：milvus/weaviate/qdrant/neo4j/doris/langfuse/searxng
docker compose --profile searxng up -d  # 仅联网搜索
```

可组合：`--profile finance --profile searxng`

---

## 四、代码地图

本 fork 新增的代码集中在四处：

| 路径 | 内容 |
|---|---|
| `python-service/zettaranc/` | 投研框架：指标、形态、波浪、选股器（约 4,000 行 Python） |
| `python-service/halo/` | HALO 基本面评分链路 |
| `internal/agent/tools/hithink_finance/` | Go 侧 hithink 工具族（HTTP 调 python-service） |
| `frontend/src/components/workspace/kline/` | K 线复盘终端前端组件 |

工具调用链：

```
Agent 引擎 (Go)
  └─ hithink.* / halo.* / zettaranc.* 工具
       └─ HTTP → python-service:50052 (仅 finance profile)
            └─ DuckDB 只读查询 ~/.hithink-finance/*.duckdb
```

> Go 侧**不直连** `.duckdb` 文件，一律经 python-service。这样 Python 侧能单独演进，Go 侧保持薄。

其余目录（`internal/`、`frontend/`、`docreader/` 等）基本沿用上游。本仓库是独立分叉、
**不携带上游的提交历史**；要对比差异，先挂上游再比：

```bash
git remote add upstream https://github.com/Tencent/WeKnora.git
git fetch upstream
git diff upstream/main...HEAD
```

---

## 五、开发

```bash
make dev-start      # 起开发用基础设施（postgres/redis）
make dev-app        # Air 热重载跑后端 :18080
cd frontend && npm install && npm run dev
```

四个独立模块，各自构建：

| 路径 | 语言 | 说明 |
|---|---|---|
| `/` | Go 1.26 | 主服务 |
| `cli/` | Go | 独立 Go module，Cobra CLI |
| `client/` | Go | 独立 Go module，Go SDK |
| `frontend/` | TS | Vue 3 + Vite（用 **npm**，不是 pnpm） |
| `docreader/` | Python | 文档解析服务 |
| `python-service/` | Python | 投研服务（finance profile） |

测试：

```bash
make test                                  # Go
cd cli && make test                        # CLI
cd frontend && npm test && npm run type-check
cd python-service && python3 -m pytest tests/ -q
```

---

## 六、许可与数据声明

- 代码：**MIT**（上游 Copyright (C) 2025 Tencent，本 fork 新增部分同 MIT）
- 第三方组件：见 [NOTICE.md](NOTICE.md) 与 `licenses/`
- **本仓库不含金融数据**；hithink-finance 数据需自行获取

---

## 七、配置速查（本 fork 实测）

> 这一节记的是**踩过的坑**，不是配置项清单（完整清单见 `.env.example`，按 A–J 分组、带中文注释）。
> 每一条都对应一次真实的**静默**故障：不报错、不崩，只是某个能力悄悄不可用。

### 7.1 模型：每个 agent 必须显式绑定

| 事实 | 说明 |
|---|---|
| 内置 `qwen-plus` / `qwen-turbo` 默认**不可用** | `api_key: ${DASHSCOPE_API_KEY}`，而 `.env` 里没有这个变量 → 插值器刻意保留字面量 → 每次调用 401。已在 `config/builtin_models.yaml` 注释停用；重启后加载器的漂移清理会自动软删这两行（只清理 `managed_by=yaml` 的行） |
| 实际在用的对话模型 | `qwen3.7-plus`（DashScope coding 端点）与 `MiniMax-M3`；`qwen3.7-plus` 是 KnowledgeQA 的默认 |
| agent 必须带 `model_id` | 内置 agent 的 YAML 里**不带** `model_id`，直接选它对话会报 `chat model is not configured: please set model_id on agent <id>`。在「智能体 → 编辑」里选一次模型即可（会落一行定制配置） |

### 7.2 内置 agent 的「定制行冻结」陷阱（最坑的一条）

内置 agent 一旦在库里存了定制行（在 UI 里编辑过、或 API `PUT` 过），**整份 config 以库为准**：之后改 `config/builtin_agents.yaml` 再重启，**不会**回灌到那个空间。

实测踩过两次：

- `builtin-halo` 的白名单里 `hithink.finance.financial.statement.balance` 写成了 `hittink...` —— 白名单里的工具名拼错 = 该工具**永远不注册**，且不报任何错；
- `builtin-zettaranc` 的定制行停在旧版本，缺 `zettaranc.four_bricks` —— "四块砖"这个能力在对话里一直不可用。

所以：**改 YAML 只对「没有定制行」的空间生效**。已有定制行的空间要在 UI 里改，或 `PUT /api/v1/agents/<id>`（记得带**完整** config，缺字段会被整份覆盖）。

护栏（跟测试一起跑）：`internal/agent/tools/halo/whitelist_test.go`、`internal/types/builtin_agent_workbench_test.go`、`internal/agent/tools/zettaranc/whitelist_test.go` 把 YAML 与代码里的注册表/词表对齐。

### 7.3 工作台（右侧面板）

- `config.workbench: "finance"` → 渲染 `kline` 组件。工作台由 `session → agent → config.workbench` 推导，一个会话只绑一个 agent，所以 Z哥 与 HALO 可以共用同一个工作台。
- K 线面板底部「继续向 Agent 提问」里的 HALO 三条问法，**只在当前 agent 的白名单含 `halo.analyze` 时出现** —— 给没有该工具的 agent 显示，等于给用户一个必定失败的动作。

### 7.4 HALO 年报链路

- 运行期状态（事实库 `halo.sqlite`、年报 PDF 缓存、判分影子记录）都在 **`halo-data` 命名卷**（容器内 `/data/halo`）。别指回 `~`：容器里 `HOME=/root`，会写进容器可写层，重建即丢，而重抓一份年报要 1–3 分钟（巨潮还限速）。
- 面板上的「归档到知识库」默认落成**草稿**（设计如此：报告里约三成是模型判断）。**草稿不进检索** —— 要在知识库列表里发布/启用之后，问答才能检索到它。
- 数据铁律：取不到就标缺失，不补值、不外推。因此**非年报期 HALO 六维必然不可计算**，这是正确行为，不是 bug。

### 7.5 后台任务并发（asynq）

- 摘要 / 多模态 / 图谱 / 问题生成跑在 **enrichment 池**，默认并发 **12**。
- 若摘要模型指向**并发额度很紧**的端点（典型：DashScope coding 计划 `coding.dashscope.aliyuncs.com/apps/anthropic`），12 路并发会稳定撞 429 `concurrency allocated quota exceeded` —— 实测一天 639 条 `summary:generation` 死信。本部署已设 `WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY=2`。
- 观测点：`GET /health/readiness`（带 `X-API-Key`）的 `dead_letter_backlog`；原始档案在 `task_dead_letters` 表。
- `dead_letter_backlog` 按最新失败时间给三种判语：24 小时内的算 `CURRENTLY FAILING`，其后是 `stopped failing, backlog not yet cleared`，7 天后降级成 `historical debris`。**行不会自己消失**：一次性故障（如 Ollama 没起）留下的死信会一直占着计数，而仓库里没有删除接口（`DeleteByID` 没接出去），要清干净只能手动删 DB 行。
- 想跑快点就调大，但要盯有没有新的 429 死信。

### 7.6 本地 embedding（Ollama）

本部署**有 embedding 模型**的知识库全部指向本地 Ollama：`milkey/wemm-embedding-2b:Q4_K_M`（7 个库）与 `bge-m3:latest`（1 个库）。**Ollama 没起**时，任何需要 embedding 的后台步骤都会失败：

```
failed to get embedding vectors: Post "http://host.docker.internal:11434/api/embed":
dial tcp 192.168.65.254:11434: connect: connection refused
```

表现为 `task_dead_letters` 里多一条（且如上所述会一直留着）。起服务：`ollama serve`（或 `brew services start ollama`）。

验证要从**容器**里发（宿主机通了不代表容器的 `host.docker.internal` 通）：

```bash
docker exec WeKnora-app wget -qO- --header='Content-Type: application/json' \
  --post-data='{"model":"milkey/wemm-embedding-2b:Q4_K_M","input":"ok"}' \
  http://host.docker.internal:11434/api/embed
```

### 7.7 自检入口

`GET /health/readiness`（需鉴权）是唯一能一眼看到「死信积压 / 悬空模型引用 / 不可达工具」的地方。本 fork 用它清掉了 28 条工具可达性**误报** —— 根因是检查项自己漏了四种情况：内置 agent 没有 DB 行、能力开关注册的工具不需要白名单条目、空白名单会兜底到 `DefaultAllowedTools`、以及少数工具是故意不授予任何 agent 的。这类误报修干净，下一个人（多半是我自己）才不会重新排查一遍。
