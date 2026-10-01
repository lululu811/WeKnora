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

## Financial data subsystems

Two locally-maintained stacks sit on top of the RAG core. Both are forked/private; treat their
invariants as load-bearing.

### `hithink_finance` — Go tools over a read-only local DuckDB

`internal/agent/tools/hithink_finance/**` are thin, parameterised SQL over
`~/.hithink-finance/*.duckdb` (7 databases: market / financials / special / index / indicators /
fund / futures). Registration is an allowlist switch in `agent_service.go`; a tool that is not on an
agent's `allowed_tools` is neither in the model's tool schema nor callable.

Two traps that have already cost real debugging:

- **`period` is not the report period.** In `v_balance_sheet` / `v_income_statement` /
  `v_cash_flow_statement` it holds the *statement basis* (`annual` / `quarterly`, ~177k identical
  rows). The actual period is `fiscal_year` + `fiscal_period` (`Q1`–`Q4`, `FY`); order by
  `period_end_ms`. `report_date_ms` is corrupted by sync (many periods share one timestamp).
  The shared ordering lives in `financial/period.go` with regression tests — do not re-inline
  `ORDER BY period` in a new query.
- **`v_financial_indicators_detail.report` is `YYYY-N`** (`2026-2`), not `YYYY-QN`; `FY` must be
  mapped to `-4` in SQL or the join silently returns nothing.

`testdata/schema.json` is a schema snapshot gated by `schema_contract_test.go`, which scans tool
source for backtick SQL and validates every referenced table/column. Run it after touching any
hardcoded SQL; it catches drift without needing a live DuckDB.

### `halo` — annual-report fact pipeline (python-service/halo/)

Fills the gap the local databases have: fixed assets, CIP, inventory, intangibles, goodwill,
headcount, audit opinion, penalties, emissions — none of which exist locally. Source of truth is the
**cninfo** (巨潮资讯网) filing PDF; the local hithink data is only a cross-check ruler, never a
substitute.

```
cninfo (限速) → PDF → pypdf → 锚点定位 → 规则抽取 → 对账 → SQLite 事实库
                                                                          ↓
                              行业分类 → HALO 六维 → 成长性 → 7 个定性槽位 → 复算校验
```

Design invariants, each backed by a regression test:

- **Python owns every number.** `AI` fills the seven qualitative dimensions (moat / stag / ESG /
  management / shareholder / valuation / risk) and nothing else. `halo.analyze` returns
  *quantitative anchors* for them; `halo.verify` recomputes the composite. LLM arithmetic is a
  bug source — the risk term enters as `(10 − risk) × 0.10`, and getting its sign wrong silently
  rescales the whole score.
- **Missing ≠ zero.** `halo.ok=false` with a reason, or a `⚠️ 缺失` marker. Never substitute
  another caliber. Headcount is annual-report-only, so **HALO is legitimately uncomputable on
  interim reports** — say so rather than extrapolating.
- **Read the table's unit.** The segment-revenue table is 元 for some issuers and 百万元 for
  others; a missing `单位：` declaration means *skip the page*, not a 1e6 error.
- **Reconciliation is a filter, not a judge.** `status` is `verified` (two channels agree, or the
  extractor's own fields reconcile against DuckDB), `disputed` (refuted — never promoted by
  "the extractor is usually right"), or `pending`. `verified_by` records which of the two produced
  it.
- **`analyze()` always returns the same shape**, including on the very common
  "no filing synced yet" path, or callers crash with `KeyError` instead of reading
  "no data yet".

External HTTP: cninfo is rate-limited (`HALO_CNINFO_MIN_INTERVAL`, default 0.5s) because it
blocks IPs — a community-measured threshold is 300 requests / 5 minutes. Note that
`datacenter-web.eastmoney.com` and `push2his.eastmoney.com` sit behind **different WAFs**: one
being banned does not take the other down, which is why a client that only talks to `push2` can
fail wholesale while its `datacenter-web` calls keep working.

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
