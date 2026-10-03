# 技术债审计 — Go application service 层 (`internal/application/service/**`)

## 结论

- **修掉 `tos.go` / `minio.go` 的传输层配置**：这两个 provider 漏掉了另外 5 个已修好的 S3 系 provider 都有的超时与 transport 修复，一个是被 30s 整请求超时截断大文件上传（#3306 回归），另一个是传输无上限可永久挂死 worker。
- **补上 3 个跨仓储写操作的事务边界**：`executeKnowledgeDelete` / `cleanupKnowledgeResources` / `ProcessKBClone` 共 12+ 次跨表写入无事务，中途失败留下"chunk 已删、knowledge 行还在"这类不一致状态，只能靠后续 sweep 兜底。
- **拆掉 `ProcessWikiIngest`（770 行 / CC=108 / 嵌套 10 层）**：它是全 slice 最重的上帝函数，一个函数同时做 payload 解析、租户存活校验、并发闸门、模型获取、行认领、map/reduce 双阶段编排、进度落盘、重试与尾随调度。
- **消除 23 处未检查的 `s.repo.UpdateKnowledge` 返回值**：这是"文档卡在 processing 永不终结"的直接来源——写失败被静默丢弃，调用方返回 nil 表示成功。
- **合并 5 份 datasource 同步失败收尾块**：`ProcessSync` 里已存在正确的 `updateSyncRunResult` helper，但 5 个错误分支绕过它手写内联副本，丢失 `UpdateResult` 原子语义与审计事件。
- **把 `registerTools` / `wiki_boost` 的逐 KB 查询换成已有的批量接口**：`GetKnowledgeBasesByIDsOnly` 已经在 `knowledgebase.go:336` 存在却无人使用，agent 每轮对话按 SearchTarget 逐条查库。

## 发现

### [S1] tos.go / minio.go 缺失对象存储超时与 transport 修复，大文件上传被 30s 截断、传输可无限挂起

- **位置**：`internal/application/service/file/tos.go:104-108`、`internal/application/service/file/tos.go:122-123`、`internal/application/service/file/tos.go:132`、`internal/application/service/file/minio.go:37-40`、`internal/application/service/file/minio.go:58`、`internal/application/service/file/minio.go:63`
- **证据**：7 个 S3 系 provider 中 5 个（s3/oss/obs/ks3/cos）都调用 `objectStorageHTTPClient()` + 5 处 `objectStorageTransferContext`；tos 与 minio 两处均为 0。`tos.go:105-108` 构造 transport 时直接用原始默认配置：
  ```go
  WithHTTPTransport(&utils.SSRFValidatingRoundTripper{
      Base: utils.NewSSRFSafeTransport(utils.DefaultSSRFSafeHTTPClientConfig()),
  }),
  ```
  而 `object_storage_http.go:37-39` 明确说明了正确写法与原因：
  ```go
  func objectStorageHTTPClientConfig() utils.SSRFSafeHTTPClientConfig {
      config := utils.DefaultSSRFSafeHTTPClientConfig()
      config.Timeout = 0   // 默认 30s 是整请求超时，含 body，会截断传输 (#3306)
      return config
  }
  ```
  `DefaultSSRFSafeHTTPClientConfig()` 的 `Timeout` 默认为 `30 * time.Second`（`internal/utils/security.go:691`）。`object_storage_http.go:16-22` 注释明确写了这个 30 分钟兜底超时的存在理由："asynq workers and the main HTTP server both omit one... so a stalled body cannot pin a worker forever"——tos/minio 两个 provider 拿不到这层保护。
- **影响**：
  1. **确定性功能故障**：走 TOS 或 MinIO 后端时，`SaveFile`/`SaveBytes`/`GetFile` 全部挂在 30s 整请求超时上。上传一个 40MB 文档必然超时失败（`object_storage_http.go:19-20` 注释里点名了这个场景："Long enough for a slow 40 MB COS PUT"）。同一份文件在 COS/S3 上传成功、切到 TOS/MinIO 就失败，且错误信息是超时，很难让人联想到"后端差异"。
  2. **资源耗尽**：`tos.go:122-123` / `minio.go:58,63` 的桶探活用 `context.Background()` 无任何 deadline。对象存储端点 accept 后静默（网络黑洞、被墙、云厂商网关丢包）时，一个 asynq worker 被无限期占用，队列积压持续扩大。`NewMinioFileService` 还在 HTTP 请求路径上被调用（见 `NewFileServiceFromStorageConfig`），会直接吃掉 handler goroutine。
  3. **修复不一致的排查成本**：同一个"上传慢/上传失败"工单，换后端表现完全不同，运维需要先反推是哪个 provider 才能定位。
- **修复**：
  1. `tos.go:104-108` 与 `minio.go:37-40` 改用 `objectStorageHTTPClientConfig()`（`Timeout=0`）+ `objectStorageTransport()`（带 `ResponseHeaderTimeout=10min`），与 s3/oss/obs/ks3 对齐。
  2. `tos.go:122,132` 与 `minio.go:58,63` 改用 `objectStorageSetupContext()`（已有 30s 上限，注释在 `object_storage_http.go:64-66`）。
  3. tos/minio 的 7 个方法（`SaveFile`/`GetFile`/`DeleteFile`/`SaveBytes`/`GetFileURL`/`CopyFile`/`CheckConnectivity`）各加 `objectStorageTransferContext`，与 `s3.go:247-248` 同款。
  4. 加一条防回归测试：对 `NewFileServiceFromStorageConfig` 返回的所有 provider 断言"传输路径不含整请求 Timeout 且带 fallback deadline"。
- **工作量**：S（半天，纯机械对齐，改 2 个文件约 15 处调用点 + 1 个测试）

### [S2] 23 处 `s.repo.UpdateKnowledge` 返回值被丢弃，文档写失败被当成处理成功

- **位置**：`internal/application/service/knowledge_process.go:637,694,702,729,3435,3480,3596,3657,3668,3679,3694,3707,3717,3726,3736,3812,3825,3839,4046,4058,4080` 等
- **证据**：`knowledge_process.go:3590-3600` 是典型形态——设置失败状态后直接丢弃写结果并返回 nil：
  ```go
  knowledge.ParseStatus = "failed"
  knowledge.ErrorMessage = ErrImageNotParse.Error()
  knowledge.UpdatedAt = time.Now()
  s.repo.UpdateKnowledge(ctx, knowledge)   // 返回值未接收
  return nil                                // asynq 认为任务成功，不再重试
  ```
  `knowledge.ParseStatus = "failed"` 这一赋值语句在 `knowledge_process.go` 中出现 18 次，在整个 service 层出现 21 次，几乎每次都配一个未检查的写入。
- **影响**：
  1. **静默失败**：`UpdateKnowledge` 因 DB 连接抖动、字段约束、行已被并发删除而失败时，错误被完全丢弃。`knowledge` 的 ParseStatus 永远停留在 `pending`/`processing`，数据库里留下一个"永远在处理中"的僵尸文档——前端进度条永远转圈，用户没有任何错误提示。
  2. **排查困难**：日志里既没有错误也没有文档 ID。要发现这些问题只能靠人工比对 `parse_status` 与 `updated_at`，没有可检索的失败记录。
  3. **和第 3 条（无事务）叠加**：这条路径上"标 deleting"（`knowledge_delete.go:442`）和"标 failed"用的是两套不同代码，失败时都会留下半更新状态。
- **修复**：
  1. 抽一个 `failKnowledge(ctx, knowledge, reason) error` helper，内部统一做 `ParseStatus=failed` + `ErrorMessage=reason` + `UpdateKnowledge` 并**返回**错误；所有 21 处改为 `return failKnowledge(...)` 或至少 `if err := ...; err != nil { logger.Errorf(...); return err }`。
  2. 配套加 lint 规则禁止在 service 层裸调 `UpdateKnowledge`（可先用 `ast_grep` 规则或 `golangci-lint` 的 errcheck 覆盖 `s.repo.` 前缀）。
- **工作量**：M（1-3 天，21 处机械替换 + 1 个 helper + 1 条 lint 规则）

### [S2] `executeKnowledgeDelete` 跨 5 个数据源写入无事务，中途失败留下不一致状态

- **位置**：`internal/application/service/knowledge_delete.go:426-620`、`internal/application/service/knowledge_delete.go:626-704`、`internal/application/service/knowledge_delete.go:442,499,530,546,557,566,590,674,685,695`
- **证据**：整个 10.4 万行的 service 层只有 3 处 `.Transaction(`（`vectorstore.go:189`、`storagebackend.go:131`、`storagebackend.go:177`），全在 storagebackend/vectorstore，删除路径一处都没有。`executeKnowledgeDelete` 的写入序列是：
  ```go
  // knowledge_delete.go:442  1. 逐条标 deleting
  s.repo.UpdateKnowledgeForTransfer(ctx, &before, knowledge)
  ...
  // knowledge_delete.go:530  2. 删 chunks
  s.chunkRepo.DeleteByKnowledgeList(ctx, tenantInfo.ID, ids)
  // knowledge_delete.go:546  3. 删图
  s.graphEngine.DelGraph(ctx, namespaces)
  // knowledge_delete.go:566  4. 删 knowledge 行
  s.repo.DeleteKnowledgeList(ctx, tenantInfo.ID, ids)
  // knowledge_delete.go:590  5. 扣存储配额
  s.tenantRepo.AdjustStorageUsed(ctx, tenantInfo.ID, storageAdjust)
  ```
  第 4 步与第 5 步之间如果进程崩溃或第 5 步失败，knowledge 行已删除但 `tenant.storage_used` 永远不减——配额被永久虚高，用户再也无法上传文件。第 2 步成功、第 4 步失败则留下"knowledge 行还在、chunk 已没了"的行；此时 reparse 会得到空文档。`cleanupKnowledgeResources`（626-704）是同一模式的第二份拷贝，序列为 embedding 删除 → chunk 删除 → graph 删除 → 配额扣减，同样无事务。
- **影响**：
  1. **配额泄漏不可自愈**：`AdjustStorageUsed` 失败只记日志不重试（`knowledge_delete.go:591`）。用户会遇到"显示还有 1GB 空间但任何文件都传不进去"，且没有任何管理入口能修复——只能改库。这类问题的排查成本极高（用户报障 → 查 knowledge 表 → 手工 SQL）。
  2. **僵尸行**：chunk 已删、knowledge 行残留的状态会让 `reparse` 走空文档路径，或让删除重试命中 "file missing but row present" 分支。
  3. **重复实现**：`cleanupKnowledgeResources` 与 `executeKnowledgeDelete` 是两套几乎相同的清理序列，改一处必漏另一处。
- **修复**：
  1. 把 DB 部分（第 1/2/4/5 步 + tag 关系删除）收进一个 `s.db.WithContext(ctx).Transaction(...)`；向量库与对象存储删除保持在事务外（不能持锁做远程 IO），失败时走补偿队列而非回滚。
  2. `storageAdjust` 改成与 `DeleteKnowledgeList` 同事务写入，把"删行"和"扣配额"变成原子的。
  3. 抽出 `cleanupKnowledgeResourcesFor(ctx, tenantID, knowledge, kb)` 供两处共用，消除重复序列。
- **工作量**：M（1-3 天；需要把 `s.db` 注入 knowledgeService，di 走 dig 不内联构造）

### [S2] `ProcessWikiIngest` 770 行 / CC=108 / 嵌套 10 层，一个函数承担整条 ingest 流水线

- **位置**：`internal/application/service/wiki_ingest_batch.go:260-1029`
- **证据**：单函数内混合了 9 类职责：payload 解析与统计 defer（260-320）、panic 转发与 reservation 释放（318-325）、租户存活校验（335-343）、Lite/Redis 双模式锁分支（359-371）、KB 加载与模型可用性降级（372-425）、in-flight 配额闸门（437-450）、pending 行认领（452-467）、异常退出 claim 释放（492-511）、map/reduce 双阶段并行编排 + 尾随调度（516-1029）。嵌套深度达 10 层，圈复杂度 108（Go 社区公认 >15 即为高）。同文件 `mapOneDocument`(446 行, CC=45) 与 `reduceSlugUpdates`(417 行, CC=70) 同样超标。整个 `wiki_ingest_batch.go` 2302 行里挤了 9 个函数。
- **影响**：
  1. **改动必漏**：新增一种退出路径（例如再加一种 KB 不可用原因）需要同时理解锁、claim 释放、reservation 释放、follow-up 调度 4 套互相纠缠的清理逻辑。`wiki_ingest_batch.go:318-325` 的 defer 已经在处理"panic 时保留 reservation"这个特例，说明历史 bug 就出在这里。
  2. **不可测**：CC=108 的函数无法做有意义的单元测试。测试只能通过 asynq 端到端触发，实测只有 5 个测试文件引用了 `ProcessWikiIngest`。
  3. **注释即文档债**：函数里大段注释在解释"为什么用 claim 而不是 peek"、"为什么锁要 fail closed"，这些是拆分时最容易丢失的隐式契约——拆分等于重新做一次考古。
- **修复**：
  1. 按现有注释里的天然接缝拆成：`acquireBatch`（锁+配额+行认领，返回 plan struct）、`runMapPhase`、`runReducePhase`、`settleBatch`（trim + requeue + 尾随调度）。这四块的边界在代码里已经有清晰注释标记。
  2. 纯机械搬移，不改语义；每拆一块先补一个针对该块的测试。
  3. `mapOneDocument` / `reduceSlugUpdates` 同样处理，但优先级低于主函数。
- **工作量**：L（>3 天，含回归测试；建议先补测试再拆）

### [S2] `ProcessSync` 里 5 份内联失败收尾块绕过已有的 `updateSyncRunResult` helper

- **位置**：`internal/application/service/datasource_service.go:650-663`、`datasource_service.go:666-679`、`datasource_service.go:726-737`、`datasource_service.go:749-760`、`datasource_service.go:503-515`
- **证据**：`datasource_service.go:1171-1208` 已有正确实现 `updateSyncRunResult`，它调 `syncLogRepo.UpdateResult` 并写审计事件。但 5 个错误分支手写内联副本，用的是 `syncLogRepo.Update`：
  ```go
  // datasource_service.go:652-662
  syncLog.Status = types.SyncLogStatusFailed
  syncLog.FinishedAt = timePtr(time.Now().UTC())
  syncLog.ErrorMessage = fmt.Sprintf("Connector not found: %s", ds.Type)
  _ = s.syncLogRepo.Update(ctx, syncLog)        // 非 UpdateResult
  if !wasPaused { ds.Status = types.DataSourceStatusError }
  ds.ErrorMessage = syncLog.ErrorMessage
  _ = s.dsRepo.Update(ctx, ds)                    // 非 UpdateSyncState
  return err
  ```
  `repository/datasource_repo.go:267-269` 说明了二者的差异："UpdateResult updates only fields produced by sync execution. Use an explicit map so empty error messages are written when a later sync succeeds"——`Update` 走 GORM 结构体更新，零值字段（`items_total=0`、`error_message=""`）会被跳过，导致失败同步的计数残留上一轮的值。
- **影响**：
  1. **UI 显示错误数据**：用户看到"本次同步 0 个文档"但抽屉里 `items_created/updated/failed` 仍是上次成功同步的数字（因为 `Update` 不写零值）。排查"为什么同步了但没变化"时会得出错误结论。
  2. **审计缺失**：`updateSyncRunResult` 会发 `AuditActionDataSourceSyncFailed` 事件，5 个内联分支都不发。同步失败在审计日志里完全不可见，只有"同步开始/结束"有记录。
  3. **改一处必漏**：以后要给失败路径加字段（比如重试次数），改 `updateSyncRunResult` 不会有任何效果，因为 5 个分支根本不走它。
- **修复**：5 个内联块全部改为构造 `types.SyncResult{Total: 0}` 并调用 `s.updateSyncRunResult(ctx, ds, syncLog, result, types.JSON("{}"), types.SyncLogStatusFailed, msg, wasPaused)`，然后 `return err`。机械替换，行为即向 helper 对齐。
- **工作量**：S（半天，5 处替换 + 补一个断言 audit 事件的测试）

### [S2] `registerTools` / `wiki_boost` 按 SearchTarget 逐条查 KB，agent 每轮对话触发 N+1

- **位置**：`internal/application/service/agent_service.go:1057-1071`、`internal/application/service/chat_pipeline/wiki_boost.go:67-77`
- **证据**：
  ```go
  // agent_service.go:1057
  for _, target := range config.SearchTargets {
      if target == nil || target.KnowledgeBaseID == "" { continue }
      kb, err := s.knowledgeBaseService.GetKnowledgeBaseByIDOnly(ctx, target.KnowledgeBaseID)
      ...
  }
  ```
  同样的循环出现在 `wiki_boost.go:68-77`。而批量接口已经存在且未被使用：
  ```go
  // knowledgebase.go:336
  func (s *knowledgeBaseService) GetKnowledgeBasesByIDsOnly(ctx context.Context, ids []string) ([]*types.KnowledgeBase, error)
  ```
  调用链确认在热路径上：`agent_service.go:242` 的 `registerTools` 由 `CreateAgentEngine` 调用，而 `CreateAgentEngine` 是每轮对话构建引擎时执行一次（其上方注释："the engine is stateless across turns"）。`wiki_boost` 同样在每次检索管线中执行。
- **影响**：
  1. **每轮对话的固定延迟**：一个 Agent 配置 10 个 SearchTarget，就在每次发消息时多打 10 次 KB 查询（wiki_boost 再打一遍，取决于是否命中 wiki chunk）。多副本部署下这是持续的 DB 连接池压力。
  2. **`wiki_boost` 的循环无早退**：`wiki_boost.go:74` 的 `break` 只在匹配到 wiki KB 时触发；最坏情况（全部非 wiki KB）会把所有 target 查完才返回 `nil`，而它唯一的用途就是判断"要不要 boost"——绝大多数会话都白付这份代价。
  3. **改一次接口、多处返工**：KB 查询的租户过滤/权限逻辑若要调整，`registerTools` 和 `wiki_boost` 这两个循环要分别改。
- **修复**：
  1. 两处都改为：先收集去重后的 `kbIDs`，调 `GetKnowledgeBasesByIDsOnly` 一次，再建 `map[kbID]*KnowledgeBase` 供循环查。
  2. `wiki_boost` 额外加前置短路：先看 `chatManage.RerankResult` 里的 `SearchTargets` 数量，小于 1 直接返回。
- **工作量**：S（半天，2 处改写 + 1 个断言单次查询的 mock 测试）

### [S2] `getJwtSecret` 在 `rand.Read` 失败时 panic，且未配置 `JWT_SECRET` 时静默降级为每进程随机密钥

- **位置**：`internal/application/service/user.go:81-95`，调用点 `user.go:1091`、`user.go:1105`、`user.go:1252`、`user.go:1349`、`user.go:1406`、`internal/application/service/sandbox_terminal_ticket.go:52,66`
- **证据**：
  ```go
  // user.go:81-95
  func getJwtSecret() string {
      jwtSecretOnce.Do(func() {
          envSecret := strings.TrimSpace(os.Getenv("JWT_SECRET"))
          if envSecret != "" && envSecret != "weknora-jwt-secret" && envSecret != "CHANGE-ME-jwt-secret" {
              jwtSecret = envSecret
              return
          }
          randomBytes := make([]byte, 32)
          if _, err := rand.Read(randomBytes); err != nil {
              panic(fmt.Sprintf("failed to generate JWT secret: %v", err))  // 请求路径上 panic
          }
          jwtSecret = base64.StdEncoding.EncodeToString(randomBytes)
      })
      return jwtSecret
  }
  ```
  `sync.Once` 意味着首次调用若 panic，后续每次调用都会重新执行 `Do` 并再次 panic（Once 不标记为已执行）。该函数在 `ValidateToken`（`user.go:1252`，每个鉴权请求都走）和 sandbox ticket 签发路径上被调用。此外 `.env:556` 与 `.env.example:658` 中 `JWT_SECRET=` 为空值，`internal/runtime/startup.go:79` 只把它列为"打印 banner"项，不做启动校验。
- **影响**：
  1. **鉴权路径 panic**：`rand.Read` 在 Linux 上极少失败，但容器内 `/dev/urandom` 被 seccomp/gVisor 阻断、或 FIPS 模式下熵源不可用时会失败。此时进程**无 recover 保护**（`recover()` 在 service 层仅 6 处，均在 wiki ingest / asynq 路径），Gin 默认 recovery 之外，登录与鉴权请求会 panic 出去。
  2. **多副本静默互踢**（更常见）：`JWT_SECRET` 未配置时每个副本生成各自的随机密钥，副本 A 签发的 token 在副本 B 上验签失败。用户表现为"随机掉线"、"部分请求 401"，且重启后全部掉线。`CHANGELOG.md:61` 已记录此行为但仅作为文档说明，无运行时告警。
  3. **与 panic 叠加**：随机分支每次调用都重新进 `Do`，一个熵源故障的进程会持续在鉴权路径 panic，而非一次性失败。
- **修复**：
  1. `user.go:91` 改为返回 `(string, error)`，由调用方转成 500 响应，不在请求路径 panic；`jwtSecretOnce` 配合 `sync.OnceValues` 缓存 (value, err)。
  2. 在 `internal/runtime/startup.go` 的启动校验里把 `JWT_SECRET` 为空升级为**显式告警**（不是硬失败，因为 lite/desktop 有意不配），日志需点明"多副本必须一致"这一后果。
  3. 补测试：`JWT_SECRET` 未设置 + `rand.Read` 失败 → 返回 error 而非 panic。
- **工作量**：S（半天；需要同步改 7 个调用点的签名，编译器会兜住漏网）

### [S2] `touchAsync` 启动无跟踪的 goroutine，memory 写入在进程退出时静默丢失

- **位置**：`internal/application/service/memory/service.go:278-292`
- **证据**：
  ```go
  // memory/service.go:278
  func (s *Service) touchAsync(ctx context.Context, scope interfaces.MemoryScope, items []*types.MemoryItem) {
      if len(items) == 0 { return }
      ids := make([]string, 0, len(items))
      for _, item := range items { ids = append(ids, item.ID) }
      bgCtx := context.WithoutCancel(ctx)
      go func() {
          if err := s.repo.TouchUsed(bgCtx, scope, ids); err != nil {
              logger.Warnf(bgCtx, "memory: touch used failed: %v", err)
          }
      }()
  }
  ```
  `context.WithoutCancel` 正确地避免了请求取消导致的写丢失，但代价是这个 goroutine **没有任何生命周期归属**：无 WaitGroup、无超时（`bgCtx` 无 deadline）、无并发上限。它在每次 `Recall` 命中记忆时触发。同一模式在 service 层共 37 处裸 `go func`。
- **影响**：
  1. **goroutine 无界增长**：`context.WithoutCancel` 去掉了唯一的终止条件。一个慢 DB（如连接池打满时的 30s 等待）会让 goroutine 堆积；持续高频 Recall 会让 goroutine 数与 QPS 同步增长，无背压。
  2. **无超时**：`bgCtx` 没有 deadline，若 DB 层不返回（如网络分区后的 TCP 重传挂起），goroutine 永久泄漏。
  3. **写入静默丢失**：容器滚动更新 / SIGTERM 时，正在执行的 `TouchUsed` 直接被丢弃，记忆的使用时间戳不更新，LRU 淘汰会误删实际常用的记忆。
- **修复**：
  1. 复用已有的 `chat_pipeline.ParallelTask` / `RunParallel`（`common.go:216-231`，有 WaitGroup + 错误聚合）承载这类 fire-and-forget，或在 memory 包内加一个带上限的 worker pool。
  2. 给 `bgCtx` 加 `context.WithTimeout(bgCtx, 5*time.Second)`。
  3. Service 增补 `Close()` 等待在途 goroutine 退出，由 container 的 shutdown hook 调用。
- **工作量**：S（半天；需要给 memory.Service 加 Close 并接入 dig 的生命周期）

### [S3] 7 个 S3 系 file provider 各自实现同一组 7 个方法，安全/超时加固必须手工同步 7 次

- **位置**：`internal/application/service/file/s3.go`、`oss.go`、`obs.go`、`ks3.go`、`cos.go`、`tos.go`、`minio.go`
- **证据**：7 个文件各自实现同名方法 `SaveFile`/`GetFile`/`DeleteFile`/`SaveBytes`/`GetFileURL`/`CopyFile`/`CheckConnectivity`，共 2,466 行。关键在于加固不是自动继承的——`objectStorageHTTPClient()` 与 `objectStorageTransferContext` 的采用情况逐文件不同：
  | provider | `objectStorageHTTPClient()` | `objectStorageTransferContext` |
  |---|---|---|
  | s3 / oss / obs / ks3 / cos | 1 | 5 |
  | **tos** | **0** | **0** |
  | **minio** | **0** | **0** |
  这直接导致了本次审计的 S1 缺陷——`#3306` 那个 30s 整请求超时的修复被手工复制到了 5 个文件，漏掉的 2 个至今带 bug。
- **影响**：
  1. **回归必然发生**：任何一次对象存储加固（超时、SSRF、重试、限流、连接池）都需要人工在 7 个文件重复，且已经漏了 2 次。下一个加固会以同样方式漏。
  2. **行为不一致**：`tos.go:175-205` 的 `SaveFile` 与 `s3.go:227-257` 结构相同但超时处理不同；`tos.go` 的对象名走 `joinTOSObjectKey` 而 `s3.go` 走 `fmt.Sprintf` 拼接，命名规则散落 7 处。
  3. **审查成本**：改一个存储后端的正确性，要 review 7 个文件才敢确定没漏。
- **修复**：
  1. 抽 `objectStorageCore`（持有 client 抽象 + bucket + pathPrefix），实现公共的 `SaveFile`/`GetFile`/`DeleteFile`/`GetFileURL`/`CopyFile`；各 provider 只提供 `PutObject`/`GetObject`/签名/endpoint 这几个差异点。
  2. 先做第 1 条 S1 的超时对齐（不依赖重构），再评估重构。
- **工作量**：L（>3 天；7 个 provider × 7 个方法，且需要保证切换零回归）

### [S3] `knowledge_clone_move.go` 硬编码批量大小 50，批次大小在三个文件里各写各的

- **位置**：`internal/application/service/knowledge_clone_move.go:797`、`internal/application/service/retriever/keywords_vector_hybrid_indexer.go:105`、`internal/application/service/tag.go:453`
- **证据**：`knowledge_clone_move.go:797` 是 `batch := 50`（无命名常量、无注释说明为何是 50），同文件 `retriever/keywords_vector_hybrid_indexer.go:105` 是 `batchSize := 40`，`tag.go:453` 是 `const batchSize = 100`。三处都是 DB 批量写的分片大小，都调 `CreateChunks`/`UpdateChunks` 类的批量接口，但阈值互相独立且无交叉引用。
- **影响**：调大某个路径的批量会显著改变 DB 压力与内存占用，但其他两处不会跟着变。一次"把 FAQ clone 批量调大以提速"的改动会引入难以归因的性能回归，而三处数值之间没有任何文档说明为什么不同。FAQ 路径上的批量还同时驱动图片复制（`knowledge_clone_move.go:846-851` 的 `cloneChunkImageInfo`），批大小与对象存储往返次数直接耦合，调大时更容易撞上本条 S1 的 TOS 超时。
- **修复**：抽到 `internal/application/service/batchsize.go` 的具名常量，按用途区分（`chunkWriteBatchSize` / `vectorIndexBatchSize` / `tagWriteBatchSize`），并注释各自的取值依据。
- **工作量**：S（半天，3 处替换 + 注释）

### [S3] 3 个 200+ 行的核心函数无任何直接测试

- **位置**：`internal/application/service/wiki_lint.go:94`（RunLint, 250 行, CC=51）、`internal/application/service/knowledge_faq.go:907`（SearchFAQEntries, 332 行, CC=63）、`internal/application/service/knowledge_faq_import.go:1373`（executeFAQImport, 294 行, CC=52）、`internal/application/service/knowledge_clone_move.go:621`（cloneFAQKnowledgeBase, 302 行）
- **证据**：按函数名在 `internal/application/service/**/*_test.go` 中检索，`cloneFAQKnowledgeBase`、`RunLint`、`SearchFAQEntries`、`executeFAQImport` 均为 0 个测试文件引用。对比之下 `ProcessWikiIngest` 有 5 个、`processChunks` 有 4 个。
- **影响**：这 4 个函数合计 1,178 行、CC 均 >50，覆盖了 wiki 巡检、FAQ 检索、FAQ 导入、FAQ 克隆四条用户主流程。FAQ 导入/克隆涉及跨租户数据搬运（`cloneFAQKnowledgeBase` 会跨 tenant 复制 chunk 与 tag），CC=52 的 `SearchFAQEntries` 决定用户能否搜到答案。这些函数出问题时没有安全网，而它们的复杂度（50+ 决策点）恰恰是最容易出错的地方。
- **修复**：按"先测主干、后补边界"补表驱动测试。优先级：`SearchFAQEntries`（用户可见的检索正确性）> `executeFAQImport`/`cloneFAQKnowledgeBase`（跨租户数据正确性）> `RunLint`。
- **工作量**：M（1-3 天，仅覆盖主干路径；完整分支覆盖需翻倍）

### [S3] `ProcessKBClone` 用闭包 + `_ =` 吞掉进度写入失败，克隆失败状态可能永远不落库

- **位置**：`internal/application/service/knowledge_clone_move.go:442-620`（尤其 508-521 的 `handleError` 闭包、578-586 的进度回调）
- **证据**：
  ```go
  // knowledge_clone_move.go:520-527
  handleError := func(progress *types.KBCloneProgress, err error, message string) {
      if isLastRetry {
          progress.Status = types.KBCloneStatusFailed
          ...
          _ = s.saveKBCloneProgress(ctx, progress)   // 唯一记录失败的写入，错误被丢弃
          recordKBActivity(...)
      }
  }
  ```
  这个闭包被 `cloneFAQKnowledgeBase` 与 `executeKnowledgeClone` 的进度回调共调用 5+ 次，每次都 `_ =` 丢弃 `saveKBCloneProgress` 的错误。Redis 不可用时，用户在 UI 上看到的克隆状态会永远停在 "processing"——因为记录"失败"的那次写入，恰好是唯一会失败的那次。
- **影响**：前端轮询 `kb_clone_progress:{taskID}`（`knowledge_clone_move.go:432`）展示进度。Redis 抖动时用户看到进度条卡在 0%，任务实际已失败，日志里只有一行 `saveKBCloneProgress failed`（在 `saveKBCloneProgress` 内部），排查需跨 Redis/DB/asynq 三处对照。叠加本文件 19 处 `_ =`（该 slice 之最），这类静默失败难以系统性发现。
- **修复**：
  1. `handleError` 内改为 `if perr := s.saveKBCloneProgress(...); perr != nil { logger.Errorf(ctx, "clone progress persist failed (task=%s): %v", payload.TaskID, perr) }`，至少让错误带 taskID 进日志。
  2. 进度写入失败时同时写一条 `AuditActionKBCloneFailed`（该分支已有 audit 能力，只是排在 saveKBCloneProgress 之后，save 失败不影响它执行——确认一下顺序即可）。
  3. 进度回调里的 `_ = s.saveKBCloneProgress(ctx, progress)`（578-586）同样处理。
- **工作量**：S（半天，6-7 处替换 + 补错误日志断言）

## 量化

### 规模

| 指标 | 数值 |
|---|---|
| 生产代码文件数 | 227 |
| 生产代码行数 | 103,727 |
| 测试文件数 | 321 |
| 顶层函数总数 | 2,780 |
| 主包（`service/` 根） | 86,314 行 |
| `chat_pipeline/` | 6,763 行 |
| `memory/` | 4,798 行 |
| `file/` | 3,455 行 |
| `retriever/` | 1,496 行 |

### 坏味道计数（生产代码，已排除 `_test.go`）

| 坏味道 | 计数 | 备注 |
|---|---|---|
| `.Transaction(` 调用 | **3** | 10.4 万行中仅 3 处，全在 storagebackend/vectorstore |
| `panic(` | 7 | dataset.go×5（加载本地 parquet）、user.go×1（请求路径）、wiki_ingest_batch.go×1（主动 re-panic） |
| `recover()` | 6 | 全部集中在 wiki ingest / asynq worker 路径 |
| `_ = ` 形式忽略错误 | 115 | 集中在 knowledge_clone_move(19)、datasource_service(14)、temporary_document(11) |
| 裸 `return err`（丢失上下文） | 658 | clone_move(64)、knowledge_process(38)、datasource_service(29) |
| `context.Background()` / `TODO()` | 50 | 替代了传入 ctx 的场景 |
| 裸 `go func`（无 errgroup/WaitGroup 归属） | 37 | 其中 4 处 goroutine 内直接用 `context.Background()` |
| 圈复杂度 > 25 的函数 | 72 | |
| 嵌套深度 ≥ 5 的函数 | 200 | |
| 函数 ≥ 200 行 | 32 | |
| 函数 ≥ 100 行 | 125 | |
| 函数 ≥ 50 行 | 409 | |

### 最高复杂度函数（Top 5）

| CC | 行数 | 嵌套 | 位置 |
|---|---|---|---|
| 108 | 770 | 10 | `wiki_ingest_batch.go:260` `ProcessWikiIngest` |
| 94 | 459 | 4 | `agent_service.go:1021` `registerTools` |
| 90 | 496 | 6 | `knowledge_process.go:3490` `ProcessDocument` |
| 82 | 497 | 6 | `knowledge_process.go:342` `processChunks` |
| 77 | 163 | 4 | `file/factory.go:16` `NewFileServiceFromStorageConfig` |

### 错误处理与契约

- 未检查返回值的 `s.repo.UpdateKnowledge`：**23 处**（其中 21 处紧邻 `ParseStatus = "failed"` 赋值）
- `ParseStatus = "failed"` 赋值：21 处（knowledge_process.go 占 18）
- 已存在但被绕过的正确 helper：`updateSyncRunResult`（5 处内联副本绕过）
- 已存在但无人调用的批量接口：`GetKnowledgeBasesByIDsOnly`（`knowledgebase.go:336`）
- 用 `==` 而非 `errors.Is` 比较错误：6 处（`session_knowledge_qa.go:796,1023`、`chat_pipeline/search_parallel.go:125,145`、`embed_session.go:65`、`sandbox_desktop_last.go:51`）
- 人工补偿式"回滚"（无事务，改用逆序删除且丢弃错误）：2 处（`user.go:218-219`、`tenant.go:67`）

### 重复代码

- `file/` 下 7 个 S3 系 provider × 7 个同名方法 = 2,466 行；两两 Jaccard 行相似度 0.12–0.22，方法名 100% 重合，安全加固需人工同步（已因此漏 2 个，见 S1）
- `datasource_service.go` 同步失败收尾块：5 份内联副本
- `cleanupKnowledgeResources`（`knowledge_delete.go:626`）与 `executeKnowledgeDelete`（`:426`）：两套几乎相同的跨源清理序列
- 租户上下文裸解包 `ctx.Value(types.TenantInfoContextKey).(*types.Tenant)`：18 处，分布在 10 个文件（无统一 helper，`MustTenantIDFromContext` 有 73 处调用说明惯例已存在只是未统一到所有路径）
- `knowledgeWriteKB` helper 存在且被 9 处复用，是本 slice 中少数做对了的抽取

### 测试覆盖缺口

按函数名检索测试文件引用数：

| 函数 | 行数 | 引用测试文件数 |
|---|---|---|
| `ProcessWikiIngest` | 770 | 5 |
| `processChunks` | 497 | 4 |
| `registerTools` | 459 | 3 |
| `ProcessSummaryGeneration` | 387 | 3 |
| `ProcessDocument` | 496 | 2 |
| `ProcessWikiFinalize` | 249 | 2 |
| `ProcessSync` | 219 | 2 |
| `executeKnowledgeDelete` | 199 | 1 |
| `CloneChunk` | 193 | 1 |
| `QueryTemplates` | 225 | 1 |
| **`cloneFAQKnowledgeBase`** | **302** | **0** |
| **`SearchFAQEntries`** | **332** | **0** |
| **`executeFAQImport`** | **294** | **0** |
| **`RunLint`** | **250** | **0** |

### 建议处理顺序

| 优先级 | 债项 | 理由 | 工作量 |
|---|---|---|---|
| 1 | S1 tos/minio 超时与 transport | 确定性功能故障 + 资源耗尽，改动小 | S |
| 2 | S2 `updateSyncRunResult` 5 处内联块 | 机械替换，立刻修好审计缺失与 UI 错误数据 | S |
| 3 | S2 `registerTools` / `wiki_boost` N+1 | 机械替换，接口已存在 | S |
| 4 | S2 `getJwtSecret` panic + 随机降级 | 签名改动，编译器兜住漏网 | S |
| 5 | S2 23 处未检查 `UpdateKnowledge` | 消除静默失败，lint 可防回归 | M |
| 6 | S3 进度写入错误吞掉 | 同上，机械替换 | S |
| 7 | S2 `touchAsync` goroutine 泄漏 | 需加 Close 生命周期 | S |
| 8 | S2 补 4 个零测试函数的测试 | **应先于第 9 项** | M |
| 9 | S2 删除路径事务边界 | 需注入 db，跨表改动 | M |
| 10 | S2 拆分 `ProcessWikiIngest` | 大重构，需先有测试网 | L |
| 11 | S3 file provider 统一 | 7×7 改造，风险高 | L |
