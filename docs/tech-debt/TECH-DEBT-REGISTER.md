# WeKnora 技术债登记表（2026-10-03，commit b03f8963f，分支 mine）

8 个只读 subagent 分片审计 + 主进程逐条复核。每条都有 file:line 证据；标 `[已复核]` 的由主进程直接 grep / 实测确认。
分片明细见同目录 8 份报告 + `corrections.md`。

## 总览

| 维度 | 数字 |
|---|---|
| 审计范围 | 6 个模块，~1.05M 行（Go 根 256k prod / 193k test，前端 279k，Python 43k，cli 43k，client 9k） |
| S1 | 6 |
| S2 | 18 |
| S3 | 20 |
| 生产代码 `_ =` 忽略错误 | 618 处 |
| 零测试生产包（Go） | 22 个，最大 `internal/types/interfaces` 5205 行 |
| 根模块 `-race` CI | 0（唯一在 cli.yml） |

---

## S1 — 建议本周处理

### 1. `python-service` `/query/` 白名单可读任意文件 + 默认无鉴权 + 端口对外发布 `[已复核]`
- **位置**：`python-service/main.py:47-58`（`_FORBIDDEN_SQL`）、`main.py:268-289`（`_read_only_sql`）、`main.py:125-137`（`require_api_key`）、`docker-compose.yml:1069,1080`
- **证据**：白名单只禁 18 个写/pragma **关键字**；DuckDB 的文件读取是**表函数**。主进程实测：`SELECT * FROM read_csv('/etc/hosts')` → 返回 8 行。`read_only=True` 不阻止读文件。compose 里 `ports: "50052:50052"` 且 `WEKNORA_PY_SERVICE_API_KEY` 默认空 → `require_api_key` 直接 `return` 放行。
- **影响**：宿主机上任何能连 `localhost:50052` 的进程（浏览器里的恶意页、误配端口转发）无需凭据即可读容器内任意可解析为 CSV 的文本，并可用 `glob()` 枚举文件系统。
- **修复**：①`_read_only_sql` 加表函数黑名单；②`duckdb.connect` 传 `allow_external_access=false`（原生开关，一次关掉所有 `read_*`/`*_scan`，比正则可靠）；③API key 未设置时 fail-fast，或 50052 从 `ports` 改 `expose`（docreader 就是这么做的，见 `docker-compose.yml:445`）。
- **工作量**：S

### 2. IM QA worker 无 panic 边界，一次 panic 打死整个进程 `[已复核]`
- **位置**：`internal/im/qaqueue.go:260`（裸调 `q.handler(req)`）、`qaqueue.go:122`（`go q.runWorker(i)`）、`internal/handler/im.go:485`（`go func()` 脱离请求栈）
- **证据**：`grep -c 'recover()' internal/im/qaqueue.go` = **0**。`q.handler` → `executeQARequest` → 完整 agent + 检索 + 文档解析 + 沙箱链路。唯一保护是 gin `Recovery()`，但这两处都是裸 goroutine，Go 运行时不 recover 即终止整个进程。
- **影响**：该链路上任何第三方 SDK 的 nil 解引用/越界都会让**所有**在线用户、正在跑的 RAG 任务、正在跑的 agent turn 一起挂掉。`internal/agent/tools/registry.go:288` 的 `executeRecovered` 已在工具层做同样防护，说明模式已知，只是没覆盖 IM。
- **修复**：`runWorker` 里给 `q.handler(req)` 包 `defer recover()`（同 `executeRecovered` 形状，recover 后回一条错误回复）；`handler/im.go:485` 同样兜底。
- **工作量**：S

### 3. `tenant_members` 唯一索引在 SQLite 上无 `deleted_at IS NULL` 谓词 `[已复核]`
- **位置**：`migrations/sqlite/000000_init.up.sql:357`、`migrations/versioned/000043_tenant_rbac.up.sql:40-42`、`internal/application/repository/tenant_member.go:169-170`
- **证据**（主进程 grep 双文件对比）：
  ```sql
  -- sqlite/000000_init:357   ON tenant_members(user_id, tenant_id);              ← 全表唯一
  -- versioned/000043:40-42   ON tenant_members(user_id, tenant_id) WHERE deleted_at IS NULL;  ← 部分唯一
  ```
- **影响**：Lite/SQLite 上成员被移除 → 软删行仍占唯一槽位 → 重新邀请触发全表唯一冲突 → `isDuplicateMembership` 误判成"已经是成员"返回 409；而邀请已被 `MarkStatusIfPending` 提前置为 accepted，**重试也失败**。用户永久无法重新加入该租户。Postgres 无此问题。
- **修复**：`migrations/sqlite/` 加一个编号迁移，`DROP INDEX` 后按 Postgres 形态重建部分索引（SQLite 3.8+ 支持）。建索引前先查历史脏数据（软删行 + 活动行同 `(user,tenant)`）。同步 bump `expectedSQLiteMigrationVersion`。
- **工作量**：S

### 4. `mcp_metadata` 表 + `mcp_services.usage_instructions` 列在 SQLite 迁移里完全缺失 `[已复核]`
- **位置**：`migrations/versioned/000092_mcp_metadata.up.sql:3`（PG 有）、`migrations/sqlite/`（**grep 零命中**）、`internal/container/container.go:917`（`case "sqlite"` 只切 DSN，不裁 service）
- **证据**：Lite 构建跑的是**同一套 service**（全仓无 `EDITION`/`lite` 条件装配），MCP handler 照常注册。`container.go:987` 对迁移失败只 `logger.Warnf` 后继续启动 → **启动期不报错**。
- **影响**：Lite 用户点开任一 MCP 服务的"目录/刷新" → `no such table: mcp_metadata`，且会被误判成 MCP 连通性问题。仓储层 `mcp_metadata.go:31` 甚至已写好 SQLite 方言分支，说明 schema 本就该支持。
- **修复**：`migrations/sqlite/` 补 `mcp_metadata`（tools 列 TEXT + `CHECK(json_valid(...))`，对应 PG 的 `JSONB NOT NULL`）+ `ALTER TABLE mcp_services ADD COLUMN usage_instructions`，带 `ON DELETE CASCADE` 外键；补进 `migration_sqlite_versioned_schema_test.go` 的白名单。
- **工作量**：S

### 5. `/api/v1/auth/login` 无限流、无账号锁定，且响应可枚举账号状态 `[已复核]`
- **位置**：`internal/router/routes_auth_tenant.go:216`、`internal/middleware/auth.go:38-40`、`internal/application/service/user.go:229-265`
- **证据**（主进程读路由文件）：
  ```go
  publicAuthRL := middleware.PublicAuthRateLimit()
  r.POST("/auth/register-by-invite", publicAuthRL, handler.RegisterByInvite)
  r.POST("/auth/invitations/lookup",  publicAuthRL, handler.LookupInvitationByToken)
  r.POST("/auth/login", handler.Login)     // ← 无限流
  ```
  service 侧无失败计数，且 `"Account is disabled"` 与 `"Invalid email or password"` 是两种不同响应。
- **影响**：可无限速撞库（bcrypt cost 10 ≈ 100ms/次），并直接枚举出哪些邮箱已注册、哪些账号被禁用。
- **修复**：给 login/register/auto-setup/refresh 挂 `publicAuthRL`（中间件已存在，改动最小）；统一失败文案；per-account 失败计数+指数退避另立。
- **工作量**：S（限流+文案）/ M（落库锁定）

### 6. `halo.fetch_external` 在事件循环上同步跑全部外网请求，可阻塞 10 分钟以上
- **位置**：`python-service/halo/analyze.py:1144`、`python-service/halo/external.py:160-166`（限速器持锁 `time.sleep`）、`external.py:268/293/302`（退避同步睡）
- **证据**：`fetch_external` 声明 `async def`，但 12 个数据桶全是同步 `urllib`（`external.py` 全部 `extdata.*` 无一个 async），`out[name] = fn()` 直接内联执行。同模块 `analyze.py:1179` 与 `pipeline.py:419` 都正确 `to_thread` 了，唯独这个漏了。
- **影响**：`/halo/score?include_external=true` 串行打 12 子域约 20+ 次 HTTP，期间该 worker **所有**请求（含 `/health`）挂起 → `docker-compose.yml:1092` 的 HEALTHCHECK 判失败 → **重启容器**，把一次慢外网调用放大成重启循环。
- **修复**：`await asyncio.to_thread(fn)`，语义不变；再给 `analyze()` 加 `asyncio.wait_for` 上限。
- **工作量**：S

---

## S2 — 建议本迭代处理

| # | 债项 | 位置 | 关键事实 | 工作量 |
|---|---|---|---|---|
| 7 | **halo「单位声明缺失=跳过该页」不变式在规则通道被反向实现** | `halo/extractor.py:263-264`、`_scan_page:525-526`；对照 `facts_finance.py:232-235` | `normalize_to_yuan(None)` 按 1:1 当元 → 资产负债表缺单位声明时值小 1e4~1e8 倍；无对账口径的字段（fixed_assets/inventory）会被 `promote_by_pipeline` 升为 `verified`，带 1e6 倍量纲错误进 `score_halo` 且无缺失标记。`test_halo_extractor.py:83-85` 还把相反行为锁死了 | S |
| 8 | **`zettaranc/filters.py` 复刻了 AGENTS.md 明文警告的 `period` 陷阱** | `zettaranc/filters.py:124-143,146-160`；正确写法 `halo/reconcile.py:203-233` | 两处 `ORDER BY period DESC` 永不选 annual 且不看 `fiscal_period`。实测选中分布只出 Q2（5569 只）；`600062.SH` 取到 **2022 Q4** 而非 2026 Q4。半年后数据重算若某票 quarterly 缺失回落到旧年份，会静默选中陈旧记录 | S |
| 9 | **tos/minio 完全没有传输层超时与上限** `[已复核修正]` | `file/tos.go:104-108,122-132`、`file/minio.go:36-40,58,63` | 5 个 S3 系 provider 都有 `objectStorageTransferContext`×5，`tos`/`minio` 是 **0**；`config.Timeout` 只在 `utils/security.go:805` 的 `NewSSRFSafeHTTPClientWithTransport` 里被消费，而 tos/minio 传的是 `RoundTripper` 根本不走 `http.Client` → **既无 30s 截断，也无 30min 兜底**。对象存储端点 accept 后静默时 worker/goroutine 可被无限期 pin 住 | S |
| 10 | **23 处 `UpdateKnowledge` 返回值被丢弃 → 文档卡 processing 永不终结** | `knowledge_process.go` 21 处 + `:637` | `ParseStatus="failed"` 后直接丢弃写结果并 `return nil`（asynq 认为成功不再重试）→ DB 抖动时留下僵尸文档，前端进度条永远转圈，日志里既无错误也无文档 ID | M |
| 11 | **删除路径跨 5 个数据源无事务** | `knowledge_delete.go:426-620,626-704` | 10.4 万行 service 层只有 3 处 `.Transaction(`，全在 storagebackend/vectorstore。`DeleteKnowledgeList` 成功但 `AdjustStorageUsed` 失败 → 配额永久虚高，无管理入口可修，只能改库 | M |
| 12 | **172 处把 `err.Error()` 原样回显给客户端** `[已复核]` | `middleware/error_handler.go:26-33`；`WithDetails(err.Error())` **152 处** + 裸 `c.JSON(500, err.Error())` **22 处** | gorm 错误形如 `Error 1146 (42S02): Table 'weknora.kb_shares' doesn't exist`、`dial tcp 10.0.3.17:5432: connection refused` 进响应体。日志侧已有 `SanitizeForLog`，响应侧无对等处理 | M |
| 13 | **`LoadMessages` 的 `limit` 传负数会让 LIMIT 子句消失** `[已复核]` | `handler/message.go:87,103-107` → `repository/message.go:81-89` | handler 只判 `convErr != nil`，`limit=-5` 原样传给 gorm，`clause/limit.go:16` 的 `>= 0` 守卫把它丢弃 → 返回该 session **全部**消息。`limit=100000000` 则真发该上界。路由挂 `apiKeyChat` 能力 | S |
| 14 | **CORS `AllowOrigins:["*"]` + `AllowCredentials:true`** `[已复核]` | `internal/router/router.go:139-150` | gin-contrib/cors v1.7.7 在 AllowAllOrigins 时同时发 `Allow-Origin:*` 与 `Allow-Credentials:true`。当前靠浏览器拒绝而无害，但代码注释自己写着"若引入 cookie 认证必须先换受控清单"——是注释里写清楚了、代码里留着地雷的形态 | S |
| 15 | **mysql/paradedb 迁移从未接线 + schema 漂移零守卫** | `migrations/mysql/`（README 自述不被执行）、`internal/database/migration_sqlite_versioned_schema_test.go:23,49` | parity 测试只断言**手写**的 22 表 + 21 表白名单。实测 PG 有 / SQLite 无：`embeddings, mcp_metadata, organization_members, wiki_log_entries` —— `mcp_metadata` 即第 4 条，至今无声 | M |
| 16 | **3 处无方言判断的 Postgres 专有 SQL，Lite 上必 500** | `repository/wiki_page.go:370`（`to_tsvector`）、`organization.go:96`（`ILIKE`+`id::text`）、`user.go:265` | 同文件 `wikiSearchMatchOp()` 已正确做方言分支 → 是遗漏非设计。`wiki_page_test.go` 三个 `List` 测试都**不设 `Query` 字段**，CI 里零覆盖 | S |
| 17 | **`getJwtSecret` 在 `rand.Read` 失败时 panic；未配 `JWT_SECRET` 时静默降级为每进程随机** | `application/service/user.go:81-95`，7 个调用点 | `sync.Once` 遇 panic 不标记已完成 → 每次调用重新 panic。在 `ValidateToken`（每个鉴权请求）上。**更常见**：多副本各自随机密钥 → A 签的 token 在 B 验签失败，用户表现为"随机掉线"，重启全掉 | S |
| 18 | **`ProcessWikiIngest` 770 行 / CC=108 / 嵌套 10 层** | `wiki_ingest_batch.go:260-1029` | 一个函数同时做 payload 解析、租户校验、并发闸门、模型获取、行认领、map/reduce 双阶段编排、进度落盘、重试与尾随调度。同文件 `mapOneDocument` 446 行、`reduceSlugUpdates` 417 行同样超标 | L |
| 19 | **i18n 门禁方向反了：代码引用但语言包缺失的 key 全部漏检** `[已复核方向，数字待定]` | `i18n/localeKeyAudit.ts:388`、`KLineWorkspace.vue:98,406` | `collectReferencedLocaleKeys` 先遍历**语言包已有**的 key 再筛引用 → 代码写了但语言包没有的 key 进不了待检集。`npm run check-i18n` 13/13 全绿，但 `kline.volumeLabel`、`kline.requestRejectedHint` 确实不在 en-US.ts。`KLineWorkspace.vue:406` 更糟：key 不存在 → vue-i18n 返回 key 本身 → `{message}` 插值丢失，而 `loadError.message` 来自服务端 body 且**未经 escape 进 v-html** | M |
| 20 | ~~`financial_indicator_detail.go` 绕过统一排序来源且零测试~~ **主指控已证伪**（2026-10-05） | `hithink_finance/financial/financial_indicator_detail.go`、`period.go:21` | 实查 `financials.duckdb`：`v_financial_indicators_detail` 无 `period`/`period_end_ms`/`fiscal_year`/`fiscal_period` 四列，**照原文"统一排序来源"去改会 binder 报错**；该表 `report` 实测全表统一为 `'YYYY-N'`，字典序=时间序，现排序正确。已改为：补 `financial_indicator_detail_test.go`（含防误改断言）+ `Description()` 补 `YYYY-N`/`FY→-4`。**另修正 `query/sql_query.go` 描述里两条真·退化排序的趋势范例**（实测返回乱序且漏 2025FY） | 已结案 |
| 21 | **`python-service/` 与 `client/` 两个模块 CI 零覆盖** `[已复核]` | `.github/workflows/` | `grep -rn python-service .github/workflows/` = **NONE**（31 个 pytest 文件、99KB `main.py` 从不在 runner 上跑，而 `test_halo_cninfo.py` 正是 `period` 陷阱的回归网）。`client/` 只被 `go-lint.yml` lint，`app.yml:16,44` 显式 `!client/**` 排除，**没有 workflow 跑它的 go test** | S |
| 22 | **3 个 workflow 的 push 只绑 `main`，在 `mine` 上永不触发** `[已复核]` | `anydoc.yml:8`、`go-lint-cache.yml:12`、`docker-image.yml` | `main` 按分支模型零提交、origin 零 tag。`anydoc.yml` 是**唯一编译 anydoc build tag 并跑 `cargo audit`** 的路径 —— 默认 `WITH_ANYDOC=1` 在进程内解析不可信上传文件，这条是 S1 攻击面的唯一自动防线 | S |
| 23 | **SQLite `BatchUpdateChunk*` 逐条 UPDATE 且完全吞掉错误** | `retriever/sqlite/repository.go:355,362` | 返回值直接丢弃；Postgres 同名方法（`retriever/postgres:708`）按 tag 分组批量 UPDATE **并检查 `result.Error`**。Lite 上 FAQ 批量启停退化为 N 次单行 UPDATE，且向量库与 `chunks` 表可能永久不一致 | S |
| 24 | **前端 token 全存 localStorage + 69 处 v-html 组成提权链** | `stores/auth.ts:212,217`、`utils/request.ts:75` | JWT + refresh token 同时暴露。`utils/security.test.ts` 只有 38 行 3 用例，**0 条 XSS payload 断言**，净化行为没被钉死 | M（token 迁 cookie 需后端联动） |

---

## S3 — 可维护性债

| # | 债项 | 关键数字 / 位置 |
|---|---|---|
| 25 | `AgentStreamDisplay.vue` 69 行模板逐字重复两遍 | `:155-223` vs `:477-545` 字节级一致；改一处必漏另一处 |
| 26 | `WikiBrowser.vue` 的 wiki slug 未转义拼进 HTML 属性 | `:1531` vs `AgentStreamDisplay:904`（后者已 escape）—— 两份实现已分叉 |
| 27 | 5 处 S3 系 provider 各写一遍 7 个方法 | 7×7 = 2466 行；`#3306` 修复手工同步漏了 2 个（即第 9 条） |
| 28 | `knowledge_tags` 模型无 `gorm.DeletedAt` 但表有 `deleted_at` 列 | `types/tag.go:12` / `migrations/versioned/000001:251` → `tag.go:139` 实为硬删，`knowledge_tag_relations` 残留孤儿行使 `DeleteUnusedTags` 永不可用 |
| 29 | `.golangci.yml` 只显式开 3 个 linter；安全/资源类整组关闭 | 实测生效 7 个（多出 4 个来自 v2 默认集）；`gosec/bodyclose/noctx/rowserrcheck/sqlclosecheck` 全关；`errcheck` 无 settings → `check-blank=false` → 618 处 `_ =` 零拦截 |
| 30 | 根 Go 模块 1214 个测试文件在 CI 里从不加 `-race` | 全仓唯一 `-race` 在 `cli.yml:49`（小模块）；`app.yml:141-144` 是全量无 race |
| 31 | `.env.example` 与代码实际读取差 21 个变量 | 代码读 230 个；4 个被 Go 直接 `os.Getenv` 且**静默改变启动行为**：`WEKNORA_WEB_DIR`、`MODELS_CONFIG`、`BUILTIN_MODELS_CONFIG`、`JIEBA_DICT_DIR`（`JIEBA_DICT_DIR` 缺失 → 中文召回质量下降且不报错） |
| 32 | 18 处分页解析各自为政 | 5 种不同策略（400/静默忽略 × 上界 0/100/200）；`parseAuditCursor` 被复制两遍 |
| 33 | `stream_manager` memory 后端无淘汰，`cleanup_timeout` 是死配置 | `config.go:473/794` 定义+校验，**无消费者**；`lastUpdated` 写 5 处从未读；Lite/桌面（`.env.lite:28`）下事件缓冲只增不减 |
| 34 | 4 个 200+ 行核心函数零测试 | `SearchFAQEntries`(332行/CC=63)、`executeFAQImport`(294)、`cloneFAQKnowledgeBase`(302)、`RunLint`(250) —— 覆盖 FAQ 检索/导入/克隆 + wiki 巡检四条用户主流程 |
| 35 | 5 份 datasource 同步失败收尾块绕过已有 `updateSyncRunResult` | `datasource_service.go:650-760` ×5；用 `Update` 而非 `UpdateResult` → 失败同步的计数残留上一轮的值，且不发审计事件 |
| 36 | `registerTools`/`wiki_boost` 按 SearchTarget 逐条查 KB（N+1） | 批量接口 `GetKnowledgeBasesByIDsOnly`（`knowledgebase.go:336`）**已存在却零调用**；每轮对话固定 N 次查询 |
| 37 | `ListOrgShares` 每行 3 次查询 | `organization.go:1316-1332`；50 条 = 151 次往返；`CountKnowledge` 失败被 `err == nil` 静默吞掉 |
| 38 | 12 处无保护 `c.Get(...).(uint64)` 类型断言 | 未设 key 即 panic，Recovery 把 `interface conversion: interface {} is nil` 写进响应体 |
| 39 | 响应契约三套并存 | `{success,error{code,message,details}}` / `{code:0,msg,data}` / 裸 `{"error":...}` |
| 40 | 72 个 Go 包零测试 | 最大 `internal/types/interfaces` 5205 行、`im/wechat` 955、`retriever/postgres` 857 |
| 41 | 死代码：`client/example.go` 261 行 | `ExampleUsage` 零引用且遮蔽 Go 1.21 内置 `min`（并列的 `finanserv`/`zettarancserv` 两个注册包已于 2026-10-05 删除） |
| 42 | 7 个 shell 脚本顶层无 `set -euo pipefail` | `get_version.sh` 零 set 行却被 6 处 `eval` 消费 → 失败时静默输出 unknown |
| 43 | 硬编码中文 UI 文案 159 处 + 5 条遗留 `console.log` | 集中在 `finance/`（KLineWorkspace 85、Watchlist 37）；`ModelEditorDialog.vue:1380-1415` 每次点 Ollama 检测都往生产控制台刷 |
| 44 | `t(key, '中文默认值')` 兜底 54 处 | 在"语言包缺 key"的环境下双重保险失效：key 拼错不显示 raw key 而是**静默显示中文** |

---

## 与 subagent 结论不一致处（主进程已复核修正）

1. **tos/minio「30s 整请求超时截断大文件上传」→ 结论反向。** `config.Timeout` 只被 `NewSSRFSafeHTTPClientWithTransport` 消费为 `http.Client.Timeout`；tos/minio 传的是 `RoundTripper`，根本不走 `http.Client`。实际状态是**完全无超时**（既无 30s 截断，也无 s3 系那层 30min 兜底）。修复方案不变，影响描述需改写。

2. **i18n「186 个 key 缺失」→ 数字未复现。** 主进程独立扫描：5415 个静态 `t()` key，仅 4 个顶层命名空间不存在（`klineCompare` × 4），非 186。但方向性结论成立：门禁先遍历语言包已有 key 再筛引用，代码引用缺失的 key 进不了待检集；`kline.volumeLabel` / `kline.requestRejectedHint` 逐例确认缺失。

3. **`.golangci.yml` 缺 `errcheck` → 前提错误。** Infra agent 自行用 `golangci-lint linters --config` 实测推翻了自己的任务简报：errcheck/staticcheck/ineffassign/unused 均在生效集合内（来自 v2 默认集）。真正的缺口是安全/资源类整组 105 个 linter 被禁用 + `check-blank` 默认 false。

---

## 建议执行顺序

**第 1 批（本周，S 工作量，全部为可利用/确定性故障）**
1 → 2 → 3 → 4 → 5 → 6：六个 S1，改动都不大且互相独立。其中 1 和 2 是"现在就能被触发"的，优先。

**第 2 批（本迭代，消除静默失败）**
13 → 23 → 10 → 17 → 35 → 36：全是机械替换 + 已有 helper 可用，收益是把"静默失败"变成"可见失败"。

**第 3 批（本迭代，补自动化防线）**
21 → 22 → 19：先补 CI（`client.yml` + `python-service.yml` + 修 3 个 workflow 分支），再修 i18n 门禁方向 —— **门禁修好之前，第 7/8/20 条这类"靠测试守护的不变式"补了测试也没人跑**。

**第 4 批（需要先有测试网）**
7 → 8 → 16 → 19 的补 key → 34：给 4 个零测试的核心函数补测试，然后才动 `ProcessWikiIngest`（18）和 provider 统一（27）这类大重构。

**顺带（半小时）**：14（删 `AllowCredentials: true`）、41（`git rm` 死代码）。
