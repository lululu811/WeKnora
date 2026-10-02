# 数据库表结构

WeKnora 使用 **PostgreSQL** 作为主库（ParadeDB/pgvector 为检索增强变体），SQLite 用于 Lite 模式。

本文档分三部分：

- [一、概览](#一概览) —— 表的分类与规模
- [二、本 fork 的改动](#二本-fork-的改动) —— **你相对上游新增的部分（重点）**
- [三、备份与恢复](#三备份与恢复) —— 升级前该做的事

> 文档中的表结构以**实际运行库**（PostgreSQL 17）为准，而非仅凭迁移文件推断。
> 迁移文件与运行库若不一致，以运行库为准，并在修订迁移时对齐。

---

## 一、概览

当前库共 **80 张表**（`public` schema）。其中 `spatial_ref_sys` 由 PostGIS 扩展自带，非本项目创建；**本项目自己的表是 79 张**。

按用途分组：

| 分组 | 表 |
|---|---|
| **知识与检索** | `knowledge_bases`, `knowledges`, `chunks`, `embeddings`, `chunk_revisions`, `chunk_images`, `temporary_documents`, `vector_stores` |
| **知识库增强** | `knowledge_tags`, `knowledge_tag_relations`, `knowledge_processing_spans`, `wiki_folders`, `wiki_pages`, `wiki_page_revisions`, `wiki_page_issues` |
| **租户与权限** | `tenants`, `users`, `tenant_members`, `tenant_invitations`, `tenant_api_keys`, `organization_*`, `resource_access_grants`, `resource_bindings`, `resources` |
| **会话与消息** | `sessions`, `messages`, `message_artifacts`, `message_suggestion_sets`, `message_suggestion_events`, `agent_shares`, `kb_shares` |
| **Agent 与工具** | `custom_agents`, `mcp_endpoints`, `mcp_services`, `mcp_metadata`, `mcp_oauth_*`, `mcp_tool_approvals`, `tenant_skills`, `tenant_skill_catalog`, `tenant_skill_snapshots`, `tenant_sandbox_configs` |
| **记忆系统** | `memory_items`, `memory_subjects`, `memory_item_embeddings`, `memory_tombstones`, `memory_doc_affinity`, `memory_extraction_sessions`, `memory_topic_stats` |
| **嵌入渠道** | `embed_channels`, `im_channels`, `im_channel_sessions` |
| **模型与存储** | `models`, `model_catalog_configs`, `storage_backends`, `data_sources` |
| **系统运维** | `schema_migrations`, `system_settings`, `audit_logs`, `sync_logs`, `task_pending_ops`, `task_dead_letters`, `auth_tokens`, `browser_*` |
| **自选股（本 fork）** | `stock_watches` + 见[第二节](#二本-fork-的改动) |

### 迁移文件在哪

| 方言 | 路径 | 数量 |
|---|---|---|
| PostgreSQL（versioned） | `migrations/versioned/` | 116 个 up |
| SQLite | `migrations/sqlite/` | 35 个 up |
| MySQL | `migrations/mysql/00-init-db.sql` | 单文件基线 |
| ParadeDB | `migrations/paradedb/` | 基线 + 向量迁移 |

```bash
make migrate-version    # 看当前版本
make migrate-up         # 应用
```

`schema_migrations` 表记录当前版本与 `dirty` 标志。

---

## 二、本 fork 的改动

### 结论：迁移层面只新增了 1 张表

对比上游 `main` 与本分支的 `migrations/versioned/`，**CREATE TABLE 数量 76 → 77**，唯一新增的是 `stock_watches`（自选股 / 个股追踪）。

### 2.1 `stock_watches` —— 自选股

**迁移**：`migrations/versioned/000115_stock_watches.up.sql`（PG）、`migrations/sqlite/000034_stock_watches.up.sql`（SQLite）

```sql
CREATE TABLE stock_watches (
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    exchange   VARCHAR(8)  NOT NULL DEFAULT '',
    sort_order INTEGER     NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id, thscode)
);
CREATE INDEX idx_stock_watches_user_tenant_order
    ON stock_watches (user_id, tenant_id, sort_order, created_at);
CREATE INDEX idx_stock_watches_tenant_id
    ON stock_watches (tenant_id);
```

**索引各自的用途**：

| 索引 | 服务的查询 |
|---|---|
| `stock_watches_pkey (user_id, tenant_id, thscode)` | 「我在这个工作区里这只票的那一行」；也是 add 的幂等保证——双击变成 upsert 而非重复行 |
| `idx_stock_watches_user_tenant_order` | 主读路径：按显示顺序列出我的自选股。列顺序与 `ORDER BY sort_order, created_at` 一致，让排序走索引而非扫表+排序 |
| `idx_stock_watches_tenant_id` | 删除工作区时的批量清理：`DELETE WHERE tenant_id = ?` |

**设计要点**（这些是有意为之，不是遗漏）：

- **复合主键含 `tenant_id`**：同一用户在不同工作区有两份独立自选股，一个工作区的标的不会泄漏到另一个。租户删除也因此是一次带索引的 DELETE。
- **无外键**：自选股不属于 WeKnora 的任何聚合。标的空间在 python-service 的 DuckDB 里（本 schema 无法引用），水合由调用方在读取时通过 `/api/quotes` 完成。
- **无代理主键**：调用方唯一能寻址的就是「我这只票的那一行」，再加一个 id 等于给同一个事实两个身份。天然主键同时让「添加」变成幂等操作。
- **`thscode` 不用 GORM 默认命名**：Go 结构体在 `internal/types/stock_watch.go`，必须写 `gorm:"column:thscode"`，否则 GORM 会折成 `ths_code` 而迁移建的是 `thscode`（与 python-service 的 `v_symbol.thscode`、API 参数同名）。
- **刻意不建模成本/持仓/盈亏**：那需要十进制定点金额、除权除息调整和交易流水才能算对；一个可空的 `cost` 浮点列会在任何发生过拆股的持仓上静默输出错误的盈亏。注释明确说「等需求真实存在时再单开表」。

**代码位置**：

| 层 | 文件 |
|---|---|
| 类型 | `internal/types/stock_watch.go` |
| 仓储 | `internal/application/repository/stock_watch.go` |
| 服务 | `internal/application/service/stock_watch.go` |
| 处理器 | `internal/handler/stock_watch.go` |
| 前端 API | `frontend/src/api/watchlist.ts` |
| 前端页面 | `frontend/src/views/watchlist/Watchlist.vue` |

**约束**：`thscode` 必须匹配 `^\d{6}\.(SH|SZ|BJ|HK|US)$`（`IsValidStockWatchCode`），与 python-service 的 `_THSCODE` 规则保持同步——不一致会导致「存得进去、永远显示无数据且用户看不出原因」。单用户单工作区上限 500 条（`MaxStockWatchesPerUser`），因为列表每行都要向本地 DuckDB 发一次行情查询。

### 2.2 自选股池：条件 / 事件 / 通知

自选股从「一份列表」演进为「一个追踪池」时涉及 4 张表。**当前状态：代码在 `feat/watchlist-pool` / `feat/watchlist-conditions` 分支，迁移 000116 尚未合并到 `mine`**。

> 📌 **第 5 张表已落地**：`stock_watch_diaries`（每日 LLM 观察日记），
> 迁移 `000120`（PG）/ `000039`（SQLite）；`note` 列同时加宽——PG 侧改为
> `TEXT`（`000119`），SQLite 侧 `VARCHAR(500)`（`000038`）。
> 设计与决策记录见 [`watchlist-diary-design.md`](watchlist-diary-design.md)
> 与 [`watchlist-diary-decisions.md`](watchlist-diary-decisions.md)。


#### 2.2.1 `stock_watches` 增列（`state` / `note`）

来自 `000116_stock_watch_pool_state`：

```sql
ALTER TABLE stock_watches ADD COLUMN IF NOT EXISTS state VARCHAR(16)  NOT NULL DEFAULT 'observing';
ALTER TABLE stock_watches ADD COLUMN IF NOT EXISTS note  VARCHAR(200) NOT NULL DEFAULT '';
```

`state` 是用户对这只票的**自己的立场**，只由用户改变：

```
observing → holding | dropped
holding   → observing | dropped
dropped   → observing
```

`triggered` 是预留状态：目前没有任何代码或 API 会把行提升为 `triggered`，它留给未来的「买点触发」。把列定义得通用，意味着将来那个写入方不需要改 schema。

`note` 是跟踪理由，刻意限长 200。两列都是 `NOT NULL DEFAULT`，所以读路径永远不必判断 NULL 是什么意思——「没备注」是空串。

#### 2.2.2 `stock_watch_events` —— 追加式事件日志

```sql
CREATE TABLE stock_watch_events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,   -- added | state_changed | note_changed
    thscode    VARCHAR(16) NOT NULL,
    from_state VARCHAR(16) NOT NULL DEFAULT '',
    to_state   VARCHAR(16) NOT NULL DEFAULT '',
    note       VARCHAR(200) NOT NULL DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stock_watch_events_user_tenant_created
    ON stock_watch_events (user_id, tenant_id, created_at);
CREATE INDEX idx_stock_watch_events_tenant_id ON stock_watch_events (tenant_id);
```

> 📌 本机库的这张表还多一个 `eval_date DATE` 列，是手工加的，未写进 000116 迁移。

- **只追加**：应用层没有任何 UPDATE 或 DELETE。每行是一个事实（「这只票进池了」「用户改成 holding」「用户改了理由」），不是当前状态的镜像——所以读日志能回答「这行为什么变成这样」，而 `stock_watches` 那行在时刻过去后答不了。
- 这里用**代理主键**：与 `stock_watches` 不同，事件不被调用方寻址，它是一条流。
- 事件上的 `note` 是**变化瞬间的快照**，不是对当前备注的引用——用户后来改写备注，不能追溯篡改历史记录。
- `kind` 是通用 VARCHAR 而非 enum：将来加「买点触发」不需要迁移。

#### 2.2.3 `stock_watch_conditions` —— 条件筛选

```sql
CREATE TABLE stock_watch_conditions (
    id             VARCHAR(36) PRIMARY KEY,
    user_id        VARCHAR(36) NOT NULL,
    tenant_id      BIGINT      NOT NULL,
    thscode        VARCHAR(16) NOT NULL,
    field          VARCHAR(24) NOT NULL,   -- 指标字段
    op             VARCHAR(8)  NOT NULL,   -- 比较运算符
    value          DOUBLE PRECISION NOT NULL,
    last_satisfied BOOLEAN,                -- 上次评估是否满足
    last_eval_date DATE,                   -- 上次评估日期
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_stock_watch_conditions_unique
    ON stock_watch_conditions (user_id, tenant_id, thscode, field, op, value);
CREATE INDEX idx_stock_watch_conditions_tenant_user
    ON stock_watch_conditions (tenant_id, user_id);
```

唯一索引保证「同一标的同一条件不重复添加」。

#### 2.2.4 `stock_watch_notifications` —— 通知队列

```sql
CREATE TABLE stock_watch_notifications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    payload    TEXT        NOT NULL DEFAULT '',
    ok         BOOLEAN     NOT NULL DEFAULT false,
    error      TEXT        NOT NULL DEFAULT '',
    attempts   INTEGER     NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stock_watch_notifications_user_tenant_created
    ON stock_watch_notifications (user_id, tenant_id, created_at);
```

投递结果与重试次数持久化，失败可追溯。

---


#### 2.2.5 `stock_watch_diaries` —— 每日观察日记

**迁移**：`migrations/versioned/000120_stock_watch_diaries.up.sql`（PG）、`migrations/sqlite/000039_stock_watch_diaries.up.sql`（SQLite）

| 列 | 类型 | 说明 |
|---|---|---|
| `user_id` / `tenant_id` | VARCHAR(36) / BIGINT | 与 `stock_watches` 同域，同一人在两个空间各有一份日记 |
| `thscode` | VARCHAR(16) | 被观察标的 |
| `trade_date` | DATE | **所属交易日**，不是写入日。作业在 D+1 早上 08:30 报告 D 日收盘 |
| `verdict` | VARCHAR(16) | `buy`/`hold`/`sell`（未持仓）、`keep`/`tighten`/`exit`（已持仓）、`none`（数据不足） |
| `confidence` | SMALLINT | 模型自评 0-5 |
| `reasons` / `body` | TEXT | 一句话依据 / 日记正文 |
| `model_id` | VARCHAR(64) | 写这篇的模型，被换掉时能从历史里看出来 |
| `readings` | TEXT | 当时那五个读数的 JSON 快照，使日记可被事后核对 |

**唯一索引** `(user_id, tenant_id, thscode, trade_date)` 就是幂等键：同一天重跑
作业是覆盖而不是追加。

**不建模的东西**：成本、数量、盈亏（理由同 `stock_watches`）。`holding` 行的
`verdict` 回答的是「当初关注它的理由还站得住吗」，不是「要不要止损」。

**谁写它**：模型写这张表，**永远不写** `stock_watches.state`。状态机是用户自己的
立场，采纳建议是一次独立的、由用户点击发起的 POST。

## 三、备份与恢复

数据在两个 Docker 卷里：`postgres-data`（数据库）、`data-files`（上传文件）。

```bash
# 逻辑备份
docker exec WeKnora-postgres pg_dump -U postgres -d WeKnora > backup.sql

# 恢复
docker exec -i WeKnora-postgres psql -U postgres -d WeKnora < backup.sql
```

⚠️ `docker compose down -v` 会**删除数据卷且不可恢复**。升级前请先备份。
