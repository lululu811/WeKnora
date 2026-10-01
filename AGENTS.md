# AGENTS.md

Tencent's open-source knowledge management framework for Q&A, tasks and wikis — an LLM-backed RAG
system with agent execution, multi-vendor model routing, and a pluggable retrieval layer.

Monorepo: **six independently-built Go/Python/npm modules**. The root Go module is the primary
server; everything else is a separate module with its own lockfile and its own CI workflow.

## Setup commands

Dependencies are per-module. Run commands from the module root, not the repo root, except `make`.

```bash
# Root Go module (server) — cmd/server
make deps             # go mod download
make build            # go build -o WeKnora ./cmd/server (binary lands at repo root)
make build-prod       # versioned, stripped, CGO on
make build-lite       # single binary: sqlite + sqlite_fts5 + local file storage
make build-anydoc     # links the in-process Rust parser (needs cargo)
make run              # build, then run
make fmt && make lint # go fmt ./... ; golangci-lint run

# cli/ — separate Go module, Cobra CLI. make lint is `go vet ./...`, not golangci-lint.
cd cli && make build && make test && make test-coverage && make lint

# client/ — separate Go module, REST SDK
cd client && go build ./... && go test -race ./...

# frontend/ — Vue 3 + Vite + tdesign-vue-next. npm, not pnpm, despite pnpm-workspace.yaml.
cd frontend && npm install && npm run dev      # also: npm test, npm run type-check, npm run build

# docreader/ — Python parsing service (:50051), uv-managed
uv sync --project docreader
uv run --project docreader python -m unittest discover -s docreader/tests -p "test_*.py" -v

# python-service/ — datasource sync service, pip/pytest
cd python-service && pip install -r requirements.txt && python -m pytest
```

Local dev loop: `make dev-start` (docker-compose.dev.yml infra) → `make dev-app` (Air hot-reload on
`:18080`) → `make dev-frontend`; `make dev-stop` tears down.

## Project layout

- `cmd/` — binaries: `server` (primary), `desktop` (Wails), `download`
- `internal/` — all server code: `handler` → `application/service` → `application/repository`, plus `agent`, `models`, `stream`, `mcp`, `container` (dig DI), `router`, `middleware`, `types`
- `migrations/versioned/` — `golang-migrate`, sequential numeric filenames, per-DB subdirs (sqlite/mysql/paradedb)
- `config/` — `config.yaml`, `builtin_models.yaml` (+ `models.json` overlay)
- `cli/`, `client/`, `frontend/`, `docreader/`, `python-service/`, `miniprogram/`, `packages/` — separate modules (see above)
- `scripts/` — repo utilities, `scripts/git-hooks/`, `scripts/model-catalog/`
- `deploy/`, `docker/`, `helm/` — container and chart packaging
- `testdata/`, `third_party/`, `licenses/` — fixtures, vendored code, third-party notices
- `docs/`, `website-docs/` — long-form documentation and the docs site

## Code style

- Go: `gofmt` + `gofumpt`, 120-char line limit (`.golangci.yml`, linters `lll`/`govet`/`revive`)
- CI checks formatting on **changed lines only** (`base...head`), not the whole tree
- Vue/TS: `vue-tsc --build` for type-check; there is no ESLint/Prettier config — match surrounding
  component style
- Python: stdlib `unittest` in `docreader`, `pytest` in `python-service`; no ruff/mypy config present
- All dependency injection goes through `uber/dig` in `internal/container/container.go` — never
  construct services inline
- Config: Viper reads `config/config.yaml` with `${ENV}` substitution. `.env.example` (sections A–J)
  is the canonical env reference — add new env vars there.

## Testing instructions

- Root module: `make test` (`go test -v ./...`); single package with
  `go test ./internal/agent/... -run TestName -count=1`
- Build-tagged: `go test -tags anydoc ./...` and `go test -tags desktop ./internal/container`
- CI excludes `docreader` from the root sweep; its Python and Go-client tests run in `docreader.yml`
- Concurrency-sensitive changes: `go test -race` (CI runs `-race` in `cli.yml`)
- In-memory infra only — `miniredis` for Redis, `sqlmock` for SQL, `gorm.io/driver/sqlite` +
  `sqlite-vec` for vectors. **No testcontainers.**
- Tests use `testify`: `require` for setup/teardown, `assert` for assertions
- Integration tests use the `_integration_test.go` suffix and respect `-short`
- Before pushing: `make model-catalog-check` if you touched `config/` or `scripts/model-catalog/`

## PR & commit conventions

This repo is a personal fork of `Tencent/WeKnora` by `lululu811`: **permanently diverged — never
open a PR, never push changes back upstream.** Remotes: `origin` = the fork, `upstream` = Tencent.

- **永久分叉，不提 PR，不回流上游** (permanently diverged — no PRs, no upstream contribution)
- `main` is a **pure mirror of upstream** — **NEVER** commit on it or modify files there
- All local patches live on the long-lived branch `mine`. Syncing upstream **MUST** use
  `git merge upstream/main` — **NEVER** `rebase`
- Before changing code, **MUST** read `GIT_WORKFLOW.md` at the repo root; it holds the branch
  model, conflict handling and self-check list
- Conventional Commits, scope-prefixed: `feat(agent/tools):`, `fix(frontend):`, `chore:`, `docs:`
- Pre-commit and pre-push hooks mirror CI. Opt out with `HOOK_SKIP_TEST=1` (skip tests) or
  `SKIP_HOOKS=1` (skip all) — see `scripts/git-hooks/`
- CI lints only new code vs `origin/main`; keep unrelated reformatting in a separate commit

## Security

- **Never commit secrets.** `.gitignore` ignores all dotfiles except `.env.example` — `.env`,
  `.env.lite` and similar stay local. `cli.yml` runs `scripts/check-secret-tokens.sh` to catch
  committed credentials in docs
- `scripts/check-license-bundle.sh` and `test-license-bundle.sh` gate `THIRD_PARTY_NOTICES.md` —
  run them after changing dependencies
- Report vulnerabilities via GitHub private vulnerability reporting only, never public issues
  (see `SECURITY.md`)
