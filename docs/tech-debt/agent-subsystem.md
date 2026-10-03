# 技术债审计 — Agent 执行子系统与外部集成

范围：`internal/agent/**`（含 `tools/`、`skills/`、`approval/`、`compaction/`、`token/`）、`internal/mcp/**`、`internal/sandbox/**`、`internal/im/**`、`internal/stream/**`

## 结论

- **封住一个进程级崩溃口子**：IM 的 `qaQueue` worker 与 handler 的 `go func()` 都不在 gin's `Recovery()` 保护范围内，QA 链路里任何一个 panic 都会打挂整个进程（`internal/im/qaqueue.go:260`、`internal/handler/im.go:485`）。
- **沙箱与 MCP 的外圈已经修得很扎实**：SSRF（`ValidateURLForSSRF` + 拨号层 `SSRFSafeDialContext` + 固定 IP 拨号 + 同源重定向）、租户键（`SessionSandboxKey{TenantID, SessionID}`）、OAuth 跨实例租约刷新、Docker 容器内 `timeout -s KILL` 包装、shell 黑名单 + head/tail 截断 —— 这些都经过实读验证，本次不构成债。
- **`allowed_tools` 白名单是真实闸门**：`registerTools` 的 switch 只注册白名单命中项，未命中落 `default:` 只打一条 warn；MCP 侧另有 `MCPCatalog.authorize`（tenant+principal+oauthPrincipal 三重比对）与调用期二次 `gate.IsEnabled`。没有发现"注册了但未鉴权"的工具。
- **金融工具里 `financial_indicator_detail.go` 是唯一漏网的**：`ORDER BY report DESC` 绕过了 `financial/period.go` 的唯一排序来源，且该文件零测试、描述文案未说明 `FY→-4` 映射 —— 正是 AGENTS.md 记为"已付出真实调试代价"的陷阱。
- **`ValidateReadOnlySQL` 是子串黑名单而非词法分析**：`SELECT 1 FROM t WHERE name='DROP'` 这类含关键字字面量的合法只读查询会被误拒；同时 `SET ...` / `PRAGMA` / `IMPORT` 未在名单内（Python 侧才是真正执行点，需确认）。
- **`stream_manager` 的 memory 后端无任何淘汰逻辑**：`config.StreamManager.CleanupTimeout` 只在 `internal/config/config.go:794` 被校验，从未被 memory manager 读取；`DropMessageStreams` 只在 rewind 路径调用。Lite/桌面模式（`STREAM_MANAGER_TYPE=memory`）下事件缓冲只增不减。
- **死代码两处**：`internal/agent/tools/finanserv/` 与 `internal/agent/tools/zettarancserv/` 已被 `agent_service.go` 的内联 switch 取代，两个 `register.go` 无任何 importer 且仍在 git 版本控制内。

## 发现

### [S1] IM QA worker goroutine 完全没有 panic 边界，一次 panic 打死整个进程

- **位置**：`internal/im/qaqueue.go:260`、`internal/im/qaqueue.go:122`、`internal/handler/im.go:485`
- **证据**：`runWorker` 是裸 goroutine，其调用的 `q.handler(req)`（即 `s.executeQARequest`，见 `internal/im/service.go:898` 构造）**没有 `recover()`**：

  ```go
  // qaqueue.go:219-265
  func (q *qaQueue) runWorker(id int) {
      for {
          req := q.dequeue()
          ...
          q.activeWorkers.Add(1)
          q.handler(req)          // ← 260: 裸调用
          q.activeWorkers.Add(-1)
  ```

  `internal/im/qaqueue.go:122` 是 `go q.runWorker(i)`，`q.handler` 会走进 `executeQARequest` → `handleMessageStream` / `runQA` → 完整的 agent + 检索 + 文档解析 + 沙箱链路。

  唯一的 HTTP 侧保护是 `internal/middleware/recovery.go` 的 gin `Recovery()`，但 `internal/handler/im.go:485` 显式脱离了请求栈：

  ```go
  asyncCtx := context.WithoutCancel(ctx)
  go func() {
      if err := h.imService.HandleMessage(asyncCtx, msg, channelID); err != nil { ... }
  }()
  ```

  Go 运行时在未 recover 的 goroutine panic 时终止整个进程，`Recovery()` 中间件救不到。

- **影响**：`executeQARequest` 触及的第三方/自研代码里任何一个 nil 解引用或越界（docreader 解析结果、blob 存储 SDK、sandbox SDK、MCP SDK、`types.Scan` 后的类型断言）都会让 WeKnora **全进程退出**，且不只是 IM 通道 —— 所有在线用户、正在跑的 RAG 任务、正在跑的 agent turn 一起挂掉。复现条件是"某条 IM 消息触发了那条会 panic 的代码路径"，不是需要特殊输入。`internal/agent/tools/registry.go:288` 的 `executeRecovered` 已经在工具层做了同样的防护并写明理由（"errgroup does not carry a panic back to Wait"），说明团队清楚这个模式，只是没覆盖到 IM worker。
- **修复**：在 `runWorker` 里给 `q.handler(req)` 包一层与 `executeRecovered` 同构的 recover（`defer func(){ if r:=recover(); r!=nil { logger.Errorf(...); req.adapter.SendReply(...错误提示) } }()`）。同时给 `internal/handler/im.go:485` 的 goroutine 加同样的兜底。**最小可行改法**只动 `internal/im/qaqueue.go` 一处。
- **工作量**：S（<半天）

### [S2] `stream_manager` memory 后端无淘汰，`cleanup_timeout` 是死配置

- **位置**：`internal/stream/memory_manager.go:42-62`、`internal/config/config.go:794`
- **证据**：`MemoryStreamManager` 用 `streams map[sessionID]map[messageID]*memoryStreamData` 全量持有事件，只在 `DropMessageStreams`（`memory_manager.go:304`）里删，而该方法**唯一的调用方**是 `internal/application/service/session_rewind.go:616`（仅 rewind 时触发）。`lastUpdated` 字段被写了 5 处（`memory_manager.go:15,56,92,154,210,238`）却**从未被读取**——没有基于它的过期清理。

  与此同时 `internal/config/config.go:794` 对 `cfg.StreamManager.CleanupTimeout < 0` 做了校验，`internal/config/config.go:473` 定义了 `CleanupTimeout time.Duration`：

  ```go
  CleanupTimeout time.Duration `yaml:"cleanup_timeout" json:"cleanup_timeout"` // 清理超时，单位秒
  ```

  全仓 `grep -rn "CleanupTimeout" --include=*.go internal/ cmd/` 只命中 `config.go` 的定义与校验两行，**没有任何消费者**。`cli/AGENTS.md:477` 也确认了这个事实："`STREAM_MANAGER_TYPE=memory`（default）| **Process lifetime**（server restart = data loss; no explicit cleanup logic）"。

  选中该分支的部署：`.env.lite:28` 是 `STREAM_MANAGER_TYPE=memory`，`cmd/desktop/main.go:220-221` 在非 redis 模式下强制置为 memory。

- **影响**：Lite/桌面模式下，一次会话的每个 assistant 消息都会在 `streams` 里留下一条 `[]StreamEvent`（每个 token chunk 一条 `interfaces.StreamEvent`，见 `memory_manager.go:80` 起的 `AppendEvent`），且**永不回收**。长时间运行的桌面实例会持续吃内存；同时 map key 数量无上界，`m.mu.Lock()` 下的 `getOrCreateStream`（`memory_manager.go:44`）会随 map 增长变慢，拉高每轮 agent 的写入延迟。运维侧改 `cleanup_timeout` 没有任何效果，是个会误导人的配置项。
- **修复**：给 `MemoryStreamManager` 加一个基于已有 `lastUpdated` 字段的周期清理（参照 `internal/im/service.go:952` 的 `dedupCleanupLoop` 写法，`ticker` + `m.mu.Lock()` 扫描删除），或让 `NewStreamManager` 把 `CleanupTimeout` 传进来驱动它；两者至少做前者，否则 `lastUpdated` 应删掉。
- **工作量**：S（<半天）

### [S2] `financial_indicator_detail.go` 绕过统一排序来源且零测试

- **位置**：`internal/agent/tools/hithink_finance/financial/financial_indicator_detail.go:109`、`internal/agent/tools/hithink_finance/financial/period.go:21`
- **证据**：AGENTS.md 与 `period.go:6-19` 的注释都把"排序必须来自 `periodOrderBy`"列为硬约束，`period_test.go:26-49` 用 `TestStatementQueriesOrderByPeriodEnd` 锁住了三张表。但第四个财务工具自己内联了一条：

  ```go
  // financial_indicator_detail.go:99-110
  FROM v_financial_indicators_detail
  WHERE thscode = ?
  ORDER BY report DESC
  LIMIT ?
  ```

  该文件**没有任何 `_test.go`**：`grep -rn "financial_indicator_detail|indicator.detail" --include=*_test.go internal/` 返回空。`period_test.go` 的 `TestStatementQueriesOrderByPeriodEnd` 只枚举 `balanceSheetQuery / incomeStatementQuery / cashFlowQuery` 三个常量，`schema_contract_test.go` 只校验表名/列名是否存在于 `testdata/schema.json`（不校验排序语义），所以这条 SQL 完全在测试闸门之外。

  同一文件的 `Description()`（`financial_indicator_detail.go:47`）写的是"注意：报告期字段是 report（不是 period）。数据区间 2024-1 至 2026-2"——只说了列名不同，**没有说明 `report` 是 `YYYY-N` 格式且 `FY` 必须映射为 `-4`**。AGENTS.md 明确把这条列为"必须映射否则 join 静默返回空"的陷阱，模型拿不到这个信息。

- **影响**：`periods=N` 在这张表上返回的是**字典序前 N** 个报告期。`report` 是 `YYYY-N` 字符串，`"2026-2" > "2025-4" > "2025-3" > ... > "2024-1"`，跨年时数字部分能比对正确，但同一年内 `2025-10` 这种两位数期别（若未来出现）会排在 `2025-4` 之后错位；更确定的问题是模型按描述把 `report` 当作可读期别，再按 `2025-4 < 2025-10` 推理时序。零测试意味着下次改列名/改视图也不会有人发现。
- **修复**：① 把这条查询的 `ORDER BY` 提取到 `financial/period.go`（或新增一个 `indicatorReportOrderBy` 常量）并加进 `period_test.go` 的 `TestStatementQueriesOrderByPeriodEnd` 枚举里；② 在 `Description()` 里补一句 `report` 是 `YYYY-N`、`FY` 对应 `-4`；③ 补一个 `_test.go` 至少锁住排序与描述。
- **工作量**：S（<半天）

### [S2] `ValidateReadOnlySQL` 用子串黑名单，既可误拒也可绕过

- **位置**：`internal/agent/tools/hithink_finance/common.go:279-297`
- **证据**：

  ```go
  func ValidateReadOnlySQL(sqlStr string) error {
      upper := strings.ToUpper(strings.TrimSpace(sqlStr))
      if !strings.HasPrefix(upper, "SELECT") { ... }
      dangerous := []string{"INSERT","UPDATE","DELETE","DROP","CREATE","ALTER","COPY","ATTACH","DETACH","CALL","EXECUTE"}
      for _, keyword := range dangerous {
          if strings.Contains(upper, keyword) { return fmt.Errorf(...) }
      }
      return nil
  }
  ```

  三个具体问题：
  1. **误拒**：`SELECT thscode, name FROM v_index_universe WHERE name = '半导体-CREATE'`、`... WHERE tag='DELETE'` 这类含关键字字面量的合法只读查询被拒。`sql_query.go:42` 的工具描述里就有 `WHERE u.tag='industry' AND u.name='半导体'` 这类模式，用户数据里出现这些词是迟早的事。
  2. **漏项**：`SET` / `PRAGMA` / `IMPORT` / `LOAD` / `INSTALL` / `SUMMARIZE` 不在 `dangerous` 里；`PRAGMA` 在 DuckDB 上能改会话设置（如 `SET memory_limit`），`INSTALL httpfs` 会尝试拉扩展。
  3. **注释绕过**：`strings.Contains` 不去注释，`SELECT 1 -- DROP` 无关（这是误拒方向），但 `/*x*/SELECT` 会被 `HasPrefix(upper,"SELECT")` 直接拒 —— 反过来，用 `WITH cte AS (DELETE ... RETURNING *) SELECT * FROM cte` 虽被 DELETE 拦下，但 `WITH cte AS (SELECT ...) SELECT` 不含任何危险词就放行，这是设计意图内的。

  真正的执行点在 `python-service`（`common.go:93` POST 到 `config.ServiceURL+"/query/"`），Go 侧只是前置过滤——注释里也承认了这点（`common.go:75-78` 提到 python-service 会用外层 `LIMIT` 包裹）。

- **影响**：误拒方向是真实的用户可见故障（工具返回"安全限制：禁止 DROP 操作"，模型和用户都看不懂）；漏项方向需要确认 python-service 侧是否有第二道闸门，如果有则只是纵深缺失，如果没有则是 DuckDB 会话被模型改写。**这一条我未读 python-service 的 `/query/` 实现，不确定执行点是否还有校验** —— 修 Go 侧之前应先确认。
- **修复**：改成剥离注释与字符串字面量后再匹配；危险词用词边界（`\b(INSERT|...)\b`）而非子串；补上 `SET|PRAGMA|IMPORT|LOAD|INSTALL`。同时确认 python-service 侧有独立校验，两层独立。
- **工作量**：S（<半天，改 Go 侧）

### [S2] IM 队列的 Redis 全局 per-user 计数器在异常路径上会残留

- **位置**：`internal/im/qaqueue.go:139-145`、`internal/im/qaqueue.go:364-374`
- **证据**：`Enqueue` 先 `redisCheckAndIncrUser`（对 `RedisKeyQueueUser+userKey` 做 `INCR` 并设 5 分钟 TTL），成功后才拿本地锁。`redisDecrUser` 的调用点覆盖了 closed / queue full / Remove / worker 跳过 / worker 超时 / worker 执行完毕六条路径（`qaqueue.go:150,155,196,229,236,253,264`），逻辑上配对完整。

  真正的问题在 `redisCheckAndIncrUser` 自身（`qaqueue.go:364-374`）：

  ```go
  count, err := q.redis.Incr(ctx, key).Result()
  if err != nil {
      // Redis error — skip global check, rely on local limit.
      return nil          // ← 返回 nil = "检查通过"，但 INCR 可能已在服务端成功
  }
  q.redis.Expire(ctx, key, redisQueueUserTTL)
  ```

  `INCR` 与 `EXPIRE` 是两条独立命令。若 `INCR` 成功而 `Expire` 失败（网络抖动、连接在两条命令之间断开），返回的错误被完全忽略（没有检查 `Expire` 的返回值），key 变成**永不过期**的计数器。此后该 userKey 的 `INCR` 会单调增长直到超过 `maxPerUser`，而 `redisDecrUser` 只能减不能删 —— 用户被永久限流到 `maxPerUser`（默认 3）条并发/排队，5 分钟 TTL 也救不回来。

  同样的模式出现在 `Enqueue` 开头：`context.Background()`（`qaqueue.go:140`）意味着 Redis 挂起时这里没有超时保护。

- **影响**：低概率但不可自愈。用户遇到的现象是"发消息永远回'当前排队人数较多，请稍后再试'"，重启服务才好 —— 因为计数在 Redis 里。排查成本很高：日志里 `INCR` 成功、`DECR` 也都成功，只有 `EXPIRE` 那一条无声失败。
- **修复**：`q.redis.Expire(...)` 检查返回值，失败时 `Del(key)` 或至少打一条 Warn；或用 `INCR` + `EXPIRE` 合并为一个 Lua 脚本（与 `globalGateScript` 同款），保证原子性。同时给 `Enqueue` 里的 Redis 调用换成带 2s 超时的 ctx。
- **工作量**：S（<半天）

### [S3] `finanserv` / `zettarancserv` 两个注册包是死代码且仍在版本控制内

- **位置**：`internal/agent/tools/finanserv/register.go`、`internal/agent/tools/zettarancserv/register.go`
- **证据**：`git ls-files` 两个文件都在版本控制内。`grep -rn "tools/finanserv|tools/zettarancserv" --include=*.go .`（排除 node_modules）**返回空** —— 零 importer。

  两者都已被 `internal/application/service/agent_service.go` 的内联 switch 取代：`zettaranc` 走 `agent_service.go:1394` 的 `case "zettaranc.analyze", "zettaranc.screener", "zettaranc.four_bricks"`，`hithink` 走 `agent_service.go:1326-1417` 的 22 个 `case`。`agent_service.go:1400-1418` 有一段长注释解释 `zettaranc.backtest` 为什么被**故意**从 switch 里去掉，而 `zettarancserv/register.go:29-37` 是同一段决策的**旧副本** —— 两处会漂移，且注释里提到的 `TestZettarancWhitelistMatchesRegisteredTools` 保护的是 agent_service 的那份。

- **影响**：读者按包名找 zettaranc 注册会先找到这个已死的 `register.go`，读到一份与真实实现不一致的工具清单（含已删除的 stub 策略）。同时 `finanserv` 里 `hithink_finance.NewDiscoverTool(registry)` 的签名与 `agent_service.go:1332` 用的 `NewDiscoverToolWithConfig(registry, s.hithinkConfig)` 不同 —— 两份 API 已经分叉。
- **修复**：删除两个目录（`git rm`）。
- **工作量**：S（<半天）

### [S3] shell_exec 的 `env` 参数在非 skill 路径上无保留名过滤

- **位置**：`internal/agent/tools/shell_exec.go:532`、`internal/agent/skills/env_resolver.go:57-66`
- **证据**：`ApplyResolvedEnv` 的注释（`env_resolver.go:57-61`）说得很清楚："This is the second layer of reserved-name protection. ... letting it land on `WEKNORA_SKILL_OUTPUT_DIR` would silently redirect the turn's artifacts to a directory nobody drains." 但第一层（`InjectedSandboxEnvVars()`，见 `internal/agent/skills/manager.go:48-57`）只作用于**技能声明的写库路径**。

  在 `shell_exec.go:532`，`env := input.Env` 直接来自模型参数。带 `skill_name` 时 `PrepareShellEnvironment`（`internal/agent/skills/shell_environment.go:33-45`）会在复制完 `env` 之后**覆盖** `skillDirEnvVar` / `artifactOutputEnvVar` / `artifactHistoryEnvVar` / `sessionInputEnvVar`，所以那条路径是安全的。但 `skill_name` 为空时（`shell_exec.go:574-579` 的 `if input.SkillName != "" && t.skillEnvironment != nil` 不成立），`input.Env` 原样进 `ExecShellCommand`。

  实际影响经过核实后**很有限**：`ArtifactOutputDir()`（`manager.go:85-92`）读的是**宿主机** `os.Getenv` 而非沙箱 env，`agent_stream_handler.go:789` 的产物收集也走宿主机侧，所以模型在 `env` 里塞 `WEKNORA_SKILL_OUTPUT_DIR` 改不了收集路径。它只能影响沙箱内该命令自己的进程环境。
- **影响**：一个技能脚本若读 `$WEKNORA_SKILL_DIR` / `$PYTHONPATH`（后者在 `manager.go:43` 的黑名单里），模型可以在同一次 `shell_exec` 的 `env` 里伪造它，把 venv 指向攻击者选的解释器。这不是逃逸（都在沙箱内），但会让一个"本该用受管 venv 跑"的脚本改用系统 python，行为与提示词承诺不符，排查时极难定位。
- **修复**：在 `ShellExecTool.Execute` 的 `env := input.Env` 之后，对 `skills.InjectedSandboxEnvVars()` 的名字做一次过滤（与 `ApplyResolvedEnv` 同样的"已存在则不覆盖"语义改为"直接丢弃"），无论 `skill_name` 是否为空。
- **工作量**：S（<半天）

## 量化

| 包 | prod 文件 | prod 行 | test 文件 | test 行 |
|---|---|---|---|---|
| `internal/agent` | 152 | 39,626 | 136 | 27,851 |
| `internal/sandbox` | 54 | 16,904 | 59 | 17,657 |
| `internal/im` | 56 | 15,929 | 50 | 8,723 |
| `internal/mcp` | 9 | 2,112 | 9 | 1,126 |
| `internal/stream` | 3 | 907 | 1 | 224 |
| **合计** | **274** | **75,478** | **255** | **55,581** |

- 测试/生产行数比 **0.74**；`internal/im` 仅 **0.55**，是本 slice 中比例最低、而外部集成面最宽的包（10 个平台适配器 + 队列 + 会话生命周期）。
- 本 slice 内 `_ = <expr>` 形式的忽略错误 **76 处**（agent 12 / sandbox 7 / mcp 5 / im 9，其余在子包）；`panic(` **1 处**；`TODO|FIXME|XXX` **0 处**。
- 无 `_test.go` 的 prod 文件（规模 ≥40 行）：`internal/im/qaqueue.go`(409)、`internal/im/supervisor.go`(97)、`internal/im/command.go`(66)、`internal/agent/skills/manager.go`(411)、`internal/agent/compaction/cutpoint.go`(118)、`internal/agent/checkpoint.go`(43)。其中 `qaqueue.go` 含全部 Redis 分布式并发闸门（`globalGateScript`、`acquireGlobalGate`、`redisCheckAndIncrUser`），是本 slice 风险最高的无测试文件 —— 本报告的 S2/S3 两条 IM 队列发现正出自主此。
- `internal/agent/tools/hithink_finance/financial/` 下 5 个 prod 文件，`period.go`（有 82 行测试）覆盖三张表；`financial_indicator_detail.go`（130 行）零测试。
- 已实读确认**不是债**的部分（列出以免重复审计）：`internal/mcp/security.go` + `internal/utils/security.go:1303` 的 SSRF 双重校验（保存时 + 构造前，`internal/mcp/client.go:142`）、`internal/sandbox/url_guard.go:161` 的拨号层 IP 复检、`internal/sandbox/session_binding.go:17-36` 的租户键校验、`internal/mcp/oauth_lifecycle.go:119-230` 的跨实例刷新租约、`internal/sandbox/docker_remote_client.go:804-823` 的容器内 `timeout -s KILL` 包装、`internal/agent/tools/registry.go:288-306` 的 `executeRecovered`。
