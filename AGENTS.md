# Repository Guidelines

> Project: **WeKnora** — an open-source LLM RAG + ReAct Agent + Wiki framework from Tencent.
> Module path: `github.com/Tencent/WeKnora` · Go 1.26 · VERSION `0.8.0` (2026-09-03).
> This document is an AI-assistant map of the repo. When in doubt, prefer existing patterns and the references it cites.

---

## Project Overview

WeKnora turns documents into queryable knowledge. It combines hybrid retrieval (vector + full-text + graph), an LLM-driven ReAct agent with tool/MCP execution, an auto-curated long-form Wiki mode, cross-session long-term memory, and a Skill sandbox. Sources cover 10+ document formats (PDF/Office/HTML/EPUB/MHTML/XMind/images/URLs) plus data-source connectors (Feishu, GitLab, Tencent IMA, Notion, Yuque, RSS, DingTalk, Confluence). It exposes a web UI, a Go CLI, a Go SDK, a WeChat Mini Program client, MCP servers, and an IM channel layer (WeCom/WeChat/Feishu/DingTalk/Slack/Telegram/Mattermost).

---

## Architecture & Data Flow

### Top-level shape

```
+-----------------------------+       +----------------------------+
| frontend/  Vue 3 + Vite     | <---> | cmd/server (Go) :8080      |
+-----------------------------+       |  Gin + dig + Viper + GORM  |
                                      |  internal/*                |
                                      +----------------------------+
                                                  |
                                                  v
+-----------------+  +----------------+  +-------------------+  +-----------------+
| docreader/      |  | internal/models|  | internal/         |  | internal/       |
| Python gRPC     |  | chat/embed/    |  | retriever/*       |  | datasource/*     |
| :50051          |  | rerank/vlm/asr |  | milvus/qdrant/    |  | feishu/gitlab/   |
| (parsers)       |  | (vendor SDKs)  |  | weaviate/...      |  | notion/yuque/... |
+-----------------+  +----------------+  +-------------------+  +-----------------+
```

### Request lifecycle (HTTP)

1. `cmd/server/main.go` calls `container.BuildContainer` (`internal/container/container.go` — uber/dig DI), then `runStartupBootstrap` (`cmd/server/bootstrap.go`), starts Gin on `cfg.Server.Host:Port` with retry + signal-driven shutdown.
2. `internal/router/router.go::NewRouter` mounts middleware in order: CORS → RequestID → Language → Logger → Recovery → ErrorHandler → auth (JWT/API-key/embed) → RBAC/capability/kb-access. Public routes: `/health`, swagger, IM callbacks, embed channels, static (Lite).
3. `internal/handler/*` parses/validates and delegates to `internal/application/service/*` (e.g. `knowledgeBaseService`, `sessionService`, `chat_pipeline/search.go`).
4. Services compose repositories under `internal/application/repository/*` and call vendor adapters in `internal/models/{chat,embedding,rerank,vlm,asr}/...`, `internal/infrastructure/docparser/*`, `internal/infrastructure/web_search/*`, `internal/datasource/connector/*`, `internal/im/*`, `internal/sandbox/*`.
5. Streaming: `internal/stream` (memory or Redis). Async jobs: `asynq` (`internal/middleware/asynqdl`).
6. Domain structs/interfaces live in `internal/types` and `internal/types/interfaces`. Cross-cutting: `internal/{logger,errors,middleware,tracing/langfuse,event,common/redislock,common/db_retry,storageurl,storageallowlist,embedpolicy,ipclass,browserskill,filetransport,searchutil,textconv}`.

### Key modules under `internal/`

| Subdir | Responsibility |
|---|---|
| `application/service/` | Business logic (KB, knowledge, session, FAQ, custom agent, datasource, wiki, memory, evaluation, skills, sandbox, tenant). |
| `application/service/chat_pipeline/` | RAG: hybrid search, rerank, MMR, fan-out. |
| `application/service/retriever/` | Retrieve engine registry/factory — wires milvus/postgres/sqlite/weaviate/qdrant/opensearch/elasticsearch/tencent_vectordb/neo4j/doris/volc vikingdb. |
| `application/service/file/` | Object-storage factory: local / S3 / TOS / KS3 / MinIO. |
| `application/repository/` | GORM-backed repos. |
| `agent/` | ReAct loop (`act.go`, `think.go`, `steer.go`), tool registry, skills, MCP, compaction, approval, prompts, token estimator. |
| `models/{chat,embedding,rerank,vlm,asr,limiter,vendors,parity,catalog}/` | Vendor catalog + per-vendor SDK adapters. Catalog resolution: `internal/models/catalog/resolve.go`. |
| `types/`, `types/interfaces/` | GORM domain models + per-domain Go interfaces (`KnowledgeBaseService`, `ChunkService`, `SessionService`, `RetrieveEngineRepository`, `MCPEndpointService`, etc.). |
| `handler/`, `handler/session/` | Gin handlers; SSE QA stream entrypoints in `handler/session/qa.go`. |
| `router/` | Route registration (chat, knowledge, chunk, debug). |
| `middleware/` | logger, auth, RBAC, KB access, embed auth, API-key gate, asynq DLQ. |
| `infrastructure/docparser/` | Parser engine registry: builtin, MarkItDown, PaddleOCR, MinerU, OpenDataLoader, anydoc (Rust cgo), gRPC/HTTP docreader. |
| `infrastructure/web_search/` | baidu, bing, bocha, brave, duckduckgo, exa, google, keenable, metaso, ollama, searxng, serply, tavily, zhipu. |
| `datasource/connector/` | feishu/{drive,wiki,core}, gitlab, confluence, dingtalk, ima, notion, rss, yuque. |
| `im/` | IM adapters + service. |
| `sandbox/` | Docker / E2B / Cube remote sandbox; terminal, websocket dialer, URL guard, ticket auth. |
| `mcpserver/`, `mcp/` | In-Go Streamable HTTP MCP server (`/mcp/:endpoint_id`) + outbound MCP manager with OAuth. |
| `stream/` | SSE stream manager (memory + redis). |
| `tracing/langfuse/` | OTLP Langfuse wrappers for chat/embedding/rerank. |
| `database/` | `golang-migrate` wiring for sqlite + postgres. |
| `browserskill/`, `storageurl/`, `storageallowlist/`, `embedpolicy/`, `ipclass/`, `common/redislock`, `runtime/`, `searchutil/`, `textconv/`, `filetransport/` | Browser extension manager; URL signing/stream/SSRF allowlist; IP classification; distributed lock; runtime helpers; chunk merge + text normalization; file response helpers. |

### Companion binaries (`cmd/`)

- `cmd/server/main.go` — primary HTTP server.
- `cmd/server/bootstrap.go` — env-driven one-shot hooks (`WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL`, legacy API-key hash backfill).
- `cmd/server/listen.go` — listen-with-retry + exponential backoff.
- `cmd/milvus-migrate/main.go` — standalone Milvus collection migration to multilingual BM25.
- `cmd/desktop/main.go` — Wails desktop shell binding.
- `cmd/download/` — model download helpers (DuckDB-related).

### CLI, MCP, SDK, desktop

- `cli/` — **separate Go module**; Cobra root `cli/cmd/root.go` with subcommands `agent, api, auth, chat, chunk, config, doc, doctor, kb, link, mcp, message, model, profile, search, session, skills` plus bundled Agent Skills (`weknora-rag-search`, `weknora-shared`, `embed`) and `acceptance/` contract/e2e suites.
- `mcp-server/` — **deprecated** Python MCP server (`main.py`, `weknora_mcp_server.py`); README redirects to in-Go Streamable HTTP server.
- `client/` — Go SDK for the REST API (sessions, KBs, knowledge Q&A + SSE, chunks, messages, models, evaluation, **sandbox skills**, **long-term memory**, auth). `client/cmd/agent_test/` is an interactive Agent-QA CLI.
- `examples/` — sample Agent Skills (`pdf-processing`) and a local MCP demo server.
- `docreader/` — Python FastAPI/gRPC parsing service on `:50051` (PDF/DOCX/PPT/Excel/Markdown/HTML/MHTML/EPUB/XMind/images/URLs; LibreOffice + Playwright WebKit). Routes via `parser/` registry; SSRF-safe HTTP client.
- `miniprogram/` — WeChat Mini Program client (chat + KB list + URL import).

---

## Key Directories

```
/                              # Go module root, Makefile, .air.toml, .env.example
cmd/                           # Binaries: server, desktop, milvus-migrate, download
internal/                      # All Go server code (cmd-style: types/services/handlers/middleware/infrastructure)
  container/                   # dig DI wiring
  config/                      # Viper loader
  router/                      # Gin routes
  handler/                     # HTTP handlers
  application/service/         # Business logic + chat_pipeline
  application/repository/      # GORM repos + retriever engines
  agent/                       # ReAct loop, tools, skills, MCP
  models/                      # chat/embedding/rerank/vlm/asr/vendor adapters + catalog
  infrastructure/              # docparser + web_search providers
  datasource/                  # Source connectors + scheduler
  im/                          # IM channel adapters
  sandbox/                     # Remote sandbox
  mcpserver/, mcp/             # MCP server (in) + client manager (out)
  middleware/                  # gin middleware
  tracing/langfuse/            # OTLP tracing
  database/                    # golang-migrate
cli/                           # Separate Go module: Cobra CLI + skills + acceptance tests
client/                        # Go SDK for the REST API
mcp-server/                    # Deprecated Python MCP server
docreader/                     # Python gRPC parser service
frontend/                      # Vue 3 + Vite + tdesign-vue-next (npm)
miniprogram/                   # WeChat Mini Program
examples/                      # Sample skills + MCP demo
migrations/                    # versioned/ sqlite/ mysql/ paradedb/ SQL files
config/                        # config.yaml + prompt_templates/ + builtin_agents.yaml + agent_type_presets.yaml + builtin_models.yaml.example
scripts/                       # Build/dev/CI/cloud-image helper scripts + git-hooks/
docker/                        # Dockerfile.{app, docreader, sandbox, odl-hybrid}
helm/                          # Helm chart
Formula/                       # Homebrew weknora-lite.rb
website-docs/                  # VitePress product site + user docs
docs/                          # Chinese in-repo engineering docs + generated swagger.yaml/.json/.go
testdata/                      # chat, MHTML, wiki fixtures
```

---

## Development Commands

All Go commands run from the repo root unless noted.

```bash
# Build
make build            # dev binary into ./bin/
make build-prod       # ldflags version + protobuf conflictPolicy=warn
make build-lite       # EDITION=lite -tags sqlite_fts5; also runs npm build for the frontend
make build-anydoc     # builds the Rust anydoc static lib first, then Go with -tags anydoc

# Run locally
make run              # go run ./cmd/server (respects .env / config/config.yaml)
make dev-start        # scripts/dev.sh: brings up postgres/redis/minio/neo4j/etc. via docker-compose.dev.yml
make dev-app          # backend with hot reload (uses .air.toml → port :18080)
make dev-frontend     # vite dev server
make dev-logs         # tail docker-compose.dev.yml logs
make start-all        # full launcher incl. optional Ollama

# Test
make test             # go test -v ./...   (root Go module)
make lint             # golangci-lint run   (uses .golangci.yml)
make fmt              # gofmt + gofumpt
make migrate-up       # scripts/migrate.sh up  (golang-migrate; postgres by default)
make migrate-down
make migrate-create name=foo   # produces NNN_foo.up.sql + .down.sql under migrations/versioned

# Docker
make docker-build-app
make docker-build-docreader
make docker-build-frontend
make docker-build-all
make docker-run / docker-stop / docker-restart
make pull-images
make clean-images / clean-db

# Packaging
make package-lite     # tar.gz single-binary distribution
make package-mac-app  # Wails .app
make model-catalog-check   # python scripts/model_catalog_diff.py against models.dev/api.json
make docs             # swag init → docs/swagger.{yaml,json,docs.go}
make install-swagger

# CLI module (cd cli/)
make test             # same target name, scoped to cli/
make test-coverage    # go test -coverprofile=coverage.out ./...; go tool cover -func=coverage.out
make lint             # go vet ./...
```

Hot reload watches `**/*.go`, `*.tpl/tmpl/html/yaml` (`exclude_dir`: frontend, migrations, etc.). Build uses `GO_BUILD_TAGS` + `get_version.sh` ldflags; pre-stop kills `:18080`.

---

## Runtime / Tooling Preferences

- **Go 1.26.0** (per `go.mod`). Use `go mod tidy` after dep changes; `go.sum` is large (~389KB) — commit it.
- **Rust** required only for `make build-anydoc` (anydoc Office parser, cgo-linked from `third_party/anydoc-go`).
- **Node 24 + npm** for `frontend/`. The frontend has a `pnpm-workspace.yaml` but uses `package-lock.json` — **npm is the real package manager**. Lockfile matters; never substitute yarn/pnpm.
- **Python 3.10+** for `docreader/`, `mcp-server/`, the `rerank_server_demo.py`, and several `scripts/*.py` helpers. Use **uv** for Python dependency management in CI.
- **Postgres** (ParadeDB recommended) for production; **SQLite** (`-tags sqlite_fts5`) for Lite. Legacy **MySQL** driver exists but is experimental.
- **Redis 7** for stream manager + redislock.
- **docreader** gRPC service at `:50051` (TLS optional; env `DOCREADER_GRPC_*`). Docker-compose uses `http://docreader:50051`.
- **Optional services** (compose profiles): `minio`, `neo4j`, `qdrant`, `milvus`, `weaviate`, `opensearch`, `doris`, `searxng`, `dex`, `langfuse`, `odl-hybrid`, `sandbox`, `mcp`.
- **Config**: Viper loads `config/config.yaml` (server, conversation, knowledge_base, extract, tenant, prompt_*_id), plus `prompt_templates/`, `builtin_agents.yaml`, `agent_type_presets.yaml`, `builtin_models.yaml.example`. `${ENV}` substitution + `AutomaticEnv` with dotted-key env replacer. Paths searched: `./`, `./config`, `$HOME/.appname`, `/etc/appname/`. **`godotenv` is only used by `cmd/desktop` (Wails app)** — the server reads env from the process, not `.env` directly.
- **Env config groups** (`.env.example`, ~794 lines): A deployment/runtime, B DB+Redis+Asynq+storage (MinIO/COS/TOS/S3/OBS/OSS), C `RETRIEVE_DRIVER` + per-vector-store + Neo4j, D models/Ollama/builtin_models, E docreader gRPC+TLS+ODL+PDF, F JWT/SYSTEM_AES_KEY/registration/RBAC/OIDC, G sandbox/agent timeouts, H SearXNG/Tavily/MCP, I Langfuse (cloud + self-hosted), J SSRF/proxies/BROWSERSKILL.
- **Migrations**: `golang-migrate` with **sequential numeric** filenames (`000007_*.up.sql` … `000108_*.up.sql` with sibling `.down.sql`). Driver-specific subdirs: `migrations/{versioned,sqlite,mysql,paradedb}`. `MIGRATIONS_DIR` env var defaults to `migrations/versioned`.
- **`.air.toml`** is the source of truth for hot reload.
- **Homebrew**: `Formula/weknora-lite.rb` installs the Lite tarball via XDG-aware wrapper.
- **Helm**: `helm/Chart.yaml` (appVersion `v0.8.0`).
- **Lite** mode (`make build-lite` + `.env.lite.example`): sqlite + sqlite_fts5 retriever, memory stream manager, local Ollama at `127.0.0.1:11434`, local file storage. Zero external deps.
- **Any CGO** with anydoc: `GO_BUILD_TAGS=anydoc`. Pre-built static lib via `scripts/build-anydoc-lib.sh` (Rust toolchain).
- **ProGuard of protobuf**: build with `protoc` conflict policy `warn` set via ldflags.

---

## Code Conventions & Common Patterns

### Module / package layout
- Use the `internal/` tree exclusively; never expose internal types via public packages.
- Every cross-cutting concern has its own package: `logger`, `errors`, `middleware`, `event`, `tracing/langfuse`, `common/redislock`, `storageurl`, `storageallowlist`, `embedpolicy`, `ipclass`, `searchutil`, `textconv`, `runtime`, `database`, `filetransport`, `browserskill`.
- Per-domain code lives under `internal/<area>/<domain>` (e.g. `internal/handler/session/`, `internal/application/service/memory/`, `internal/agent/skills/`).

### Dependency injection
- All wiring goes through **`uber/dig`** in `internal/container/container.go`. New services must be registered there; don't `init()`-style global singletons outside the container.
- Interfaces belong in `internal/types/interfaces/`; concrete types implement them and are returned via `dig`-bound providers.

### Errors
- Use the `internal/errors` envelope (`AppError` + `BadRequest/Unauthorized/Forbidden/NotFound/Conflict/TooManyRequests/InternalServer/ServiceUnavailable/Validation` + tenant/agent/vector-store variants). **Don't return `fmt.Errorf`/`errors.New` from handlers** — wrap into `AppError` so the global error middleware can shape the response.

### Logging
- `internal/logger` is a thin wrapper around `logrus` with ctx-aware `Info/Warn/Error/Debug/Fatalf` and `WithFields`. Always pass `context.Context` and a `logrus.Fields` map; never log secrets (`SYSTEM_AES_KEY`, JWT, API keys, OAuth tokens).

### Async / streaming
- Background jobs: `asynq` on Redis, plus `internal/middleware/asynqdl` dead-letter middleware.
- Streaming: `internal/stream` factory picks **memory** or **redis** manager; SSE responses stream from handlers (e.g. `internal/handler/session/qa.go`).

### Naming
- Handlers end with `Handler` (struct) in `internal/handler/<domain>.go`. Methods are `CreateX/GetX/ListX/UpdateX/DeleteX/PinX`. Sub-handler folders for `session/`, etc.
- Services end with `Service` (`knowledgeBaseService`, `sessionService`).
- Repositories end with `Repository`.
- Domain models are plain GORM structs in `internal/types/`.
- Vendor adapters: `internal/models/{chat,embedding,rerank,vlm,asr}/<vendor>.go` implementing the package's interface; catalog resolution in `internal/models/catalog/resolve.go`.
- Retriever engines: `internal/application/service/retriever/<engine>.go`; the registry is the only entry point.

### HTTP / API
- Routes registered by `internal/router/<topic>.go` (`routes_chat.go`, `routes_knowledge.go`, …); chain middleware at registration time.
- API-key + RBAC capability policies: `internal/middleware/api_key_gate.go` (per-route full / capability / KB-scope / platform-only).
- Embed channels use publish-token + session-token auth + global per-minute rate limit (`internal/middleware/embed_auth.go`).

### Configuration access
- Inject typed config structs (e.g. `*config.Config`) via dig; never read `os.Getenv` inside business code. The only direct env readers are bootstrap hooks in `cmd/server/`.

### Tests
- Pure stdlib `testing` + `testify` (`require` for setup/teardown, `assert` for assertions).
- In-memory infra: `miniredis` (`alicebob/miniredis/v2`), `DATA-DOG/go-sqlmock`, `gorm.io/driver/sqlite`, `asg017/sqlite-vec-go-bindings`. No testcontainers.
- Mock HTTP via `httptest.NewRecorder` + `gin.TestMode`.
- Integration-style files use `_integration_test.go` suffix (e.g. `internal/sandbox/docker_integration_test.go`) and respect short-mode conventions.
- CLI e2e uses build tag `acceptance_e2e`, gated by env `WEKNORA_E2E_HOST`/`WEKNORA_E2E_TOKEN`.
- Long-running tests should respect `-race` (CI uses `-race`).

### Lint / format
- `.golangci.yml` (v2): `lll` (line-length 120, tab-width 4), `govet`, `revive`, `revoke`. Formatters: `gofmt`, `gofumpt`. Exclusion: `docs/docs.go$`.
- Pre-commit hook (`scripts/git-hooks/pre-commit`) runs PR checklist + `gofmt`. Pre-push mirrors CI; opt-out via `HOOK_SKIP_TEST` / `SKIP_HOOKS`.

---

## Important Files

| Path | Why it matters |
|---|---|
| `cmd/server/main.go` | Primary entry; container build + bootstrap + Gin listen + shutdown. |
| `cmd/server/bootstrap.go` | One-shot env-driven bootstrap (system-admin promotion, API-key hash backfill). |
| `internal/container/container.go` | dig DI wiring — every new service registers here. |
| `internal/config/config.go` | Viper loader; YAML + env + prompt templates + agent presets. |
| `internal/router/router.go` | Middleware chain + public/embed/IM routes + health. |
| `internal/router/routes_chat.go`, `routes_knowledge.go` | RBAC/API-key gated route registration. |
| `internal/handler/session/qa.go` | `KnowledgeQA`/`AgentQA`/`SearchKnowledge` SSE entrypoints. |
| `internal/handler/auth.go`, `custom_agent.go`, `mcp_endpoint.go` | Auth + per-tenant custom agent + Streamable HTTP MCP endpoints. |
| `internal/application/service/chat_pipeline/search.go` | RAG: hybrid search, rerank, MMR, fan-out. |
| `internal/application/service/retriever/registry.go` | Per-vendor retriever registry. |
| `internal/agent/{act,think,steer}.go` | ReAct loop. |
| `internal/models/catalog/resolve.go` | Built-in model registry overlay. |
| `internal/types/interfaces/` | All per-domain interfaces (add new ones here). |
| `internal/errors/errors.go` | AppError envelope + constructors. |
| `internal/middleware/{auth,rbac,kb_access,api_key_gate,embed_auth,asynqdl}.go` | AuthN/authZ/DLQ. |
| `internal/infrastructure/docparser/engines.go` | Parser engine registry. |
| `internal/infrastructure/web_search/` | Web search providers. |
| `internal/sandbox/` | Docker / E2B / Cube remote sandbox. |
| `internal/mcpserver/server.go` | In-Go Streamable HTTP MCP server. |
| `internal/database/migration.go` | Migration runner. |
| `cli/cmd/root.go` + `cli/cmd/<sub>.go` | CLI entry and subcommands. |
| `client/` | Go SDK entry. |
| `docker/Dockerfile.app` | Production image (rust+node+browserskill → golang builder → debian-slim runtime). |
| `docker/Dockerfile.docreader` | Python docreader image (LibreOffice + Playwright WebKit). |
| `docker-compose.yml`, `docker-compose.dev.yml` | Production / dev service composition + profiles. |
| `Makefile` | All Make targets (build/test/lint/docker/migrate/dev/packaging). |
| `.env.example`, `.env.lite.example` | Full / Lite env reference (sections A–J). |
| `config/config.yaml` | Default typed config. |
| `migrations/versioned/` | Sequential SQL migrations. |
| `Formula/weknora-lite.rb` | Homebrew tap. |
| `helm/Chart.yaml`, `helm/values.yaml` | Kubernetes deployment. |
| `test_agent_config.sh` | Manual curl-driven smoke test for `/api/v1/initialization/*` + `/api/v1/tenants/.../agent-config`. |
| `.air.toml` | Hot reload. |

---

## Testing & QA

### Frameworks
- **Go**: `testify` (assert + require), in-memory `miniredis`, `go-sqlmock`, `gorm.io/driver/sqlite`, `sqlite-vec-go-bindings`. ~600 `*_test.go` files under `internal/`, `cmd/`, `client/`, `cli/`, `packages/dsh-weknora/`, `docreader/{client,proto}/`.
- **Python (mcp-server)**: `unittest` (`python -m unittest discover`).
- **Python (docreader)**: `unittest` + Playwright WebKit; integration tests against running gRPC.
- **TypeScript (miniprogram, dsh plugin)**: `node:test`.
- **Frontend**: `npm test`, `npm run type-check`, `npm run build` (CI in `frontend.yml`).

### Running tests
```bash
make test                         # root Go module
cd cli && make test               # CLI module
cd cli && make test-coverage      # CLI: coverprofile + go tool cover -func
go test -tags anydoc -count=1 ./...   # anydoc build-tagged tests
go test -tags acceptance_e2e -v -timeout=8m ./cli/acceptance/e2e/...   # CLI e2e
cd mcp-server && uv run python -m unittest discover -s . -p "test_*.py"
cd docreader && uv run python -m unittest discover -s docreader/tests -p "test_*.py" -v
node --test tests/miniprogram/miniprogram.test.js
./test_agent_config.sh            # manual smoke: agent-config endpoints + SQL check
```

### Coverage
- Root Makefile has no coverage target. CLI Makefile has `test-coverage` (coverprofile + `go tool cover -func`).
- `cli.yml` runs `go test -race -coverprofile=coverage.out ./...` across 3 OSes; no enforced coverage threshold.

### CI workflows (`.github/workflows/`)
- `app.yml` — root Go module: gofmt-diff, vet, test, build.
- `go-lint.yml`, `go-lint-cache.yml` — `golangci-lint v2.12.2`; matrix on `.` / `cli` / `client`; `--new-from-merge-base` filter.
- `cli.yml` — 3-OS matrix; `-race -coverprofile`; vocab + secret checks.
- `cli-e2e.yml` — `acceptance_e2e` build tag, gated by `WEKNORA_E2E_HOST`/`WEKNORA_E2E_TOKEN` secrets + `acceptance-e2e` PR label.
- `anydoc.yml` — `cargo-audit` + `go vet/test/build -tags anydoc`.
- `mcp-server.yml` — Python 3.10–3.13; PyPI publish via OIDC.
- `frontend.yml` — `npm test` + `npm run type-check` + `npm run build` + multi-stage Docker image.
- `docreader.yml` — Python unittest + Playwright WebKit + Go client/proto tests against running docreader gRPC.
- `docker-image.yml` — multi-arch buildx for UI/docreader/sandbox/app.
- `release-lite.yml` — Lite binaries (linux/darwin × amd64/arm64) + Wails desktop app.
- `dsh-plugin.yml` — DeepSeek-Harness plugin: typecheck, contract, e2e, drift, npm publish.

### Test data (`testdata/`)
- `chat_import_test.json` — synthetic Chinese multi-user chat for IM chat-import.
- `mhtml/titled-image.mhtml` + `mhtml/titled-image-contract.json` — MHTML parser parity fixture.
- `wiki_test/doc1_stardust_memo.md`, `doc2_psionic_engine.md`, `doc3_dr_cole_log.md` — zh wiki ingest/citation fixtures.

### Lint
- Linters: `lll` (line-length 120), `govet`, `revive`, `revoke`.
- Formatters: `gofmt`, `gofumpt`.
- Path exclusion: `docs/docs.go$` (auto-generated swagger).

---

## Practical Tips for AI Assistants

- **Before editing exported symbols**, run `lsp references` (or `grep -r '<Symbol>' internal/`) — many types are referenced across `handler`/`application/service`/`types/interfaces` layers; a rename or signature change without updating all callers will silently break the dig container.
- **New services / repos / handlers** must be registered in `internal/container/container.go`. The container constructs in a specific order; respect existing init order (config → langfuse → DB → file service → …).
- **New vendor adapters** (LLM/embedding/rerank/VLM/ASR/web_search/docparser/datasource/IM/sandbox) belong under their `internal/<area>/<vendor>/` package, implement the relevant interface, and (for models) be registered in `internal/models/catalog/resolve.go`.
- **Config keys**: prefer adding to `config/config.yaml` and reading via injected `*config.Config`. Use env vars only for deployment-time overrides; mirror them in `.env.example` under the correct section (A–J).
- **Migrations**: append the next sequential number (`ls migrations/versioned/ | tail`); produce both `.up.sql` and `.down.sql`; keep SQL driver-agnostic where possible; add a `migrations/sqlite/` sibling when adding columns touched by Lite.
- **Secrets**: never log `SYSTEM_AES_KEY`, JWT signing key, OAuth tokens, API keys, or PATs. Audit existing fields/middleware (`internal/storageallowlist`, `internal/ipclass`, `internal/embedpolicy`) before adding a new outbound network path — SSRF guards are layered.
- **CI parity**: every change to root Go code is checked by `app.yml` + `go-lint.yml`. Local pre-push (`scripts/git-hooks/pre-push`) mirrors CI; opt-out only with `SKIP_HOOKS=1`.
- **API breakage**: routes registered in `internal/router/routes_*.go`; breaking a route affects the Go SDK (`client/`) and the CLI (`cli/`). Coordinate across both modules.
- **MCP**: prefer the in-Go Streamable HTTP server (`internal/mcpserver/`) over the deprecated `mcp-server/` Python package.
- **Lite vs Full**: when adding a feature, decide which edition(s) it should support; Lite = sqlite + sqlite_fts5 + memory stream + local Ollama + local file storage.
- **Frontend**: never introduce a second package manager. Always commit `frontend/package-lock.json`. Build arg `VITE_IS_DOCKER=true` matters for asset paths.