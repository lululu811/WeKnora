# 技术债审计 — CLI 模块 (`cli/**`) + REST SDK (`client/**`)

## 结论

- **确认 S1 级凭据泄漏路径不存在**：`FileStore` 写 0600、目录 0700、keyring 优先且降级时三处（`login.go:381`、`config/view.go:254`、`doctor.go:297`）都有显式告警；`auth login` 无 `--password` flag（只走 `huh.EchoModePassword` 交互或 `--with-token` stdin），不会进 shell history；`auth token` 已有 TTY-only 泄漏提示（`token.go:175-181`）。凭据落盘这一路是本 slice 最扎实的部分。
- **确认 S1 级并发/资源耗尽不存在**：`RunBatch`（`cmdutil/batch.go:40-57`）是顺序循环不是 goroutine 池，`doc wait` 的并发用 `maxConcurrentPolls = 5` 信号量封顶（`doc/wait.go:139,189`），`uploaded` map 由顺序写入独占，多请求无并发上限问题。
- **`ClassifyHTTPError` 用字符串前缀而非 `errors.As` 解析 SDK 错误，在被 `fmt.Errorf("%w")` 包一层后 100% 塌缩成 `network.error`** —— 这是本 slice 唯一确认的 S2 正确性缺陷，且 `errors_test.go:20-30` 用 `fmt.Errorf` 伪造字符串而非构造真实 `*sdk.APIError`，所以测试永远发现不了它。
- **契约路径零漂移**：写了脚本从 `internal/router/*.go` 抽 367 条真实 gin 路由（处理 `apiKeyGroup`/`With`/`apiKeyRoute` 三种注册形态），与 `client/` 154 条路径逐一比对，**13 条初筛不匹配全部经人工核实为抽取器假阳性**，`cli/` 硬编码路径 0 条。路由层是干净的，债在字段层。
- **`client/` 结构体与 `internal/types` 有 11 处字段漂移**（`tags`/`profile`/`pending_subtasks_count`/`stall_state` 等服务端有、SDK 无），而 CLI 又在 6 个命令里手抄了同一份字段清单当 `--help` 广告 —— 漂移不会被编译器和测试发现，只会静默误导 agent 的 `--jq` 投影。
- **`client/` 30 个生产文件里 26 个零测试**（235 个方法 vs 21 个测试），且**恰好没有 `client.go` 和 `auth.go` 的测试** —— 即 CLI 全部退出码契约所依赖的错误分类核心层裸奔。

---

## 发现

### [S2] `ClassifyHTTPError` 靠字符串前缀匹配 SDK 错误，被包装后所有 HTTP 状态码退化为 `network.error`

- **位置**：`cli/internal/cmdutil/errors.go:396-425`（判定逻辑）、`cli/internal/cmdutil/errors_test.go:20-30`（测试盲区）、`client/knowledge.go:203`、`client/knowledge.go:211`、`cli/cmd/doc/upload.go:311`
- **证据**：CLI 侧的判定实现：

```go
// cli/internal/cmdutil/errors.go:396-404
func ClassifyHTTPError(err error) ErrorCode {
	if err == nil { return "" }
	msg := err.Error()
	rest, ok := strings.CutPrefix(msg, "HTTP error ")
	if !ok {
		return CodeNetworkError   // ← 唯一的非 HTTP 兜底
	}
```

  SDK 侧在 `client/client.go:243-245` 提供了本该被使用的类型化出口：
  ```go
  func (e *APIError) Error() string { return fmt.Sprintf("HTTP error %d: %s", e.StatusCode, e.Body) }
  ```
  且 `client/client.go:228-236` 的注释明确教用户用 `errors.As(err, &apiErr)` 分支。

  而 `client/knowledge.go:201-204` 恰好把它包了一层：
  ```go
  resp, err := c.httpClient.Do(req)
  if err != nil {
  	return nil, fmt.Errorf("failed to send request: %w", err)
  }
  ```

  静态复现（`strings.CutPrefix` 语义 + `APIError.Error()` 输出）：

  | 传入 `ClassifyHTTPError` 的值 | 实际返回 | 期望 |
  |---|---|---|
  | `HTTP error 404: {...}` （裸 APIError） | `resource.not_found` | `resource.not_found` |
  | `failed to send request: HTTP error 404: {...}` （knowledge.go:203 产出） | **`network.error`** | `resource.not_found` |
  | `upload /x/y.pdf: failed to send request: HTTP error 404: {...}` | **`network.error`** | `resource.not_found` |

  同一个值上 `errors.As(wrapped, &apiErr)` 恒为 `true` —— 类型信息没丢，是 CLI 选择了不去读。

- **影响**：`doc upload` / `doc upload --recursive`（`cli/cmd/doc/upload.go:311`、`upload_recursive.go:87`）经 `CreateKnowledgeFromFile` 返回的错误全部经由 `knowledge.go:203` 包装。因此：
  - 服务端返回 403（知识库无权）→ 用户看到 `network.error`，exit 1，而不是 `auth.forbidden`。
  - 服务端返回 404（KB 已删）→ `network.error` 而非 `resource.not_found`。
  - 服务端返回 429（限流）→ `network.error` 而非 `server.rate_limited`。

  这直接击穿 CLI 对 agent 的核心承诺：`cli/README.md` 的退出码表把 `server.rate_limited` 定义为"值得重试"，而 `network.error` 的重试语义完全不同（`errors.go:325-329` 的 `IsTransient` 对 `network.*` 也返回 true，所以**限流会被 agent 当网络抖动盲目重试**，反而加剧限流）。排查成本也高：用户看到"网络错误"会去 ping 主机，而真实原因是权限或配额。
  `client/knowledge.go:211` 的 409 分支还有同样的包装（`failed to parse response: %w`），会把 body 解码错误和 HTTP 错误混为一谈。

- **修复**（分两步，可独立上线）：
  1. `ClassifyHTTPError` 开头加类型化快路径，保留字符串解析作兜底（避免动既有测试）：
     ```go
     var apiErr *sdk.APIError
     if errors.As(err, &apiErr) {
         base := ClassifyHTTPStatus(apiErr.StatusCode)
         if base == CodeServerError && apiErr.Code == sdk.ServerErrNotFound {
             return CodeResourceNotFound
         }
         return base
     }
     ```
     这一处就覆盖了 `client/knowledge.go:203/211` 造出的全部包装错误，且 `apiErr.Code` 已经是结构化的，比现在用正则 `"code":1003\b`（`errors.go:361`）匹配 body 更稳。
  2. 顺手把 `errors_test.go:20-30` 的表驱动用例从 `fmt.Errorf("HTTP error 404: ...")` 改成构造真实 `*sdk.APIError`，并**新增一条"包一层 `%w` 后仍能分类"的回归用例** —— 现有 4 个 `TestClassifyHTTPError*` 全部只测裸字符串，这正是缺陷能存活的原因。
- **工作量**：M（1-3 天，含回归测试与 acceptance/contract/errorcodes_test.go 的对齐）

---

### [S2] `client/` 结构体与 `internal/types` 有 11 个字段漂移，CLI 另手抄 6 份字段清单当 `--help` 广告

- **位置**：`client/knowledge.go:24-50`（SDK 缺字段）、`cli/cmd/doc/list.go:21-27`、`cli/cmd/doc/view.go:17-23`、`cli/cmd/doc/upload.go:27-33`、`cli/cmd/doc/fetch.go:18`（手抄清单）
- **证据**：按 json tag 集合做差（`client/knowledge.go` 的 `Knowledge` vs `internal/types/knowledge.go:166` 的 `Knowledge`）：

  ```
  服务端有、SDK 无：tags, profile, pending_subtasks_count, stall_state,
                   knowledge_base_name, custom_metadata, last_activity_at,
                   last_faq_import_result, deleted_at, -
  SDK 有、服务端无：tag_id
  ```

  服务端侧这些字段是真实存在的业务字段，例如 `internal/types/knowledge.go:174`：
  ```go
  // Tags holds the tags associated with this knowledge (populated on query, not persisted directly).
  Tags []*KnowledgeTag `json:"tags" gorm:"-"`
  ```
  以及 `internal/types/knowledge.go:193`：
  ```go
  PendingSubtasksCount int `json:"pending_subtasks_count" gorm:"type:int;not null;default:0"`
  ```

  而 CLI 侧 6 个命令各自硬编码同一份 22 字段清单，其中 4 份完全相同（`docListFields` / `docViewFields` / `docUploadFields` / `docFetchFields`）。这份清单的唯一用途是拼进 `--help` 文本（`cli/internal/cmdutil/format.go:55-56`）：
  ```go
  hdr := "\n\nJSON fields available under .data (project with --jq '.data.<field>'...):\n  " + strings.Join(sorted, "\n  ")
  ```

- **影响**：`weknora doc list --help` 明确告诉 agent "JSON fields available under .data"，但列表里没有 `tags` / `profile` / `pending_subtasks_count` / `stall_state` —— 这些服务端**确实会返回**。agent 据此写 `--jq '.data[].pending_subtasks_count'` 判断"还有多少个子任务在跑"会拿到 `null`，且没有任何报错。同理 `doc view` 无法给用户展示文档的 profile 摘要。清单是纯字符串拼接，不参与编译、不参与测试，**服务端加字段 / 改字段名时没有任何机制会报警**，这是典型的静默契约腐烂。
  另外 `tag_id` 只在 SDK 侧存在（服务端 `internal/types` 无此 tag），说明两侧是各自演进的，从来没有对过。
- **修复**（最小可行）：
  1. 短期：给 SDK `Knowledge` 补齐缺失的 json tag 字段（`Tags []*KnowledgeTag`、`Profile`、`PendingSubtasksCount`、`StallState` 等，与 `internal/types/knowledge.go` 对齐），并把 4 份重复的 22 字段清单提取成 `cli/cmd/doc/fields.go` 里的一个 `var knowledgeFields`，四处引用同一变量。
  2. 中期：加一个契约测试（可放 `cli/acceptance/contract/`，那里已有 golden-JSON 的 in-process 驱动框架），用 `httptest` 返回一个满字段的 `Knowledge`，断言 CLI 透传给 `--format json` 的对象键集合与服务端 `internal/types` 一致 —— 这样服务端加字段时测试会红。
  3. 顺带核实 `tag_id`：确认是服务端真的删了/改名了，还是 SDK 写错了（两者必有一方在漂）。
- **工作量**：M（1-3 天）

---

### [S2] `client/` 30 个生产文件 26 个零测试，含 CLI 退出码契约所依赖的 `client.go` / `auth.go`

- **位置**：`client/client.go`（5 方法，含 `parseResponse`/`newAPIError`/`extractServerCode`）、`client/auth.go`（6 方法）、`client/knowledge.go`（26 方法）、`client/organization.go`（31 方法）等 26 个文件
- **证据**：

  ```
  client 导出方法总数：235
  client 测试函数总数：21
  零测试文件（26）：agent.go auth.go chunk.go client.go env_var.go
              evaluation.go example.go faq.go initialization.go knowledge.go
              knowledgebase.go mcp_endpoint.go mcp_service.go memory.go message.go
              message_suggestion.go model.go organization.go retrieval.go
              session.go skill.go sse.go system.go tag.go tenant.go web_search.go
  ```

  `grep -rn "parseResponse\|newAPIError\|extractServerCode\|APIError" client/*_test.go` 返回 **0 行** —— 整个 SDK 的 HTTP 响应解析与错误构造层没有任何测试。

- **影响**：本报告的第一条缺陷（S2 `ClassifyHTTPError` 塌缩）之所以能长期存活，直接原因就是 `parseResponse` / `newAPIError` 没有测试作为"行为锚点"，而 `cmdutil` 侧的测试又是用伪造字符串测的 —— 两头都没有真实 `*APIError` 流经，等于这条链路上没有任何一个测试见过真实的错误对象。
  同样地，`client/auth.go` 的 `Login` / `RefreshToken` 是 CLI 唯一登录路径（`client.go:80-87` 承认 `WithToken` 是 "compatibility alias ... preserved for two minor versions"），零测试意味着 token 刷新/登录的字段漂移（比如 `client/auth.go:135-137` 那个 `out.Tenant = out.ActiveTenant` 的后端改名兼容垫片）无人守护。
- **修复**（不必追求覆盖率数字，聚焦风险点）：
  1. `client/client_test.go`：表驱动覆盖 `parseResponse` 的全部分支（2xx 有/无 target、204、3xx、非 2xx、非 JSON body、body 为 JSON 但无 `code` 字段），断言返回的 `*APIError.StatusCode` 与 `.Code`。`extractServerCode` 单独测非 JSON 输入。
  2. `client/auth_test.go`：用 `httptest` 覆盖 `Login` 成功路径 + 409/401 错误路径，断言 `ActiveTenant`/`Tenant` 镜像关系。
  3. 复用现成的模式：`client/knowledgebase_duplicate_test.go:11-57` 已经是标准写法（`httptest.NewServer` + 断言 path/method/body），照抄即可，无需引入新依赖。
- **工作量**：M（1-3 天）

---

### [S2] `client/example.go` 是 261 行纯死代码，且自带 `min()` 遮蔽 Go 1.21 内置函数

- **位置**：`client/example.go:22-253`（`ExampleUsage`）、`client/example.go:256-261`（`min`）
- **证据**：
  - `grep -rn "ExampleUsage" --include="*.go" .` 除定义处外 **0 处引用**。
  - 它不是 godoc example（那要求小写 `Example` 前缀 + 无返回值 + `_test.go` 文件），而是一个 232 行的普通导出函数，打印到 stdout。
  - `client/example.go:256` 定义了 `func min(a, b int) int`，而 go.mod 声明 `go 1.24.2` —— 1.21 起 `min` 已是内置函数，定义包级 `min` 会遮蔽内置。
  - 函数体内 `client/example.go:28` 还用了 `WithTimeout(30*time.Second)`，这是全仓库**唯一**一处业务代码调用 `WithTimeout`（`cli/` 从不调用），意味着该选项的唯一实际使用点在一个永不执行的函数里。
- **影响**：编译进 SDK 二进制（`example.go` 不是 `_test.go`），增加体积；`min` 遮蔽内置会让后续在该包内写 `min(a, b)` 得到包内版本而非内置，语义相同所以不会出错，但属于会误导读者的噪音。真正的问题是**它伪装成文档**（函数名 `ExampleUsage`、注释写 "demonstrates the complete usage flow"），新人会以为这是权威用法示例，而它调用的 API（比如 `WithTimeout`）实际无人使用。
- **修复**：整文件删除。若要保留用法示例，改为 `client/example_test.go` 里的 `func Example() { ... }` + `// Output:` 注释，让 godoc 渲染且不编译进生产路径。
- **工作量**：S（<半天）

---

### [S3] `client/` 缺少版本号与兼容性承诺文档，代码里引用的 ADR 不存在

- **位置**：`client/client.go:80-84`、`client/go.mod`
- **证据**：
  ```go
  // client/client.go:80-84
  // WithToken is the v0.x compatibility alias for WithAPIKey. ...
  // is preserved for two minor versions per ADR.
  //
  // Deprecated: use WithAPIKey for X-API-Key, WithBearerToken for JWT.
  ```
  - `find . -iname "*adr*"` → **0 个结果**。被引用的 ADR 不存在，"two minor versions" 无从追溯起点。
  - `client/go.mod` 只有 `module` + `go 1.24.2`，**无 version 字段**；`client/` 下无 `CHANGELOG.md`（`cli/` 有 `cli/CHANGELOG.md`，SDK 没有）；根 `CHANGELOG.md` 中 `grep -c "client/"` = **0**，SDK 变更从不进 changelog。
  - `client/README.md`（482 行）中 `grep -n "version\|Version\|compat\|兼容"` 命中 0 条版本策略章节。
  - 实际在库中的 Deprecated 标记有 5 处（`client/agent.go:83`、`auth.go:38`、`client.go:84`、`initialization.go:136`、`knowledgebase.go:82`），无一生效期。

- **影响**：`client/` 是独立 Go module，若被外部导入（`go get github.com/Tencent/WeKnora/client`），下游无法知道哪些 API 会消失、什么时候消失。`WithToken` 的移除条件（"next major"）无锚点，实际不可执行；`client/auth.go:38` 那个 `Tenant` 字段的后端改名兼容垫片（`client/auth.go:135-137`）没有标注退役条件，可能永久留存。长期结果是：破坏性变更会在无预警情况下发生，或反过来因为没人敢删而永久积累 deprecated 表面。
- **修复**：
  1. 新建 `client/CHANGELOG.md`，采用 Keep a Changelog，首节标注当前版本与支持范围。
  2. 在 `client/README.md` 增一节"版本与兼容性"，写明：模块版本策略、`Deprecated:` 标记的含义、每条 deprecated 的计划移除版本。
  3. 把 `client/client.go:82` 的 "per ADR" 改成指向真实文档（或直接删掉这个短语，写明具体版本号）。5 处 `Deprecated:` 逐个补 `@deprecated 计划在 vX.Y 移除`。
- **工作量**：S（<半天）

---

### [S3] 流式命令（`chat` / `session ask` / `session resume` / `agent`）无任何超时上界，与 `doc wait` 的处理不一致

- **位置**：`cli/cmd/chat/chat.go:113-115`、`cli/cmd/session/ask.go`、`cli/cmd/session/resume.go`、`client/client.go:168-178`
- **证据**：SDK 层刻意不给流式请求设超时（设计正确，否则会掐断长 SSE）：
  ```go
  // client/client.go:161-167
  // doRequestStream executes a streaming (SSE) request without the client's
  // default 30-second blanket Timeout. http.Client.Timeout covers reading the
  // response body, so applying that default would sever long-running chat /
  // session-ask / continue-stream responses. Stream lifetime is governed by ctx
  // unless the caller explicitly supplied WithTimeout ...
  ```
  正确做法是在 **ctx** 上加界，CLI 也确实在需要的地方这么做了 —— 但只做了两处：
  ```go
  cli/cmd/doc/wait.go:166:    ctx, cancel := context.WithTimeout(ctx, opts.Timeout)   // --timeout flag
  cli/cmd/doctor/doctor.go:449: ctx, cancel := context.WithTimeout(ctx, pingTimeout)
  ```
  而 `chat` 的全部 flag 只有 3 个（`chat.go:113-115`：`--session` / `--reference` / `--verbose`），`session ask` / `resume` / `agent` 同样没有 `--timeout`；`grep -rn "context.WithTimeout" cli/cmd/chat/chat.go cli/cmd/session/*.go cli/cmd/agent/*.go` → 0 行。
  CLI 层也从不调用 `sdk.WithTimeout`（唯一调用点是死代码 `client/example.go:28`）。
- **影响**：服务端 SSE 挂住但不发 `done` 帧时，`weknora chat` 会无限期挂起。用户唯一的退出手段是 Ctrl-C（`cli/main.go:20` 的 `signal.NotifyContext` 确实接了 SIGINT），也就是说**唯一的上界是人**，CI / 脚本 / agent 调用没有自愈路径 —— 只能靠外部 `timeout` 包一层。这与 `doc wait` 有显式 `--timeout` + exit 124 的行为不一致，同一 CLI 内两种长任务语义不同。
- **修复**（低成本、与既有约定对齐）：
  1. 给 `chat` / `session ask` / `session resume` 加 `--timeout` flag，RunE 内 `ctx, cancel := context.WithTimeout(cmd.Context(), opts.Timeout)`，默认给一个宽松值（如 30m）。
  2. 超时时复用现成的 `cmdutil.CodeOperationTimeout`（`errors.go:66`）与 `ClassifyContextErr`（`batch.go:129-137`），自动得到 exit 124，不需要新错误码。
  3. 在 `cli/README.md` 的退出码表补一行说明。
- **工作量**：S（<半天，每命令约 15 行）

---

## 量化

### 代码规模

| 项 | 数字 |
|---|---|
| `cli/**` Go 文件 | 284（144 测试 / 140 生产） |
| `cli/**` 代码行 | 43,400（生产 20,171 / 测试 23,229） |
| `cli/**` 测试:生产行比 | 1.15 : 1 |
| `cli/` cobra 命令构造函数 (`func NewCmd*`) | 79 |
| `cli/cmd/*` 包 | 20 个（kb/doc/agent/session/auth 各 13/12/8/7/8 测试文件） |
| `cli/acceptance/testdata/wire/` golden 用例 | 14 |
| `client/**` Go 文件 | 36（30 生产 / 6 测试） |
| `client/**` 代码行 | 8,890 |
| `client/` 导出 SDK 方法 | 235 |
| `client/` 测试函数 | 21 |
| `client/` 零测试生产文件 | 26 / 30 |

### 契约比对（本次实测）

| 项 | 数字 |
|---|---|
| 从 `internal/router/*.go` 抽取的真实 gin 路由 | 367（`/api/v1` 下 366） |
| `client/` 中出现的 `/api/v1` 字面路径 | 154 条去重 |
| 路径不匹配（初筛） | 13 → **经核实全部为抽取器假阳性，真实漂移 0** |
| `cli/` 中硬编码 `/api/v1` 路径 | 0（全部经 SDK 收敛，good） |
| `Knowledge` 结构体 json tag 漂移 | 服务端有 SDK 无 **10** 个；SDK 有服务端无 **1** 个 |
| 手抄重复的 `--help` 字段清单 | 46 份字段清单中 6 组重复，最大一组 4 份完全相同 |

### 坏味道计数（人工核对，非工具统计）

| 类别 | 计数 | 备注 |
|---|---|---|
| 无超时上界的流式命令 | 4（chat / session ask / session resume / agent） | |
| 用字符串而非类型判定 SDK 错误的分类函数 | 1（`ClassifyHTTPError`） | 连带 4 个测试全部测不到真实对象 |
| 重复字段清单定义 | 6 组 / 共 15 个 var | |
| 零测试的 client 生产文件 | 26 | |
| 死代码文件 | 1（`client/example.go` 261 行） | |
| 无上限的批量并发 | **0** | `RunBatch` 顺序；`doc wait` 有 5 并发信号量 |
| 凭据明文/权限问题 | **0** | 0600/0700 已验证，keyring 降级有告警 |
| 响应体未关闭 | **0** | 全部经 `parseResponse` 或显式 `defer Close` |
| 巨型命令文件（>500 行） | **0** | 最大 `cli/internal/mcp/tools.go` 713 行，是工具注册表非命令逻辑 |
| `panic(` | 0 | CLI 全模块无 panic |

### 明确排除的非问题（已核实，避免误报）

- **响应体吞掉**：`parseResponse`（`client/client.go:285-297`）对非 2xx 读全文并塞进 `APIError.Body`，`Error()` 原样输出，用户能看到服务端原文。
- **分页一次性拉全量**：仅 `ListKnowledgeBases`（`client/knowledgebase.go:319`）无分页参数，但服务端 `internal/handler/knowledgebase.go:568` 的 `ListKnowledgeBases` 本身就是不分页的全量返回（注释说明"tenant-bounded KB list is small, typically <100 rows"），契约一致。有分页的端点 CLI 都提供了 `--all-pages` + `--limit` + `meta.has_more`（`cli/cmd/doc/list.go:180-205, 223-226`）。
- **Ctrl-C 半写状态**：`cli/main.go:20` 接了 `signal.NotifyContext`；`cli/cmd/doc/wait.go:200-226` 区分 Canceled 与 DeadlineExceeded；`upload_recursive.go:126-146` 取消时保留 130/124 而不误报为可重试的 5xx。
