# 技术债审计 — Go HTTP 接入层 (internal/handler, internal/router, internal/middleware, internal/types)

## 结论

- **鉴权主干是可靠的**：JWT/API-key/Embed 三通道统一走 `middleware.Auth`，API-key 走 fail-closed 的 `APIKeyRouteAuthorizer`（默认拒绝未声明路由），KB/Agent 子资源有 `KBAccessRead/Write` + `OwnedXxxOrAdmin` 双重守卫，session/message 在 service 层强制 user scope。**没有发现"改 URL 里的 ID 就能读写他人数据"的确定性 IDOR**——凡是我试图构造的越权路径，service/guard 都已经兜住。
- **修复 `/api/v1/auth/login` 无限流 + 无账号锁定**：这是本次唯一达到 S1 的问题。`noAuthAPI` 白名单放行了 login/register/auto-setup/refresh，但只有 `register-by-invite` 和 `invitations/lookup` 挂了 `PublicAuthRateLimit`。`userService.Login` 无失败计数、无锁定、且用 `"Account is disabled"` 区分了账号存在性。
- **修 CORS 的 `AllowCredentials: true` + `AllowOrigins: ["*"]` 组合**：gin-contrib/cors v1.7.7 在 `AllowAllOrigins` 时无条件发 `Access-Control-Allow-Credentials: true`（`utils.go:21-22` + `14-16`），同时发 `Allow-Origin: *`。虽然当前没有 cookie 认证，但这是"注释里写清楚了、代码里留着地雷"的形态——任何人引入 cookie 认证即变成全站 CSRF。
- **收敛 `err.Error()` 直接回显的 172 处**（22 处 `c.JSON(500, {"error": err.Error()})` + 150 处 `.WithDetails(err.Error())`）：`ErrorHandler` 把 `Details` 原样写进响应体，DB 错误/SQL/内部路径会直接给客户端。wiki_page.go 一个文件就有 20 处。
- **分页参数解析有 18 个各自为政的副本**，共用 helper 的只有 7 处。已确认其中 1 个是真 bug：`POST /messages/:session_id/load?limit=-5` 让 gorm 的 `Limit(-5)` 被 `clause.Limit.Build` 丢弃（`clause/limit.go:16` 的 `*limit.Limit >= 0` 守卫），LIMIT 子句消失，返回该 session 的**全部**消息。
- **`ListOrgShares` / `SearchSearchableOrganizations` 是 N+1 + 无上限**：`ListOrgShares` 每行 3 次查询（count knowledge / count chunks / get user），org 分享 50 条 = 150 次查询；`SearchSearchableOrganizations` 每行 3 次查询且 limit 只夹下界不夹上界。
- **CORS 白名单 `X-External-User-Token` 是个未使用但已开放的 header**，而 `Auth` 走的是 `X-API-Key`/`Authorization`——不过 CORS 只影响浏览器，攻击者用 curl 不受限于 CORS，所以这不是漏洞，但列入清理项。

## 发现

### [S1] `/api/v1/auth/login` 无任何暴力破解防护，且可枚举账号状态
- **位置**：`internal/middleware/auth.go:38-40`（noAuthAPI 白名单）、`internal/router/routes_auth_tenant.go:214`（`r.POST("/auth/login", handler.Login)` —— 未挂 `publicAuthRL`）、`internal/handler/auth.go:290-297`、`internal/application/service/user.go:229-265`
- **证据**：白名单放行 login：
  ```go
  "/api/v1/auth/login":      {"POST"},
  "/api/v1/auth/auto-setup": {"POST"},
  "/api/v1/auth/refresh":    {"POST"},
  ```
  但 `RegisterAuthRoutes` 只给两个分享链接端点挂了限流：
  ```go
  publicAuthRL := middleware.PublicAuthRateLimit()
  r.POST("/auth/register-by-invite", publicAuthRL, handler.RegisterByInvite)
  r.POST("/auth/invitations/lookup", publicAuthRL, handler.LookupInvitationByToken)
  r.POST("/auth/login", handler.Login)   // ← 无限流
  ```
  service 侧无失败计数，bcrypt 比对失败直接返回：
  ```go
  if !user.IsActive {
      return &types.LoginResponse{Success: false, Message: "Account is disabled"}, nil
  }
  err = bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password))
  if err != nil { return &types.LoginResponse{Success: false, Message: "Invalid email or password"}, nil }
  ```
- **影响**：(a) 攻击者可以对任意已知邮箱无限速撞库，bcrypt cost（通常 10）让单次 ~100ms，10 QPS 即 10 万次/小时；(b) `"Account is disabled"` 与 `"Invalid email or password"` 是两种不同响应，可直接枚举出**哪些邮箱已注册**、**哪些账号已注册但被禁用**；(c) `/api/v1/auth/register` 同样无限制，可被用于批量注册垃圾账号占满租户配额。
- **修复**：给 `RegisterAuthRoutes` 里的 `login` / `register` / `auto-setup` / `refresh` 挂 `publicAuthRL`（或为 login 单独做一个更严的、按 IP + 邮箱双维的限流器）；service 侧加 per-account 失败计数 + 指数退避锁定；同时把 `"Account is disabled"` 统一改成与密码错误相同的文案。
- **工作量**：S（半天；限流中间件已存在，只需挂载 + 改文案；锁定逻辑需落库，约 1 天）

### [S1] CORS 同时开启 `AllowOrigins: ["*"]` 与 `AllowCredentials: true`，为 cookie 化埋下全站 CSRF
- **位置**：`internal/router/router.go:137-150`
- **证据**：
  ```go
  r.Use(cors.New(cors.Config{
      AllowOrigins:     []string{"*"},
      ...
      AllowCredentials: true,
  }))
  ```
  gin-contrib/cors v1.7.7 的 `newCors`（`config.go:46-49`）看到 `AllowOrigins` 含 `"*"` 就设 `allowAllOrigins=true`，`generateNormalHeaders`（`utils.go:12-27`）随即**同时**设置 `Access-Control-Allow-Credentials: true` 和 `Access-Control-Allow-Origin: *`。代码上方的注释明确写了"若引入 cookie 认证，必须先把 AllowOrigins 换成受控清单"。
- **影响**：当前 `AllowCredentials` 因 `*` + credentials 被浏览器拒绝而无害。但这是一个静默的陷阱：任何人日后加 cookie session（哪怕只是给 embed 加一个 remember-me），所有 `/api/v1` 路由立刻对任意站点开放带凭据的跨域读写。审查时无法从测试发现，因为当前行为"看起来是对的"。
- **修复**：二选一。(a) 直接删掉 `AllowCredentials: true`（当前认证全走显式 header，确实用不到）；(b) 把 `AllowOrigins` 换成由 `cfg` 驱动的受控清单。
- **工作量**：S（半小时）

### [S2] `LoadMessages` 的 `limit` 传负数会让 LIMIT 子句消失，返回整个 session 的全部消息
- **位置**：`internal/handler/message.go:87`（`limit := secutils.SanitizeForLog(c.DefaultQuery("limit", "20"))`）、`:103-107`、`internal/application/repository/message.go:81-89`
- **证据**：handler 只判 `convErr != nil`，不判正负：
  ```go
  limitInt, convErr := strconv.Atoi(limit)
  if convErr != nil { limitInt = 20 }      // limit="-5" 时 convErr==nil，limitInt=-5 原样传下去
  ```
  repo 直接把 int 交给 gorm：
  ```go
  .Order("created_at DESC").Limit(limit).Find(&messages)
  ```
  gorm v1.31.1 的 `clause.Limit.Build`（`clause/limit.go:16`）是 `if limit.Limit != nil && *limit.Limit >= 0` —— 负数**不生成任何 LIMIT 关键字**，查询退化成"按 created_at 倒序取该 session 的每一行"。上界同样没设：传 `limit=100000000` 会真的发 `LIMIT 100000000`。
- **影响**：(a) 数据量放大——一个跑了几千轮的长 session，`?limit=-1` 一次拉全量消息（含每条 assistant 消息的 content、attachments、artifacts），内存与带宽尖峰；(b) 这条路由挂的是 `apiKeyChat` 能力，持有 chat key 的集成方可直接放大。
- **修复**：改用已存在的 `parseOffsetPagination(c)`（`internal/handler/list_pagination.go:48`），它对 `limit < 1 || limit > 100` 一律报 400；或至少在 handler 里加 `if limitInt < 1 { limitInt = 20 }` + 上界。
- **工作量**：S（半小时）

### [S2] 172 处把 `err.Error()` 原样回显给客户端（DB 错误 / SQL / 内部路径）
- **位置**：`internal/middleware/error_handler.go:26-33`、`internal/handler/wiki_page.go`（20 处 `c.JSON(500, gin.H{"error": err.Error()})`）、`internal/handler/tenant.go:37`、`internal/handler/auth.go:20`、`internal/handler/organization.go:16`、`internal/handler/knowledge.go:15`
- **证据**：ErrorHandler 把 `Details` 写进响应体：
  ```go
  c.JSON(appErr.HTTPCode, gin.H{"success": false, "error": gin.H{
      "code": appErr.Code, "message": appErr.Message, "details": appErr.Details,
  }})
  ```
  调用侧则是 `c.Error(errors.NewInternalServerError("Failed to list ...").WithDetails(err.Error()))`——150 处，分布在 tenant/auth/organization/knowledge/faq 等所有主要 handler。另有 22 处绕过 ErrorHandler 直接 `c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})`。
- **影响**：pdriver 的 gorm 错误形如 `Error 1146 (42S02): Table 'weknora.kb_shares' doesn't exist`、`dial tcp 10.0.3.17:5432: connect: connection refused`、绝对文件路径都会进响应体。这给攻击者提供了表结构探测、拓扑探测、路径探测的现成工具，也让用户看到本该只进日志的细节。日志侧已有 `sanitizeBody`/`SanitizeForLog`（`internal/middleware/logger.go:45-57`），响应侧完全没有对等处理。
- **修复**：加一个 `apperrors.InternalWithLog(ctx, msg, err)` 之类的 helper——`err` 只进 logger，`Message` 用固定文案；对 22 处裸 `c.JSON(500, err.Error())` 做同样替换。可以在 CI 加 grep 门禁防回流。
- **工作量**：M（1-3 天；172 处但模式统一，可脚本化 + 人工抽查）

### [S2] `ListOrgShares` 每行 3 次查询（N+1），组织分享列表线性放大
- **位置**：`internal/handler/organization.go:1316-1332`
- **证据**：
  ```go
  for _, s := range shares {
      ...
      if count, err := h.knowledgeRepo.CountKnowledgeByKnowledgeBaseID(ctx, s.SourceTenantID, s.KnowledgeBaseID); err == nil { resp.KnowledgeCount = count }
      if count, err := h.chunkRepo.CountChunksByKnowledgeBaseID(ctx, s.SourceTenantID, s.KnowledgeBaseID); err == nil { resp.ChunkCount = count }
      ...
      if user, err := h.userService.GetUserByID(ctx, s.SharedByUserID); err == nil && user != nil { resp.SharedByUsername = user.Username }
  }
  ```
  `ListOrgAgentShares`（`:1553`）有同样的 `GetUserByID` 逐行调用。
- **影响**：一个共享了 50 个 KB 的组织 = 151 次串行 DB 往返，按 2ms/次约 300ms；`ListSharesByOrganization` 本身不加分页，所以组织越大越慢，UI 的"共享管理"页会先卡住。且 `CountKnowledgeByKnowledgeBaseID` 失败被 `err == nil` 静默吞掉，前端会看到 KnowledgeCount=0 而无任何提示。
- **修复**：循环外一次性收集 `knowledge_base_id` 集合与 `shared_by_user_id` 集合，用 `WHERE id IN ?` 批量取回后在内存里 join。
- **工作量**：M（1 天）

### [S2] `SearchSearchableOrganizations` 的 limit 只夹下界不夹上界 + 3 次/行的 N+1
- **位置**：`internal/handler/organization.go:743-747`、`internal/application/service/organization.go:245-265`、`internal/application/repository/organization.go:87-103`
- **证据**：handler 和 service 都在检查 `limit <= 0` 就退回 20，但**没有任何上界**：
  ```go
  limit := 20
  if l := c.Query("limit"); l != "" {
      if n, err := strconv.Atoi(l); err == nil && n > 0 && n <= 100 { limit = n }   // 这层有 <=100
  }
  ```
  实际 handler 这层**有** `<= 100`，但 service 和 repo 没有第二道保险：
  ```go
  func (s *organizationService) SearchSearchableOrganizations(ctx, tenantID, query, limit) {
      if limit <= 0 { limit = 20 }     // 只有下界
      orgs, err := s.orgRepo.ListSearchable(ctx, query, limit)
      for _, org := range orgs {
          s.orgRepo.CountTenantMembers(ctx, org.ID)          // N+1
          s.shareRepo.ListByOrganization(ctx, org.ID)        // N+1
          s.agentShareRepo.ListByOrganization(ctx, org.ID)   // N+1
      }
  ```
  三层各写一遍同样的"默认 20"，改一处漏两处的经典形态。
- **影响**：handler 层的 100 上界目前挡住了这条路，属于"单点防御"——一旦新增调用方（agent 工具、CLI）直接调 service，上界就没了。N+1 是确定存在的：`limit=100` → 301 次查询。
- **修复**：把上界下沉到 service（和 `SearchPagesAcross` 在 `wiki_page.go:1045` 的 `maxWikiSearchKnowledgeBases` 做法保持一致），handler 的检查改为纯校验；三层的默认值合并到 service 一处。
- **工作量**：M（1 天）

### [S2] Ollama 模型下载任务的全局 map 无上限、无过期清理、跨租户可见
- **位置**：`internal/handler/initialization.go:52`（`downloadTasks = make(map[string]*DownloadTask)`）、`:1221-1251`（插入）、`:1286-1341`（Get/List）、`:1453-1470`（updateTaskStatus）
- **证据**：任务存在一个包级 map，插入后只有 `updateTaskStatus` 改字段，**没有任何 delete**：
  ```go
  tasksMutex.Lock()
  downloadTasks[taskID] = task
  tasksMutex.Unlock()
  ```
  `updateTaskStatus` 在 completed/failed 时只写 `task.EndTime`，行本身留在 map 里。查询侧不做租户过滤：
  ```go
  func (h *InitializationHandler) ListDownloadTasks(c *gin.Context) {
      for _, task := range downloadTasks { tasks = append(tasks, task) }   // 无 tenant 过滤
  ```
  路由守卫是 `g.Viewer()`（`routes_infra.go` 中 `/initialization/ollama/download/tasks`），即同空间任意 Viewer 可读。
- **影响**：(a) **内存泄漏**——每次 `DownloadOllamaModel` 永久新增一条记录，只有进程重启才释放。12 小时超时的下载（`:1253` `context.WithTimeout(..., 12*time.Hour)`）失败后同样留下条目；(b) 任务的 `Message` 字段直接来自 Ollama 的进度回调（`pullModelWithProgress` 把 `progress.Status` 原样塞进去），可能含内部 registry 地址——同租户 Viewer 都能看到；(c) 进程重启后任务状态全丢，前端永远等不到终态。
- **修复**：(1) 在 `updateTaskStatus` 的 completed/failed 分支加 `delete(downloadTasks, taskID)`，或加一个带 TTL 的清理协程；(2) `DownloadTask` 加 `TenantID` 字段并在 `List`/`Get` 里过滤；(3) `Message` 走 `SanitizeForLog`。
- **工作量**：M（1 天）

### [S3] 18 处分页参数各自为政，与 helper 的语义各不相同
- **位置**：`internal/handler/list_pagination.go:22-70`（helper）、`internal/handler/system.go:1491-1502`、`internal/handler/audit_log.go:172-183` 与 `:216-226`、`internal/handler/wiki_page.go:757-761` 与 `:838-848`、`internal/handler/embed_channel.go:375-381`、`internal/handler/custom_agent.go:701-705`、`internal/handler/organization.go:743-747`、`internal/handler/stock_watch.go:214`、`internal/handler/datasource.go:542-555`
- **证据**：同一个 `limit` 参数有至少 5 种不同策略：
  | 位置 | 非法值行为 | 上界 |
  |---|---|---|
  | `list_pagination.go` | 400 | 100 |
  | `system.go:1491` | 静默忽略 | 200 |
  | `audit_log.go:172` | 静默忽略 | 0（不夹） |
  | `wiki_page.go:757` | 静默忽略 | 0（不夹） |
  | `datasource.go:542` | 400 | 100 |
  
  `audit_log.go:172-183` 和 `:216-226` 是**同一个函数 `parseAuditCursor` 被复制了两遍**（`ListTenantAuditLog` 内联一份、`ListSystemAuditLog` 又内联一份）。
- **影响**：新端点照抄哪一份全凭作者习惯；本次审计里我发现的 `LoadMessages` 负数 bug 就是"第 19 种写法"（handler.go 根本不用 helper）。上界为 0 的两处（audit / wiki index）目前靠 service/repo 层兜底，属于隐式契约。
- **修复**：统一到 `parseListPagination` / `parseOffsetPagination`；把 `parseAuditCursor` 去重成一份；对确实需要"非法值静默降级"的端点，写成一个 `parseTolerantLimit(c, default, max)` 明确表达，不要靠内联复制。
- **工作量**：M（1-3 天）

### [S3] 响应契约三套并存，前端拦截器需要分别适配
- **位置**：`internal/middleware/error_handler.go:26-40`（envelope 型）、`internal/handler/system.go:379-382`（`{"code":0,"msg":"success","data":...}`）、`internal/handler/im.go:73` 等（`{"error": "..."}` 裸型）
- **证据**：同一进程内三种成功/失败信封并存：
  ```go
  // ErrorHandler
  c.JSON(appErr.HTTPCode, gin.H{"success": false, "error": gin.H{"code":…, "message":…, "details":…}})
  // SystemHandler.GetSystemInfo
  c.JSON(200, gin.H{"code": 0, "msg": "success", "data": response})
  // IMHandler / 大量 handler
  c.JSON(http.StatusBadRequest, gin.H{"error": "..."})
  ```
  `routes_auth_tenant.go:296-299` 的注释自己承认了这件事："Reads return raw model rows / arrays (no `gin.H{"data":...}` wrapping), matching the project's axios interceptor convention — see frontend/src/utils/request.ts:97"。
- **影响**：改任何一处状态码或字段名都要先查前端拦截器；新端点作者会继续自由发挥，契约继续漂移。文档生成（swag）也因此有三套 `@Success` 注解风格。
- **修复**：定一份 DTO 契约文档 + 写一个 `respond(c, status, data, err)` 单一出口，逐步迁移；不要求一次性改完，先把新端点强制走新出口。
- **工作量**：L（>3 天）

### [S3] `c.Get(tenantID).(uint64)` 无保护类型断言散布 12 处，任一未设 key 即 panic
- **位置**：`internal/handler/knowledgebase.go:887`、`:1301`、`internal/handler/knowledge.go:2823`、`:2842`、`:2860`、`:2896`、`:2913`、`internal/handler/session/handler.go:204`、`:373`、`internal/handler/session/stream.go:249`
- **证据**：
  ```go
  // knowledgebase.go:887
  tenantID, _ := c.Get(types.TenantIDContextKey.String())   // 忽略了 ok
  if kb.TenantID != tenantID.(uint64) { ... }                 // 未设则 panic
  ```
  而 `Auth` 在"tenantless 会话 + 非 tenant-optional 路由"下会 409 提前 abort（`auth.go:225-232`），理论上到不了 handler。真正的风险是**新增的 pre-Auth 路由**（如 `/files/presigned-preview` 走 `RequireRole` + `DenyAPIKeyPrincipal`，不经过 `applyAuthSession` 的 tenant 设置路径）——目前它没用断言所以安全，但同文件里 `organization.go:1067` 的 `c.GetUint64(...)` 才是正解。
- **影响**：一旦有路由绕过 `Auth`（这是这个 codebase 明确的设计——`files.go` 的 presigned、`/r/:token`、`/mcp/:endpoint_id`、两个 WS 路由都是 pre-Auth 注册的），panic 会由 `Recovery` 兜住变成 500，但响应体里带 `message: fmt.Sprintf("%v", err)`（`recovery.go:32-34`）= `"interface conversion: interface {} is nil, not uint64"` —— 又是信息泄漏 + 排查困难。
- **修复**：统一改用 `c.GetUint64(types.TenantIDContextKey.String())`（gorm 内部自带 ok 检查，返回 0），并在这 12 处后面加 `if tenantID == 0 { 401 }`。
- **工作量**：S（半天）

## 量化

| 指标 | 数值 |
|---|---|
| 本 slice 生产文件数（非测试） | 295 |
| 本 slice 生产代码行 | 84,687 |
| 本 slice 测试文件数 | 252 |
| 本 slice 测试代码行 | 42,741 |
| 测试/生产行比 | 0.50 |
| 注册的路由总数（router 包内 `.GET/.POST/...`） | 429 |
| `noAuthAPI` 免鉴权路由条目 | 15 条路径（~22 个 method） |
| 挂了限流的免鉴权路由 | 2（`register-by-invite`、`invitations/lookup`） |
| `c.JSON(500, {"error": err.Error()})` 直出 | 22 处 |
| `.WithDetails(err.Error())` | 150 处 |
| 各自实现的分页解析点 | 18 |
| 复用 `parseListPagination`/`parseOffsetPagination` 的点 | 7 |
| 无保护 `.(uint64)` 类型断言 | 12 处（另 6 处是带 ok 的安全写法） |
| 裸 SQL 拼接（`Where(fmt.Sprintf(` 之类） | 0（排序字段全部走白名单：`wiki_page.go:53-72`、`knowledge.go:1051`） |
| gorm `.Raw(` / `.Exec(` | 0（handler/middleware/router 三处均为其他含义） |
| 路径穿越防护点 | 4 处（`files.go:89` `requireFilePathQuery`、`files.go:555` presigned、其余经 `ValidateStoragePathTenant`） |
| 命中坏味道：N+1 查询 | 2 处 handler（`organization.go:1319/1323/1328`、`organization.go:1553`）+ 1 处 service（`organization.go:258/261/263`） |
| 命中坏味道：无界 map 增长 | 1 处（`initialization.go:52`） |
| 命中坏味道：契约不一致 | 3 套响应信封并存 |

**评估**：整体鉴权质量高于平均水位——`middleware/access.go` 把租户可达性判断收敛到一处，`kb_access.go` 把 KB 三路访问（自有/组织共享/共享 agent）收敛到一处，API-key gate 做 fail-closed 并在启动时 `assertAPIKeyPoliciesMatchRoutes` 自检路径漂移（`rbac.go`），`RequireOwnershipOrRole` 明确区分 NotFound(放行给 handler 出 404) / Forbidden(403) / LookupFailed(503) 三态。真正的债集中在**输入边界的一致性**（分页/错误回显/类型断言）和**两处有实际后果的资源管理**（Ollama map、organization N+1），而不是鉴权模型本身。
