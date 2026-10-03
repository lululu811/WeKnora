# 技术债审计 — 工程基础设施与供应链（Makefile / CI / docker / deploy / helm / scripts / config / 依赖）

## 结论

- **切断 `mine` 后有 3 个 workflow 永远不跑**：`anydoc.yml`、`go-lint-cache.yml`、`docker-image.yml` 的 push 分支只写 `main` 或 `v*` tag，而 `main` 按分支模型零提交、`origin` 零 tag —— 唯一在 `anydoc.yml` 里编译 `anydoc` build tag 的路径在这个 fork 上从不执行，任何 Rust 侧破坏都进不了主线。
- **`client/` 模块没有任何 workflow 跑它的测试**：`go-lint.yml` 会 lint 它，但 `go test` 一次都没跑过；AGENTS.md 第 27 行写「`cd client && go build ./... && go test -race ./...`」是开发者自跑，不是 CI。6 个测试文件 / 30 个 prod 文件的 REST SDK 无回归网。
- **`python-service/`（99KB `main.py` + 31 个 pytest 文件 + 31 个 unit/e2e 测试）完全没有 CI**，13 个 workflow 无一提及；`miniprogram/` 的 `npm test` 也没有任何 workflow 执行。
- **`.golangci.yml` 只显式开 3 个 linter，但 golangci-lint v2 默认集带来了 7 个**（`errcheck`/`staticcheck`/`ineffassign`/`unused`/`govet`/`lll`/`revive`）；真正缺的是**安全与资源类整组**：`gosec`/`gocritic`/`bodyclose`/`noctx`/`rowserrcheck`/`sqlclosecheck` 全部关闭（共 105 个 linter 被禁用），而 `errcheck` 的 `check-blank` 默认为 `false`，所以 618 处 `_ = xxx` 忽略错误照样放行。
- **`.env.example`（918 行 / 346 个变量）与代码实际读取存在缺口**：代码读 230 个环境变量，`WEKNORA_WEB_DIR`、`MODELS_CONFIG`、`BUILTIN_MODELS_CONFIG`、`JIEBA_DICT_DIR` 等改变启动行为的开关在示例文件里完全没有出现，新部署只能靠读源码。
- **根模块 1214 个 Go 测试文件、约 19.3 万行测试代码，`app.yml` 跑 `go test` 但从不加 `-race`**（全仓只有 `cli.yml` 一处 `-race`），AGENTS.md 自己承认「并发敏感的改动用 `-race`」但 CI 不执行。

## 发现

### [S2] `anydoc.yml` 的 push 触发只绑 `main`，在 `mine` 唯一工作分支上从不执行 —— Rust 解析引擎（含唯一的 `cargo audit`）失去 CI 覆盖

- **位置**：`.github/workflows/anydoc.yml:8-9`、`.github/workflows/go-lint-cache.yml:12-13`、`.github/workflows/docker-image.yml:2-5`
- **证据**：
  ```yaml
  # anydoc.yml:8
  on:
    push:
      branches: [main]
      paths:
        - "third_party/anydoc-go/**"
  ```
  `go-lint-cache.yml:13` 与 `dsh-plugin.yml:17`、`mcp-server.yml:17` 同为 `branches: [main]`。而 `GIT_WORKFLOW.md:16-19` 规定 `main` 是「镜像，零本地提交」，`AGENTS.md:152` 同样声明「`main` is a **pure mirror of upstream** — **NEVER** commit on it」。实测 `git ls-remote --tags origin` 返回 0 个 tag，`git tag` 的 45 个 tag 全是 upstream 拉下来的，因此 `docker-image.yml` 的 `push.tags: v*` 也不会触发。
- **影响**：这个 fork 的任何 `third_party/anydoc-go/**` 或 `internal/infrastructure/docparser/**` 改动推上 `mine` 时，唯一的 Rust 编译 + `cargo audit` 路径不执行。`anydoc.yml:66-72` 的注释明确写着「as in RUSTSEC-2026-0187, an uncatchable abort on a hostile PDF）has to break the build」—— 而 WeKnora 在进程内解析不可信上传文件（`Dockerfile.app` 默认 `WITH_ANYDOC=1`），这条正是 S1 级攻击面的唯一自动防线。`go-lint-cache.yml` 同理只在 `main` 预热，导致 `go-lint.yml` 声明的「warm cache 6 分钟 vs 40s」失效（`go-lint.yml:37-41` 的注释解释了整个设计意图，但触发条件写错分支）。
- **修复**：三个 workflow 的 `push.branches` 加 `mine`（`anydoc.yml`、`go-lint-cache.yml`）；`docker-image.yml` 需要决定发布语义 —— 若 fork 要发版，加 `branches: [main, mine]` + `tags: ['v*']` 并在 `GIT_WORKFLOW.md` 写明 tag 只能打在 `mine` 上；若不发布，删掉该 workflow 免得每次 PR 显示一个永不触发的红叉。
- **工作量**：S（3 处各加一行 + 一段文档）

### [S2] `client/` 模块在 CI 中零测试执行，AGENTS.md 声称的命令没有任何 workflow 兑现

- **位置**：`AGENTS.md:27`、`.github/workflows/go-lint.yml:104-124`（只做 lint 与模块探测）
- **证据**：全 13 个 workflow 中 `grep -rn "working-directory: client|cd client|./client/" .github/workflows/*.yml` 返回空。`app.yml:16` 与 `:44` 显式 `!client/**` 排除了它；`docreader.yml:99` 里的 "Go client tests" 是 `docreader/client`（gRPC stub），与 `client/` SDK 无关。`go-lint.yml:112` 把 `client` 加进 lint matrix，但 lint ≠ test。AGENTS.md:27 写的是「`cd client && go build ./... && go test -race ./...`」这种交互式命令，读者会误以为 CI 覆盖。
- **影响**：`client/` 是对外发布的 REST SDK（`README.md` + `README_EN.md` 双语，30 个 prod 文件 / 6 个测试文件），它是 `cli/`（`cli.yml:57` 有 secret 扫描、`:49` 有 `-race`）的上游依赖。SDK 的 breaking change 会打到 CLI，而 CI 只会从 CLI 侧报错 —— 排查时错误现场在下游、根因在上游，本地也没人跑。实测 `client/go.mod` 无任何 require（纯 stdlib），`client/go.sum` 是 0 字节，所以 CI 加一个 job 的成本几乎为零。
- **修复**：新建 `.github/workflows/client.yml`（或在 `cli.yml` 复用 matrix 加一列 module），`go build ./... && go vet ./... && go test -race ./...`，paths 过滤 `client/**`；同时把 `AGENTS.md:27` 改成「CI 通过 `client.yml` 执行，以下为本地等价命令」。
- **工作量**：S（1 个 ~30 行 workflow + 1 行文档）

### [S2] `python-service/` 完全没有 CI：99KB `main.py` + 31 个测试文件从不在 runner 上执行

- **位置**：`python-service/main.py`（99,675 字节）、`python-service/tests/unit/*.py`（30 个文件）、`python-service/tests/e2e/test_http_api.py`（26KB）、`python-service/pytest.ini`
- **证据**：`grep -rn "python-service" .github/workflows/` 返回 `NONE`。`AGENTS.md:36-38` 把它列为六大模块之一并写明 `cd python-service && pip install -r requirements.txt && python -m pytest`，但没有任何 workflow 兑现。`docker-compose.yml` 的 `python-service` 服务（50052 端口）被 app 的 `analyze`/`screener`/`backtest` 工具族调用，是运行时依赖而非可选组件。
- **影响**：31 个测试文件全部只在开发者本机跑。其中 `test_halo_cninfo.py`（30KB）覆盖 AGENTS.md 反复强调的「period 不是报告期」「`YYYY-N` 不是 `YYYY-QN`」这两个「已经付出真实调试代价的陷阱」—— 它们在 CI 里没有任何回归保护，改坏了不会有人知道，直到某次年报分析静默返回空集。同时该模块是 13 个 workflow 里唯一有 `pytest.ini`（`xfail_strict = true`）却零 CI 的，配置形同虚设。
- **修复**：新建 `.github/workflows/python-service.yml`：`pip install -r requirements.txt` → `python -m pytest tests/unit`（e2e 需要真实服务，用 `-m "not e2e"` 或单独 job + `continue-on-error`），paths 过滤 `python-service/**`。注意 `requirements.txt` 全是 `==` 精确固定（无 lock 文件），CI 里 `pip install` 会解析传递依赖，建议同时生成 `python-service/requirements.lock`（`pip freeze`）供 CI 使用。
- **工作量**：S（1 个 workflow）；lock 文件 M

### [S3] `miniprogram/` 被 dependabot 监控但既无 lockfile 也无 CI；其 `test` 脚本指向模块外的文件

- **位置**：`miniprogram/package.json:7-9`、`.github/dependabot.yml:104-116`
- **证据**：
  ```json
  "scripts": { "test": "node --test ../tests/miniprogram/*.test.js" }
  ```
  该脚本从 `miniprogram/` 目录运行却指向仓库根的 `tests/miniprogram/miniprogram.test.js`（7,526 字节，唯一一个测试文件）。`grep -rn "miniprogram" .github/` 只命中 `dependabot.yml` 本身 —— 没有任何 workflow 的 paths 过滤器包含 `miniprogram/**` 或 `tests/miniprogram/**`。`miniprogram/package-lock.json` 不存在（`miniprogram/` 下也没有 `node_modules`，目前零依赖所以侥幸不需要）。
- **影响**：dependabot 配了 `miniprogram` 的 npm ecosystem，但该目录一旦引入第一个依赖就没有 lockfile，CI 也不存在，安装结果不可复现。同时 `test` 脚本的 `../tests/` 相对路径意味着 `cd miniprogram && npm test` 是唯一正确调用方式，从仓库根跑 `npm --prefix miniprogram test` 会解析到错误路径。
- **修复**：若小程序仍在维护：把 `miniprogram.test.js` 移入 `miniprogram/` 内（消除跨目录相对路径），生成 `package-lock.json`，加一个 3 行的 `miniprogram.yml`（`node --test`）；若已废弃，删掉 dependabot 条目和该 `test` 脚本，避免监控一个死模块。
- **工作量**：S

### [S2] `gosec`/`bodyclose`/`rowserrcheck` 等安全与资源类 linter 全部关闭，`errcheck` 的 `check-blank` 为默认 false —— 618 处忽略错误与 HTTP/SQL 资源泄漏零拦截

- **位置**：`.golangci.yml:1-18`（全文 18 行）
- **证据**：用仓库自带的 `golangci-lint`（实测版本 v2.13.2）直接查询生效集合：
  ```console
  $ golangci-lint linters --config .golangci.yml
  Enabled by your configuration linters:
  errcheck govet ineffassign lll revive staticcheck unused
  Disabled by your configuration linters:   # 共 105 个
  gosec gocritic bodyclose noctx rowserrcheck sqlclosecheck dupl errorlint nilerr misspell ...
  ```
  所以 `AGENTS.md:57` 说「linters `lll`/`govet`/`revive`」是**不完整**的 —— `errcheck`/`staticcheck`/`ineffassign`/`unused` 来自 v2 默认集，配置里一个字没写。两个关键后果：（a）`gosec`、`bodyclose`、`noctx`、`rowserrcheck`、`sqlclosecheck` 这组「安全 + 资源泄漏」linter 确实关闭；（b）`errcheck` 虽然开着，但 `.golangci.yml` 里**没有任何 errcheck settings**，`check-blank` 取默认 `false` —— 而 `check-blank: false` 的语义正是「不报 `_ = f()` 形式的显式忽略」，实测生产代码里这类写法有 **618 处**（`internal/` + `cmd/`，排除 `_test.go`），测试里另有 284 处。
- **影响**：（a）`gosec` 缺失时 SSRF / 路径穿越 / 弱随机 / 硬编码凭据这一整类不会被提示，而 `internal/utils/security.go` 有近千行 SSRF 白名单逻辑（`:388`、`:914`、`:955` 三处 `SSRF_WHITELIST` 解析），`internal/types/web_search_provider.go:87` 直接把「私网地址要手动加白名单」的责任交给运维 —— 这类安全边界正需要 `gosec` 的 G107 兜底。（b）`bodyclose`/`rowserrcheck`/`sqlclosecheck` 缺失意味着「`http.Response.Body` 未关闭」「`sql.Rows.Err()` 未检查」不在检查范围，这两者的典型后果是连接/goroutine 泄漏，表现为运行数天后服务无响应，排查极难。（c）`errcheck` + `check-blank: false` 组合下，618 处 `_ =` 里既有合理的（`defer f.Close()` 的返回值）也有真正吞错的（`rows.Close()` 漏检查、`json.Unmarshal` 失败被丢），CI 一视同仁全部放行，且**连一行配置都没写说明这是有意为之**。
- **修复**：分三步走，每步只加一个 linter 并配合 `go-lint.yml` 的 `--new-from-merge-base` 只卡新代码（历史债单独排期）—— **不要一次性全开**，那会让 400+ 文件的 backlog 变成永久红灯，等于关掉这个 workflow（正是 `go-lint.yml:9-14` 注释里描述的失败模式）。第一步（零历史债，收益最大）：`bodyclose` + `rowserrcheck` + `sqlclosecheck` + `noctx`；第二步：`errcheck.settings.check-blank: true` 并配 `exclusions` 放行 `.*_test\.go` 与明确的 `//nolint:errcheck`；第三步：`gosec`，对 G107（SSRF）用 `exclusions` 精确豁免已有防护的位置，同时补注释说明每个豁免为何安全。
- **工作量**：M（配置 S，但逐 linter 清理历史债是 M–L）

### [S2] 根 Go 模块 1214 个测试文件在 CI 里从不加 `-race`，AGENTS.md 的并发指引无自动化支撑

- **位置**：`.github/workflows/app.yml:141-144`、`.github/workflows/cli.yml:49`、`AGENTS.md:57-58`
- **证据**：
  ```yaml
  # app.yml:141
  mapfile -t pkgs < <(go list ./... | grep -v '/docreader/')
  go vet "${pkgs[@]}"
  go test "${pkgs[@]}"          # ← 无 -race
  go test -tags desktop ./internal/container
  ```
  `grep -rn '\-race' .github/workflows/` 全仓仅 1 处命中：`cli.yml:49` 的 `go test -race -coverprofile=coverage.out ./...`（cli 模块 127 行 go.sum，小模块）。AGENTS.md:139 却写「Concurrency-sensitive changes: `go test -race` (CI runs `-race` in `cli.yml`)」—— 措辞诚实，但读者容易推断根模块也有。
- **影响**：根模块有 193k 行测试、1214 个测试文件，是全仓最大的并发面（`internal/stream`、`internal/agent`、`internal/application/service` 全部有 goroutine/队列/共享缓存）。竞态检测只存在于 `cli.yml` 的小模块，根模块的数据竞争 CI 一次都发现不了。并发 bug 的特征是低概率、非确定性，恰恰是最难靠本地复现发现的一类。
- **修复**：在 `app.yml` 增加一个 `race` job，但**不要**给现有 job 加 `-race`（193k 行测试会超时，`timeout-minutes: 30` 放不下）—— 折中方案：`-race` 只跑最相关的包集合（`./internal/stream/... ./internal/agent/... ./internal/container/...`），或用 `-short` 跳过重集成测试。`timeout-minutes` 调到 45。
- **工作量**：M（需要试跑确定哪些包在 race 下不超时/不 flaky）

### [S2] `.env.example` 与代码实际读取的环境变量存在缺口，`WEKNORA_WEB_DIR` / `MODELS_CONFIG` / `JIEBA_DICT_DIR` 等启动开关缺失

- **位置**：`.env.example`（918 行 / 346 个变量名）、`internal/router/static.go:19`、`internal/models/runtime/overlay.go:101`、`internal/types/builtin_models_config.go:96`、`internal/types/evaluation.go:16`、`internal/container/container.go:1871`
- **证据**：从 `internal/` + `cmd/`（排除 `_test.go`）提取 `os.Getenv("X")` / `os.LookupEnv("X")` 得到 230 个生产环境变量；与 `.env.example`（含注释行里的 `# VAR=` 形式）做差集后，真正缺失且影响生产行为的至少有 21 个，其中被 Go 代码直接 `os.Getenv` 读取的有 4 个：
  ```go
  // internal/router/static.go:19     —— 决定 SPA 从哪个目录提供
  webDir := os.Getenv("WEKNORA_WEB_DIR")
  // internal/models/runtime/overlay.go:101  —— 模型元数据 overlay 路径
  path := os.Getenv("MODELS_CONFIG")
  // internal/types/builtin_models_config.go:96  —— 内置模型声明文件路径
  path := os.Getenv("BUILTIN_MODELS_CONFIG")
  // internal/types/evaluation.go:16   —— jieba 分词词典目录（中文召回质量）
  dictDir := os.Getenv("JIEBA_DICT_DIR")
  ```
  `.env.example` 中这 4 个变量的出现次数均为 0。`AGENTS.md:65` 声称「`.env.example` (sections A–J) is the canonical env reference — add new env vars there」，但这几项是代码读取而文档缺失的存量漂移。
- **影响**：三个文件路径变量是「静默失效」型 —— 设错/不知情时不报错，只是回退到默认行为（`webDir` 退回 `./web`、`dictDir` 退回内置小词典导致中文分词质量下降、overlay 不加载导致模型元数据缺失）。AGENTS.md 自己把 `.env.example` 定义为唯一权威参考，运维/新同事按它配置就会踩这三个坑。`config/models.json` 里的 `${DASHSCOPE_API_KEY}` 与 `DASHSCOPE_API_KEY` 的说明是齐的（`.env.example:456-463` 写得很好），说明维护标准是明确的，只是没有回归检查。
- **修复**：把这 4 项（+ 建议补 `DUCKDB_SKIP_EXTENSION_LOAD`、`DOCKER_CONFIG`/`DOCKER_CONTEXT`）按 A–J 分节补进 `.env.example`；再加一个 CI 步骤或 `scripts/check-env-example.sh`：提取生产 `os.Getenv` 变量名，与 `.env.example` 做差集，非白名单即失败。成本约 20 行脚本，永久消灭这类漂移。
- **工作量**：S（补文档）+ S（防漂移脚本）

### [S3] `.golangci.yml` 的 formatter 与 `app.yml` 的格式门禁口径不同（gofumpt 未被门禁检查）

- **位置**：`.golangci.yml:13-16`、`AGENTS.md:57`、`scripts/git-hooks/common.sh:120-125`
- **证据**：`.golangci.yml` 声明 `formatters: enable: [gofmt, gofumpt]`，而 `app.yml:108-128` 的 "Check formatting" 步骤只跑 `gofmt -l`：
  ```bash
  unformatted=$(
    git diff --name-only --diff-filter=ACMR -z "$diff_range" -- '*.go' ':!cli/**' |
      xargs -0 -r gofmt -l
  )
  ```
  `git-hooks/common.sh:120-125` 的 `check_gofmt_files()` 同样只调 `gofmt -w` / `gofmt -l`。`AGENTS.md:57` 写「Go: `gofmt` + `gofumpt`」。gofumpt 覆盖 gofmt 的全部规则再加约 10 条额外规则（如 `interface{}` → `any`、去掉多余空行、简化复合字面量），所以 gofmt 通过 ≠ gofumpt 通过。
- **影响**：gofumpt 只在 `go-lint.yml` 里对「变更行」生效，而 gofmt 对「变更行」在 `app.yml` 生效 —— 两者基线不同。开发者本地若没装 golangci-lint（`common.sh:133-138` 的 `run_golangci_if_available` 在没装时直接 `return 0` 跳过），提交的 gofmt-clean 代码可能 gofumpt-dirty，推送后被 `go-lint.yml` 拦下，CI 红了但看不出原因（`app.yml` 明明是绿的）。
- **修复**：要么把 `app.yml` 的格式检查换成 `golangci-lint fmt --diff`（与 `.golangci.yml` 单一真相源），要么从 `.golangci.yml` 移除 `gofumpt` 只保留 `gofmt`（降低承诺但口径统一）。推荐前者。
- **工作量**：S

### [S2] 13 个 workflow 里 6 个混用两代 `actions/*` 大版本，且全部用浮动 tag 而非 SHA 固定

- **位置**：`.github/workflows/dsh-plugin.yml:65,124,157,175,226,234`、`.github/workflows/mcp-server.yml:44,70,91,110,142`、`.github/workflows/docker-image.yml:59`
- **证据**：
  ```
  24 uses: actions/checkout@v6      ← 主流
   3 uses: actions/checkout@v4      ← mcp-server.yml
   4 uses: actions/setup-node@v4    ← dsh-plugin.yml
   3 uses: actions/setup-node@v6    ← app/cli/frontend
   4 uses: actions/upload-artifact@v7  vs  2 uses: actions/upload-artifact@v4
   4 uses: actions/download-artifact@v8 vs 1 uses: actions/download-artifact@v4
  ```
  全部 27 个 action 引用没有一个用 `uses: owner/action@<40位sha>` 固定。`.github/dependabot.yml:120-129` 的 `github-actions` ecosystem 被设成 `open-pull-requests-limit: 0` + 全量 `ignore`（只留 security-updates），**且前置条件是「Dependabot security updates must be enabled in Settings」（`dependabot.yml:31-32`）** —— 这个设置不在仓库里，无法从代码确认是否开启。
- **影响**：（a）浮动 tag 意味着上游 tag 被移动就会在下次 CI 静默换掉执行代码，`mcp-server.yml` 与 `dsh-plugin.yml` 这两个带 `permissions: id-token: write` 的发布 workflow 风险最高（OIDC 换取 PyPI/npm 发布权）。（b）`dsh-plugin.yml:72,143,197,234` 用 `npm ci || npm install` —— `npm ci` 失败时静默退化为 `npm install`，lockfile 约束完全失效且 CI 仍是绿的，这实际上取消了该模块的可复现构建。（c）`docker-image.yml:59` 用 `jlumbroso/free-disk-space@main`（浮动分支）。（d）若 dependabot security updates 未在仓库设置中开启，全部 7 个 ecosystem 的 CVE 修复 PR 一个都不会来，而 `dependabot.yml` 注释里写明的策略是「no scheduled version PRs, but CVE security updates still open PRs automatically」—— 策略实际可能完全不生效。
- **修复**：（a）把 `mcp-server.yml` / `dsh-plugin.yml` 的 action 统一到 v6/v7 系列；（b）删掉 `|| npm install` 四处 fallback（dsh 的两个依赖版本在 lockfile 里本就固定，`npm ci` 失败就是真失败，应让 CI 报红）；（c）到 GitHub Settings 确认 Dependabot security updates 已启用，并写进 `AGENTS.md` 的自检清单（这是仓库外的前提，代码无法保证）；（d）发布 workflow 的 action 改 SHA 固定 + dependabot 的 `github-actions` security group 保持开启即可自动升 SHA。
- **工作量**：S（版本统一）+ S（删 fallback）+ S（确认设置并文档化）

### [S3] `python-service/requirements.txt` 无 lock 文件、全量 `==` 固定顶层但传递依赖不固定，与 docreader 的 `uv.lock` 策略不一致

- **位置**：`python-service/requirements.txt`（30 行）、`docreader/uv.lock`（2072 行）、`docreader/pyproject.toml:8-32`、`mcp-server/uv.lock`（1652 行）
- **证据**：`python-service/requirements.txt` 全部用 `==`（`fastapi==0.109.0`、`pandas==2.1.4`、`numpy==1.26.3`、`pypdf==6.12.2` …），但没有 hash、没有 lock 文件、没有 `pyproject.toml`。对比：`docreader/pyproject.toml` 全部用 `>=` 宽松范围，但**有 `uv.lock` 锁住解析结果**，`docreader.yml:56` 用 `uv sync --locked` 强制 lock 一致。而 `python-service` 目录里既无 lock 也无 pyproject（只有 `pytest.ini` + `Dockerfile` + `requirements.txt`）。
- **影响**：`==` 只锁顶层，传递依赖每次安装都重新解析。半年后同一份 `requirements.txt` 在不同机器装出不同依赖树，而 `pandas 2.1.4` / `numpy 1.26.3` 的传递依赖链正是最容易出现 ABI 不兼容的（`pydantic 2.5.3` 也是）。一旦为 `python-service` 补 CI（第 3 条发现），这个不确定性会直接变成 CI 随机红。`docreader.yml:56` 的 `--locked` 是正确范式，`python-service` 完全没跟上。
- **修复**：最小改动 —— 加 `python-service/pyproject.toml`（`dependencies` 搬过去）+ `uv lock` 生成 `uv.lock`，CI 与 `Dockerfile` 都改用 `uv sync --locked`；或保守方案，直接提交 `pip freeze > requirements.lock` 并在 CI/Docker 用它。
- **工作量**：M

### [S3] 8 个 `scripts/*.sh` 顶层没有 `set -euo pipefail`，`build_images.sh` 用 `$?` 判断 docker build 失败但中间步骤会吞掉错误码

- **位置**：`scripts/build_images.sh:1`（无 `set -`）、`scripts/start_all.sh:1`、`scripts/dev.sh:1`、`scripts/quick-dev.sh:1`、`scripts/get_version.sh:1`、`scripts/doctor.sh:15`（仅 `set -uo pipefail`，无 `-e`）、`scripts/check-env.sh`、`scripts/cloud-image/prepare.sh`（唯一合规的）
- **证据**：`get_version.sh` 全文零个 `set -` 行（`grep -cE '^\s*set -' = 0`），而它被 `Makefile:158,161`、`docker-image.yml:26,244,253`、`release-lite.yml:99-100,220-221` 用 `eval "$(./scripts/get_version.sh env)"` 消费。`build_images.sh:139-157`：
  ```bash
  docker build --platform $PLATFORM \
      --build-arg GOPRIVATE_ARG=${GOPRIVATE:-""} \
      ... .
  if [ $? -eq 0 ]; then      # ← $? 来自 docker build，但无 set -e 时前面的失败已被吞
  ```
  `$PLATFORM` 与多个 `${VAR:-...}` 展开均未加引号。`scripts/test-git-hooks.sh` 与 `scripts/test-license-bundle.sh` 两个被 `app.yml:106,136` 调用的脚本**没有可执行位**（CI 靠 `bash ./scripts/xxx.sh` 显式调用才跑得通，`cli.yml:54,57` 的 `scripts/check-secret-tokens.sh` 则是直接执行、依赖可执行位 —— 两种风格并存）。
- **影响**：`get_version.sh` 被 `eval` 消费，若中途某个 `git`/`go` 调用失败，函数仍会输出完整的 `VERSION=...` 键值对，`eval` 成功但值是 `unknown` —— 构建出的二进制 `handler.Version` 显示 unknown，无报错。`build_images.sh` 是 `make build-images` 的实际执行者（`Makefile:216-224`），无 `set -e` 时 `get_version_info` 里的失败不阻断后续 `docker build`。这些都是「静默失败」型，与 `AGENTS.md:65` 强调的配置正确性文化背道而驰。
- **修复**：给这 6–8 个脚本加 `set -euo pipefail`（`get_version.sh` 需先确认 `env`/`ldflags` 模式下的非致命失败可容忍，再加 `|| true` 精修）；`build_images.sh` 的 7 处 `docker build` 全部加引号；统一用 `bash ./scripts/xxx.sh` 调用，或统一给所有 `.sh` 加可执行位。
- **工作量**：S（逐个脚本验证，加 `set -e` 后需回归 dev/start_all 流程）

### [S3] `check-secret-tokens.sh` 只扫 `cli/` 下 7 个文档文件，仓库 163 个 `.md` 中的 156 个不在扫描范围

- **位置**：`cli/scripts/check-secret-tokens.sh:17`、`.github/workflows/cli.yml:56-57`、`.github/workflows/cli.yml:35-38`（paths 过滤）
- **证据**：
  ```bash
  cd "$(dirname "$0")/.."          # → 进入 cli/
  FILES=$(git ls-files 'skills/**/*.md' 'README.md' 'AGENTS.md' 'CHANGELOG.md' 'ROADMAP.md')
  ```
  实测在 `cli/` 下该 glob 命中 7 个文件（`cli/AGENTS.md`、`cli/CHANGELOG.md`、`cli/README.md`、`cli/skills/weknora-rag-search/**` 4 个）。仓库根 `git ls-files '*.md' | wc -l` = 163。`AGENTS.md:165-166` 声称「`cli.yml` runs `scripts/check-secret-tokens.sh` to catch committed credentials in docs」，但只覆盖 `cli/` 子树 —— 根 `AGENTS.md`、`CLAUDE.md`、`CHANGELOG.md`（168KB）、`GIT_WORKFLOW.md`、`.env.example`（918 行）等全部不在扫描范围。
- **影响**：这是个人 fork，`AGENTS.md:144` 记录的 `WEKNORA_MEMORY_EVAL_API_KEY` / `OPENAI_API_KEY` 模型评测工作流（`WEKNORA_MEMORY_EVAL_API_KEY` 之类），凭据最容易被粘进的是根目录的 `CHANGELOG.md` / `CLAUDE.md` / 会话记录文档，而扫描器恰好看不到这些。目前仓库内确实干净（`git grep` 全部命中均可确认为示例值或代码变量名），但防线覆盖面与声明不符，未来的漏检成本是凭据泄漏。
- **修复**：把 `cd "$(dirname "$0")/.."` 改为定位仓库根（`cd "$(dirname "$0")/../.."`），glob 加上根级 `AGENTS.md CLAUDE.md CHANGELOG.md DEPLOY.md` 与 `docs/**/*.md`、`website-docs/**/*.md`；或改用 `git grep` 直接扫全树。调用点同步改为仓库根路径。
- **工作量**：S

### [S3] Helm chart 的 `podSecurityContext` 默认值在 app/frontend/postgresql/redis 四个组件上被空字典覆盖，`runAsNonRoot` 静默失效

- **位置**：`helm/values.yaml:34-46`（global 默认）、`helm/values.yaml:88-89`、`:155-158`、`:253-256`、`:322-325`、`helm/templates/app.yaml:39-43`
- **证据**：`values.yaml:38` 定义了全局 `containerSecurityContext: {allowPrivilegeEscalation: false}`，但每个组件各自又定义了一份：
  ```yaml
  # app.securityContext (values.yaml:88-89)
  securityContext:
    # runAsNonRoot: true  # Disabled - official images run as root
    allowPrivilegeEscalation: false
  ```
  `app.yaml:39-43` 用 `{{- with .Values.app.podSecurityContext | default .Values.global.podSecurityContext }}` —— `app.podSecurityContext: {}`（空字典）在 Helm 里是 **falsy**，`default` 会回落到 global，所以 pod 级 `seccompProfile: RuntimeDefault` 实际生效。但**容器级**没有这个 `default` 回落：`app.yaml:44-47` 是裸的 `{{- with .Values.app.securityContext }}`，四份组件副本各自覆盖，`global.containerSecurityContext` 从未被任何模板引用（`grep` 确认 `_helpers.tpl:206-211` 定义的 `weknora.containerSecurityContext` 模板**没有任何调用点**）。
- **影响**：`global.containerSecurityContext` 是死配置 —— 运维按 values.yaml 顶部注释去改它会完全无效，必须逐组件改四份。同时 `runAsNonRoot` 被显式注释掉的注释理由是「official images run as root」，但 `Dockerfile.app:105` 明确 `useradd -m -s /bin/bash appuser` 且 `docker-entrypoint.sh:70` 用 `gosu appuser` 降权 —— 这个理由对 app 已经不成立了（虽然 entrypoint 启动瞬间确实是 root，因为要 `chown` 挂载目录）。另外 helm.yml 的 `render_image` 测试只校验镜像 tag 三者一致（`helm.yml:31-66`），不覆盖任何 securityContext 语义，改坏了不会被发现。
- **修复**：要么在 `_helpers.tpl` 里实现「组件级覆盖 → 全局默认」的真实合并（`merge` + `hasKey` 语义）并让 4 个模板都走它；要么删掉 `global.containerSecurityContext` 和未被调用的 `weknora.containerSecurityContext` 模板，只保留组件级，减少一个「改了没用」的配置面。`helm.yml` 增加一个 `helm template ... | grep -q seccompProfile` 的断言。
- **工作量**：S

### [S3] `app.yml` 的 `paths` 过滤器把 `Makefile` 列为触发条件，但 `go test` 跑的是 `go list ./...` 的全量而非变更包，30 分钟超时靠运气

- **位置**：.github/workflows/app.yml:88`（`timeout-minutes: 30`）、`:135-144`
- **证据**：`app.yml` 跑的是
  ```bash
  mapfile -t pkgs < <(go list ./... | grep -v '/docreader/')
  go vet "${pkgs[@]}"
  go test "${pkgs[@]}"
  ```
  即**全量**包（1214 个测试文件 / 193k 行测试），与 paths 过滤器无关。`git-hooks/common.sh:160-172` 的注释解释了原因：「Testing only directly changed packages misses contract regressions」—— 这是合理的工程决策，但代价是每次 Go 改动都跑全量 193k 行测试。`app.yml:88` 的 `timeout-minutes: 30` 是唯一约束，而 `concurrency: cancel-in-progress: true`（`app.yml:71-72`）会在新 push 到达时取消进行中的 run —— 大 PR 上等于测试从未真正跑完。
- **影响**：仓库只增不减（256k 行 prod / 193k 行测试），全量测试时间随时间单调增长逼近 30 分钟硬顶。到顶后表现不是「CI 变慢」而是**随机失败**（有时刚好跑完、有时被 kill），且因为 `cancel-in-progress`，开发者会看到红色但本地全绿。这是典型的「技术债到期自动引爆」型。
- **修复**：短期 —— 把 `timeout-minutes` 提到 45，并给 `go test` 加 `-timeout 20m` 让超时以可读的形式报出；中期 —— 按 `git diff` 圈定「变更包 + 其反向依赖」缩小测试集合（`go list -deps` 反查），保留 `go vet` 全量；长期 —— 用 `gotestsum` + 测试结果缓存（`actions/cache` on `~/.cache/go-build` 已在做，但 test result 无缓存）拆分 shard。
- **工作量**：M

### [S3] 根 `go.mod` 的 `replace` 把 otelgrpc 从 0.67.0 降到 0.59.0，无注释说明降级原因

- **位置**：`go.mod:360`、`go.mod:332`
- **证据**：
  ```gomod
  go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.67.0 // indirect   # :332
  ...
  replace go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc => \
      go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.59.0         # :360
  ```
  紧邻的 `go.mod:362-364` 的 anydoc replace 有三行清晰注释（说明 vendored 原因 + 何时可删），而 :360 这条裸的、跨 8 个 minor 版本的降级没有一句注释。对比：otel 主线依赖是 `go.opentelemetry.io/otel v1.43.0`（`go.mod:75-78`），而 otelgrpc 被钉在 0.59.0（对应 otel 1.24 时代）—— 两个代际的 instrumentation 与 SDK 混用。
- **影响**：`dependabot.yml:57-67` 对根 gomod 配了 `open-pull-requests-limit: 0` + 全量 ignore（只走 security-updates），所以这个 replace 不会被自动升级，也不会有人知道它该在什么时候被移除。若 otel SDK 继续升到 1.5x，0.59.0 的 otelgrpc 可能在编译期或运行期（trace context 传播 / span 属性）出现不兼容，而这类问题在测试里通常测不出来。另外 `go.mod` 里还有 `otelhttp v0.68.0`（`:333`，未降级）—— 同为 contrib instrumentation，一个被钉一个没被钉，版本代际不一致。
- **修复**：补注释说明降级原因（protobuf 版本冲突？API 变更？）与移除条件；核对 `otelhttp v0.68.0` 与 `otelgrpc v0.59.0` 混用是否有意为之；如无必要，评估能否升到与 otel 1.43 匹配的版本并删掉 replace。
- **工作量**：S（补注释）/ M（若真要验证能否移除）

### [S3] `config/models.json` 参与生产模型解析但不被 `model-catalog-check` 覆盖，AGENTS.md 的「改了 config/ 就跑 check」承诺有盲区

- **位置**：`config/models.json`（697 字节，1 个 provider）、`scripts/model-catalog/generate.py:15`、`AGENTS.md:144`
- **证据**：`generate.py:15` 写死 `DATA = ROOT / "internal/models/catalog/data"`，`--check` 只校验那个目录下的生成物与 `sources.json`（`generate.py:37`）。`config/models.json` 是独立的 overlay（`internal/models/runtime/overlay.go:101` 通过 `MODELS_CONFIG` 或默认路径读取），内容是 `dashscope-anthropic` provider 的 Anthropic 协议端点（含 `qwen3.7-plus`、`context_window: 131072`、`max_output_tokens: 16384` 等手工元数据）。`grep -rn "models.json" .github/ scripts/model-catalog/ Makefile` 返回空 —— 没有任何 CI 或 make target 校验它。而 `app.yml:132-133` 的 "Verify generated model catalog" 只跑 `python3 scripts/model-catalog/generate.py --check`。
- **影响**：`models.json` 里的 `api_key: "${DASHSCOPE_API_KEY}"` 引用、模型 id、context_window 都是手工维护且无校验的。写错模型 id（与厂商实际不符）会导致该模型在 UI 里可选但调用必失败；`context_window` 写小会让长文档被静默截断。`AGENTS.md:144` 教「touched `config/` → `make model-catalog-check`」，但这个 check 覆盖不到 `models.json`，给出的是虚假安全感。
- **修复**：给 `generate.py --check` 增加 `config/models.json` 与 `config/builtin_models.yaml` 的结构校验（JSON/YAML 可解析、`${VAR}` 引用的变量名在 `.env.example` 里存在、model id 非空且唯一）。这同时能覆盖第 6 条发现的 `${...}` 引用漂移。
- **工作量**：S

### [S3] `AGENTS.md:58` 声称「CI 只检查变更行（`base...head`）」，但这只对 gofmt 和 golangci-lint 成立，对 `go vet` / `go test` 不成立

- **位置**：`AGENTS.md:57-58`、.github/workflows/app.yml:108-128`（gofmt 有 diff 过滤）、`.github/workflows/app.yml:141-144`（vet/test 无）
- **证据**：`app.yml:112-121` 的 diff 计算与 `:122-124` 的 `git diff --name-only --diff-filter=ACMR -z "$diff_range"` 只作用于 gofmt。紧随其后的 `:141-144` 是无条件全量 `go vet` + `go test`。`AGENTS.md:57` 写「CI checks formatting on **changed lines only** (`base...head`), not the whole tree」—— 这句本身是对的（限定在 formatting），但紧随 `:57` 的「Lint: `gofmt` + `gofumpt`, 120-char line limit（`.golangci.yml`, linters `lll`/`govet`/`revive`）」把 `govet` 归入 lint 行列出，读者会把「govet 也只查变更行」一起读进去，而 `go vet` 恰恰是全量的。
- **影响**：文档与实际的边界模糊，会让开发者误判「改了 A 包，B 包的 vet 问题不算我的责任」，而实际上任何 Go 改动触发全量 vet 都会因为历史 B 包的问题变红。这类「谁该修」的误判在团队协作里直接变成推诿。`AGENTS.md:160` 的「CI lints only new code vs `origin/main`」同理 —— 那只对 `go-lint.yml` 的 golangci-lint 成立，对 `go vet` 不成立（且 `go-lint.yml:57-63` 明确说 base 故意**不是** origin/main 而是 `github.event.before`）。
- **修复**：把 `AGENTS.md:57-58` 拆成两句：「Formatting (`gofmt`) 与 golangci-lint 只检查变更行（`base...head`）；`go vet` 与 `go test` 每次 Go 改动都跑全量」；`:160` 同样补充限定。纯文档修正，但能消除一整类协作误解。
- **工作量**：S

## 量化

**Workflow 清单与触发覆盖（13 个）**

| workflow | push.branches | 是否跑测试 | 备注 |
|---|---|---|---|
| `app.yml` | main, mine | `go test` 全量（无 `-race`） | 根模块 1214 个测试文件 |
| `go-lint.yml` | mine | 否（只 lint） | `--new-from-merge-base` |
| `go-lint-cache.yml` | main ❌ | 否 | 只预热缓存，**在 mine 上永不触发** |
| `cli.yml` | main, mine | `go test -race`（3 平台） | 全仓唯一 `-race` |
| `cli-e2e.yml` | 无 push | 仅 `acceptance_e2e` tag | 需 secrets，缺省跳过 |
| `docreader.yml` | main, mine | Python unittest 25 文件 + `docreader/client` | `uv sync --locked` |
| `frontend.yml` | main, mine | `npm test` + `vue-tsc` + build | 161 个 `.test.ts` |
| `helm.yml` | main, mine | 否（`helm lint` + image tag 断言） | 无 securityContext 断言 |
| `anydoc.yml` | main ❌ | 仅 docparser 包 | **含唯一 `cargo audit`** |
| `docker-image.yml` | tags `v*` ❌ | 否 | origin 零 tag，永不触发 |
| `mcp-server.yml` | main ❌ | unittest 4 Python 版本 | 带 OIDC 发布权限 |
| `dsh-plugin.yml` | main ❌ | `node --test` + Go contract | `npm ci \|\| npm install` 4 处 |
| `release-lite.yml` | 无 push（仅 dispatch） | 否 | `push.tags` 被注释掉 |

**模块与 CI 覆盖对照**

| 模块 | prod 规模 | 测试 | CI |
|---|---|---|---|
| 根 Go（server） | ~256k 行 | 1214 文件 / ~193k 行 | ✅ app.yml（无 `-race`） |
| `frontend/` | 278k 行 | 161 个 `.test.ts` | ✅ frontend.yml |
| `python-service/` | `main.py` 99KB | 31 个 test 文件 | ❌ 零覆盖 |
| `client/` | 30 个 prod 文件 | 6 个测试文件 | ❌ 零测试（只 lint） |
| `docreader/` | — | 25 个 test 文件 | ✅ docreader.yml |
| `cli/` | — | — | ✅ `go test -race` |
| `mcp-server/` | — | 若干 test_*.py | ✅ 4 版本矩阵 |
| `miniprogram/` | — | 1 个（模块外路径） | ❌ 零覆盖 |
| `website-docs/` | — | — | ❌ 零覆盖 |
| `packages/dsh-weknora/` | — | `node --test` + Go contract | ✅ 但 fallback 破坏 lock |

**依赖与可复现性**

- 根 `go.mod` 17.8KB / `go.sum` 4058 行（完整）；2 条 `replace`（otelgrpc 降级无注释、anydoc vendored 有注释）
- `cli/go.sum` 127 行（完整）；`client/go.sum` **0 字节**（无外部依赖，恰好合法）
- `frontend/package-lock.json` 5720 行 ✅；`packages/dsh-weknora/package-lock.json` 51 行 ✅（仅 2 个 devDep）
- **缺 lockfile**：`miniprogram/`（0 依赖，侥幸）、`python-service/`（30 行 `==` 固定顶层，传递依赖不锁）
- `docreader/uv.lock` 2072 行 ✅（`uv sync --locked`）；`mcp-server/uv.lock` 1652 行 ✅
- `.golangci.yml` **显式**启用 3 个（`lll` / `govet` / `revive`）+ 2 个 formatter（`gofmt` / `gofumpt`）
- 实际生效 **7 个**（`golangci-lint linters --config .golangci.yml` 实测，v2.13.2）：`errcheck govet ineffassign lll revive staticcheck unused` —— 多出的 4 个来自 v2 默认集，配置文件里没有字提及
- 禁用 **105 个**，其中与本 slice 相关的关键项：`gosec` `gocritic` `bodyclose` `noctx` `rowserrcheck` `sqlclosecheck` `dupl` `errorlint` `nilerr` `misspell` `unparam` `exhaustive` `containedctx` `contextcheck` `prealloc` `asciicheck` `forbidigo`
- `errcheck` 无 settings → `check-blank` 默认 `false` → 618 处生产 `_ = ` + 284 处测试 `_ = ` 不报
- Dependabot：7 个 ecosystem 全部 security-only（`limit: 0` + 全量 `ignore`），仅 `/cli` 收例行更新；前置条件「security updates 已启用」在仓库外
- GitHub Actions：27 处 `uses:`，**0 处 SHA 固定**，2 个大版本混用（checkout v6/v4、setup-node v6/v4、upload-artifact v7/v4）

**配置漂移**

- `.env.example` 918 行 / 346 个变量名（`# A.` ~ `# J.` 十节）
- 代码实际读取的生产环境变量：**230 个**（`os.Getenv` / `os.LookupEnv`，已排除 `_test.go`）
- 差集：**21 个**代码读而文档无，其中 Go 生产代码直接读取的 4 个：`WEKNORA_WEB_DIR`（`static.go:19`）、`MODELS_CONFIG`（`overlay.go:101`）、`BUILTIN_MODELS_CONFIG`（`builtin_models_config.go:96`）、`JIEBA_DICT_DIR`（`evaluation.go:16`）
- `docker-compose.yml` app 服务注入 **204 个**环境变量，其中仅 `TZ` 未出现在 `.env.example`（其余 203 个均覆盖）
- `.env.example` 中 85 个变量未被 compose app 服务透传（多为前端 / 其他服务专用）
- viper 走 `AutomaticEnv()` + `SetEnvKeyReplacer("."→"_")`，**无 `SetEnvPrefix`**（`internal/config/config.go:527-528`），因此 `config/config.yaml` 的嵌套键直接暴露为 `SERVER_PORT` 这类无前缀名，与 `WEKNORA_*` 系列并存 —— 已在 `config.go:56` 有注释说明

**脚本**

- shell 脚本总数 38（排除 `third_party/` / `node_modules/` / `.backup/`）
- 顶层**无** `set -e` 的 7 个：`build_images.sh`、`start_all.sh`、`dev.sh`、`quick-dev.sh`、`get_version.sh`、`check-env.sh`、`cloud-image/prepare.sh`(仅该文件有 `set -euo pipefail`，前 6 个无)
- `doctor.sh` 为 `set -uo pipefail`（有 `-u` 无 `-e`）
- 缺可执行位但被 CI 用 `bash ./scripts/x.sh` 调用的：`test-git-hooks.sh`、`test-license-bundle.sh`
- 被 `eval "$(... env)"` 消费因而错误静默传播的：`get_version.sh`（消费者：`Makefile:158,161`、`docker-image.yml:26,244`、`release-lite.yml:99,220`）
- `check-secret-tokens.sh` 覆盖 7 / 163 个 `.md` 文件

**测试跳过**

- 全仓 `t.Skip` 98 处（另 63 处按全局统计口径，本 slice 复核为 98）
- 最高频跳过原因：`set WEKNORA_*_POSTGRES_DSN to run ...`（4 个 PostgreSQL 集成测试）、`set FEISHU_APP_ID, FEISHU_APP_SECRET ...`（4 个）、`not a unix path layout`（4 个）、`host git is not using SHA-1 object names`（2 个）
- 即：PostgreSQL 迁移/仓储集成测试与飞书数据源连接器在 CI 里**全部跳过**
