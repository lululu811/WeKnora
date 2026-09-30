# `config/indicators.yaml` schema

Single source of truth for **indicator metadata** across the three stacks
(Go agent tools / Python strategy layer / Vue frontend chart).

**What lives here:** names, parameters, default periods, formula version,
display precision, and whether an indicator draws on the main chart or a
sub-chart.

**What does NOT live here:** formula implementations. Go, Python and
TypeScript each keep their own `calc*` functions. Unifying *parameters* is
what stops MA20 quietly meaning three different things; unifying *code* is a
separate, much larger project and is explicitly out of scope.

---

## Top level

| Key | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Bumped when a field below changes meaning. `internal/indicators` refuses to load a version it doesn't know. |
| `conformance` | object | yes | Tolerances used by the cross-stack conformance test. |
| `indicators` | list | yes | One entry per indicator. |
| `views` | object | yes | Main-chart and sub-chart mode presets. |
| `known_gaps` | list | no | Cross-stack disagreements that are known and still open. |

### `conformance`

| Key | Type | Meaning |
|---|---|---|
| `abs_tolerance` | float | `\|go - js\| <= abs_tolerance` counts as agreement. `0.01` because all three stacks round to 2 decimals; anything smaller tests float noise, not semantics. |
| `fixture_bars` | int | Length of the synthetic K-line series fed to all three stacks. Must exceed `114` or the `MA114` leg of `DG_YELLOW` is `null` for the whole series. |

## `indicators[]`

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | The identifier used by klinecharts, by `views.*.indicators`, and by the DuckDB alias map. Unique. |
| `short_name` | string | yes | Label shown in the indicator picker / tooltip. |
| `kind` | enum | yes | `line` = one formula, one line. `composite` = several **independent** formulas drawn together on the main chart (`Z_MAIN`). `subchart` = one sub-panel with several related series (`Z_MACD`). `overlay` = canvas decoration only, no series, no numbers (`Z_SIGNALS`). |
| `panel` | enum | yes | `main` or `sub`. Drives where the indicator may be attached. |
| `formula_version` | string | yes | Bump when a formula's *definition* changes. Conformance tests compare against this version. |
| `precision` | int | yes | Decimal places for display, `0..8`. |
| `calc_params` | int list | no | Knobs klinecharts shows in its settings panel. Defaults to `params` values. Only `Z_MAIN` overrides it (to keep its historical `[10, 14]` slots). |
| `default_enabled` | bool | no | Whether the indicator is on by default. |
| `alias_of` | string | no | Another indicator's `id`. Aliases must declare **identical** `params` and `series`; this is asserted by the loader and the conformance test (`Z_BRICK` is the legacy id of `ZX_BRICK`). |
| `summary` | string | no | One-line description for humans. |
| `params` | list | yes | `{name, value}` — the user-visible knobs, flattened for tooltips and prompts. |
| `series` | list | yes | One entry per drawn line/bar. Empty for `kind: overlay`. |
| `storage` | object | yes | Where the other stacks get their numbers. |

### `indicators[].series[]`

| Key | Type | Required | Meaning |
|---|---|---|---|
| `key` | string | yes | Field name emitted by the frontend `calc()` and consumed by klinecharts `figures`. |
| `label` | string | yes | Tooltip label. |
| `formula` | enum | yes | `DEMA` `LONGBBI` `BBI` `SMA` `EMA` `MACD_DIF` `MACD_DEA` `MACD_HIST` `KDJ_K` `KDJ_D` `KDJ_J` `RSL` `VOLUME` `ZX_BRICK`. |
| `type` | enum | no | `line` (default) or `bar`. |
| `base_value` | number | no | Zero line for bars. Omit for lines. |
| `precision` | int | yes | Per-series override; falls back to the indicator's `precision` when omitted. |
| `color` | enum | yes | A `ZettarancPalette` key (`white` `yellow` `orange` `sky` `purple` `auxAmber` `auxSky` `auxOrange` `kdjJ` `up` `down` `neutral`) or one of the dynamic tokens below. |
| `line_width` | number | no | Stroke width. Required on line series, forbidden on bars. |
| `params` | int list | yes | Positional parameters for the formula. |

**Dynamic colour tokens** — resolved at draw time from the current bar, not from
the palette: `volume_bar` (red/green by candle direction), `macd_hist` (red/green
by sign), `brick` (red/green/neutral by brick direction).

### `indicators[].storage`

| Key | Type | Meaning |
|---|---|---|
| `backend` | enum | `frontend` = only the browser computes this; `duckdb` = the Go/Python stacks read a precomputed column. |
| `duckdb_view` | string | View the columns live in, `""` when `backend: frontend`. |
| `columns` | list | `{alias, column}`. The alias is the short name used by the Go `marketRow` field and the Python `INDICATOR_COLUMNS` dict. |

`storage.columns` is what makes cross-stack drift **detectable**: the conformance
test asserts every declared column still appears in both
`internal/agent/tools/hithink_finance/analysis/data.go` and
`python-service/zettaranc/data_loader.py`. A column renamed in one stack only
fails the test instead of silently returning zeros.

## `views`

| Key | Type | Meaning |
|---|---|---|
| `main_presets` | list | Main-chart modes. Each has `id`, `label`, `indicators`. |
| `sub_presets` | list | Sub-chart modes. Each has `id`, `label`, `hint`, `indicators`. |

`indicators` entries may reference a klinecharts **built-in** (`MA`, `BOLL`,
`EMA`, …) which is not declared under `indicators:` — built-in parameters belong
to klinecharts.

## `known_gaps`

Cross-stack disagreements that have been found and are still open. Each entry has
`id`, `indicator`, `stacks`, `detected_by`, `description`, `resolution`.

**Declaring a gap does not make the test pass.** The conformance test still fails,
loudly, and names the gap id — so a recorded gap stays visible until someone
actually closes it, instead of being quietly forgotten.

---

## Consumers

| Side | How |
|---|---|
| Go | `internal/indicators.Load(configDir)` — `internal/indicators/meta.go` |
| Frontend | `frontend/src/components/workspace/kline/indicator-meta.ts`, **generated** from this file (`go test ./internal/indicators/ -run TestGeneratedFrontendModule -update`). The frontend toolchain has no YAML parser and `package.json` is not owned by the metadata work, so the file is codegen'd rather than parsed at runtime; the generator test fails loudly when this file changes without regeneration. |
| Python | `python-service/zettaranc/indicator_meta.py`, **generated** from this file (`go test ./internal/indicators/ -run TestGeneratedPythonModule -update`). `python-service/requirements.txt` has no YAML dependency and is owned elsewhere, so the module is codegen'd rather than parsed at runtime; the staleness test fails loudly when the YAML changes without regeneration. |
