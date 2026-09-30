package indicators

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// FrontendModulePath is where RenderFrontendModule's output is expected to live,
// relative to the repository root.
//
// The frontend toolchain (vite + tsx) has no YAML parser, and package.json is
// owned by another stream, so the frontend cannot import config/indicators.yaml
// directly. Instead the registry is rendered into a TypeScript module that is
// checked in, and TestGeneratedFrontendModule fails loudly whenever the YAML
// changes without regeneration.
const FrontendModulePath = "frontend/src/components/workspace/kline/indicator-meta.ts"

const frontendModuleHeader = `/**
 * GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Source of truth: config/indicators.yaml (see config/indicators.schema.md).
 * Regenerate with:
 *
 *   go test ./internal/indicators/ -run TestGeneratedFrontendModule -update
 *
 * If you change config/indicators.yaml and forget to regenerate, that test fails.
 * That is deliberate: a stale generated module is exactly the parameter drift
 * this file exists to prevent.
 *
 * Indicator *formulas* stay in the code (calcEMA/calcMACD/...); only metadata
 * lives here.
 */

export type IndicatorKind = 'line' | 'composite' | 'subchart' | 'overlay';
export type IndicatorPanel = 'main' | 'sub';
export type SeriesType = 'line' | 'bar';

export interface IndicatorParamMeta {
  name: string;
  value: number;
}

export interface IndicatorSeriesMeta {
  key: string;
  label: string;
  /** Formula family: DEMA | LONGBBI | BBI | SMA | MACD_DIF | ... | ZX_BRICK */
  formula: string;
  type: SeriesType;
  /** Zero line for bars; absent for lines. */
  baseValue?: number;
  precision: number;
  /** ZettarancPalette key, or a dynamic token (volume_bar | macd_hist | brick). */
  color: string;
  /** Stroke width for line series. Absent for bars. */
  lineWidth?: number;
  params: number[];
}

export interface IndicatorStorageMeta {
  backend: 'frontend' | 'duckdb';
  duckdbView: string;
  columns: Array<{ alias: string; column: string }>;
}

export interface IndicatorMeta {
  id: string;
  shortName: string;
  kind: IndicatorKind;
  panel: IndicatorPanel;
  formulaVersion: string;
  precision: number;
  defaultEnabled: boolean;
  /** Set when this id is a legacy alias of another indicator. */
  aliasOf?: string;
  summary: string;
  /** Knobs klinecharts shows in its settings panel; falls back to params. */
  calcParams?: number[];
  params: IndicatorParamMeta[];
  series: IndicatorSeriesMeta[];
  storage: IndicatorStorageMeta;
}

export interface ViewMeta {
  id: string;
  label: string;
  hint?: string;
  indicators: string[];
}

export interface IndicatorRegistryMeta {
  schemaVersion: number;
  /** |go - js| tolerance used by the cross-stack conformance test. */
  absTolerance: number;
  fixtureBars: number;
  indicators: IndicatorMeta[];
  mainPresets: ViewMeta[];
  subPresets: ViewMeta[];
}
`

const frontendModuleFooter = `
/** All registered indicators, in config/indicators.yaml declaration order. */
export const ALL_INDICATORS: IndicatorMeta[] = INDICATOR_META.indicators;

/** Main-chart mode presets (id -> label + indicator list). */
export const MAIN_PRESETS: ViewMeta[] = INDICATOR_META.mainPresets;

/** Sub-chart mode presets (id -> label + tooltip + indicator list). */
export const SUB_PRESETS: ViewMeta[] = INDICATOR_META.subPresets;

/**
 * Look up one indicator's metadata.
 *
 * Throws on an unknown id rather than returning undefined: a typo in a
 * calcParams reference must break loudly at registration time, not render an
 * empty chart.
 */
export function indicatorMeta(id: string): IndicatorMeta {
  const found = INDICATORS_BY_ID[id];
  if (!found) {
    throw new Error(
      '[indicators.yaml] unknown indicator id "' + id + '" (known: ' + ALL_INDICATORS.map((i) => i.id).join(', ') + ')',
    );
  }
  return found;
}

/** The first N periods of an indicator, in declaration order. */
export function indicatorParams(id: string): number[] {
  return indicatorMeta(id).params.map((p) => p.value);
}

/** One series' metadata by indicator id + series key. */
export function seriesMeta(indicatorId: string, key: string): IndicatorSeriesMeta {
  const found = indicatorMeta(indicatorId).series.find((s) => s.key === key);
  if (!found) {
    throw new Error('[indicators.yaml] unknown series "' + key + '" on indicator "' + indicatorId + '"');
  }
  return found;
}
`

// RenderFrontendModule returns the exact contents of FrontendModulePath for the
// given registry. Output is deterministic: the same YAML always renders the
// same bytes, so the staleness test has no false positives.
func RenderFrontendModule(reg *Registry) (string, error) {
	payload := struct {
		SchemaVersion int         `json:"schemaVersion"`
		AbsTolerance  float64     `json:"absTolerance"`
		FixtureBars   int         `json:"fixtureBars"`
		Indicators    []Indicator `json:"indicators"`
		MainPresets   []View      `json:"mainPresets"`
		SubPresets    []View      `json:"subPresets"`
	}{
		SchemaVersion: reg.SchemaVersion,
		AbsTolerance:  reg.Conformance.AbsTolerance,
		FixtureBars:   reg.Conformance.FixtureBars,
		Indicators:    reg.Indicators,
		MainPresets:   reg.Views.MainPresets,
		SubPresets:    reg.Views.SubPresets,
	}
	if payload.MainPresets == nil {
		payload.MainPresets = []View{}
	}
	if payload.SubPresets == nil {
		payload.SubPresets = []View{}
	}
	for i := range payload.Indicators {
		if payload.Indicators[i].Params == nil {
			payload.Indicators[i].Params = []Param{}
		}
		if payload.Indicators[i].Series == nil {
			payload.Indicators[i].Series = []Series{}
		}
		if payload.Indicators[i].Storage.Columns == nil {
			payload.Indicators[i].Storage.Columns = []Column{}
		}
		for j := range payload.Indicators[i].Series {
			// `type` is optional in the YAML (a line is the default) but
			// required in the generated TypeScript, so materialise the default
			// here instead of making the schema carry `type: line` everywhere.
			if payload.Indicators[i].Series[j].Type == "" {
				payload.Indicators[i].Series[j].Type = "line"
			}
		}
	}

	// Marshal to JSON (the structs already carry json tags chosen for
	// TypeScript), then unquote object keys so the generated file reads like
	// hand-written TS instead of JSON. Every key is camelCase/identifier-safe,
	// which Validate's field set guarantees.
	raw, err := marshalIndentStable(payload)
	if err != nil {
		return "", err
	}
	raw = unquoteJSONKeys(raw)
	raw = collapseNumericArrays(raw)

	var b strings.Builder
	b.WriteString(frontendModuleHeader)
	b.WriteString("\nexport const INDICATOR_META: IndicatorRegistryMeta = ")
	b.WriteString(raw)
	b.WriteString(";\n\n")
	b.WriteString("export const INDICATORS_BY_ID: Record<string, IndicatorMeta> = {\n")
	for _, ind := range reg.Indicators {
		b.WriteString("  " + ind.ID + ": INDICATOR_META.indicators[")
		b.WriteString(strconv.Itoa(indexOfID(reg, ind.ID)))
		b.WriteString("],\n")
	}
	b.WriteString("};\n")
	b.WriteString(frontendModuleFooter)
	return b.String(), nil
}

func indexOfID(reg *Registry, id string) int {
	for i := range reg.Indicators {
		if reg.Indicators[i].ID == id {
			return i
		}
	}
	return 0
}

// marshalIndentStable renders v as indented JSON with HTML escaping disabled so
// the Chinese labels in config/indicators.yaml stay readable in the output.
func marshalIndentStable(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return "", fmt.Errorf("render frontend module: %w", err)
	}
	return strings.TrimRight(buf.String(), "\n"), nil
}

// unquoteJSONKeys turns `"someKey":` into `someKey:` so the generated module
// reads as TypeScript. String *values* keep their quotes; only keys change.
func unquoteJSONKeys(jsonText string) string {
	var out strings.Builder
	out.Grow(len(jsonText))
	inString := false
	for i := 0; i < len(jsonText); i++ {
		c := jsonText[i]
		if c == '\\' && inString {
			// Escaped char inside a string: copy both bytes verbatim.
			out.WriteByte(c)
			if i+1 < len(jsonText) {
				i++
				out.WriteByte(jsonText[i])
			}
			continue
		}
		if c != '"' {
			out.WriteByte(c)
			continue
		}
		if !inString {
			// Opening quote. If it introduces an object key (i.e. the next
			// non-space byte after the closing quote is ':'), copy the key
			// without quotes.
			k := i + 1
			for k < len(jsonText) && jsonText[k] != '"' {
				k++
			}
			if k < len(jsonText) {
				if j := nextNonSpace(jsonText, k+1); j >= 0 && jsonText[j] == ':' {
					out.WriteString(jsonText[i+1 : k])
					i = k // loop's i++ steps past the closing quote
					continue
				}
			}
			inString = true
			out.WriteByte(c)
			continue
		}
		inString = false
		out.WriteByte(c)
	}
	return out.String()
}

// nextNonSpace returns the index of the next non-space byte at or after i, or -1.
func nextNonSpace(s string, i int) int {
	for ; i < len(s); i++ {
		switch s[i] {
		case ' ', '\t', '\n', '\r':
		default:
			return i
		}
	}
	return -1
}

// collapseNumericArrays rewrites multi-line arrays of bare numbers
// (`[\n  10,\n  14\n]`) onto one line. Period lists are the most-read part of
// the generated file and are worthless spread over four lines each.
func collapseNumericArrays(jsonText string) string {
	var out strings.Builder
	out.Grow(len(jsonText))
	for i := 0; i < len(jsonText); {
		if jsonText[i] != '[' {
			out.WriteByte(jsonText[i])
			i++
			continue
		}
		// Collect the array body, bail out if it holds anything but numbers,
		// commas and whitespace (i.e. it is not a period list).
		j := i + 1
		numeric := true
		for j < len(jsonText) && jsonText[j] != ']' {
			c := jsonText[j]
			switch {
			case c >= '0' && c <= '9', c == '-', c == '.':
			case c == ',', c == ' ', c == '\t', c == '\n', c == '\r':
			default:
				numeric = false
			}
			j++
		}
		if !numeric || j >= len(jsonText) {
			out.WriteByte(jsonText[i])
			i++
			continue
		}
		body := strings.Trim(jsonText[i+1:j], " \t\n\r")
		parts := strings.Split(body, ",")
		cleaned := make([]string, 0, len(parts))
		for _, p := range parts {
			if t := strings.TrimSpace(p); t != "" {
				cleaned = append(cleaned, t)
			}
		}
		out.WriteString("[" + strings.Join(cleaned, ", ") + "]")
		i = j + 1
	}
	return out.String()
}

// ---------------------------------------------------------------------------
// Python target
// ---------------------------------------------------------------------------

// PythonModulePath is where RenderPythonModule's output is expected to live.
//
// python-service has no YAML dependency (see requirements.txt) and adding one
// would mean touching a file this work does not own, so the Python side reads a
// generated module exactly like the frontend does. One authored source, two
// generated readers, one staleness test.
const PythonModulePath = "python-service/zettaranc/indicator_meta.py"

const pythonModuleHeader = `"""指标元数据（自动生成，请勿手改）。

真源是 config/indicators.yaml，schema 见 config/indicators.schema.md。
重新生成：

    go test ./internal/indicators/ -run TestGeneratedPythonModule -update

改了 YAML 却忘了重新生成，TestGeneratedPythonModule 会失败 —— 这正是这份文件
存在的意义：策略层的指标参数不能自己另写一份。

这里只有**元数据**（名字 / 参数 / 周期 / 精度 / DuckDB 列名）。公式实现仍在
zettaranc/{trend,volume,levels,pattern}.py 里各自保留。
"""

from __future__ import annotations

from typing import Any, Dict, List

SCHEMA_VERSION = `

const pythonModuleFooter = `

#: 指标 id -> 指标元数据
INDICATORS_BY_ID: Dict[str, Dict[str, Any]] = {
    ID: INDICATOR_META["indicators"][i] for i, ID in enumerate(INDICATOR_META["ids"])
}

#: 指标 id -> 周期列表（按声明顺序）
INDICATOR_PARAMS: Dict[str, List[int]] = {
    k: [p["value"] for p in v["params"]] for k, v in INDICATORS_BY_ID.items()
}

#: DuckDB 短别名 -> 真实列名。analysis 的 SQL 应该照着这张表生成列映射，
#: 不要在别处硬编码列名。
DUCKDB_COLUMNS: Dict[str, str] = {
    c["alias"]: c["column"]
    for ind in INDICATOR_META["indicators"]
    for c in ind["storage"]["columns"]
}

#: 跨栈容差（|go - js| <= 该值视为一致）
ABS_TOLERANCE: float = INDICATOR_META["absTolerance"]


def get_indicator(indicator_id: str) -> Dict[str, Any]:
    """按 id 取指标元数据。未知 id 直接抛错，不返回 None。

    拼错 id 必须在调用点炸掉，而不是让上层拿到一个空字典然后画出空白图。
    """
    found = INDICATORS_BY_ID.get(indicator_id)
    if found is None:
        raise KeyError(
            "config/indicators.yaml 里没有指标 %r（已知: %s）"
            % (indicator_id, ", ".join(INDICATORS_BY_ID))
        )
    return found


def params(indicator_id: str) -> List[int]:
    """按声明顺序返回指标周期。"""
    return list(INDICATOR_PARAMS[indicator_id])


def series_keys(indicator_id: str) -> List[str]:
    """返回指标所有输出字段名（figures 的 key，顺序即绘图顺序）。"""
    return [s["key"] for s in get_indicator(indicator_id)["series"]]
`

// RenderPythonModule returns the exact contents of PythonModulePath.
func RenderPythonModule(reg *Registry) (string, error) {
	payload := struct {
		SchemaVersion int         `json:"schemaVersion"`
		AbsTolerance  float64     `json:"absTolerance"`
		FixtureBars   int         `json:"fixtureBars"`
		IDs           []string    `json:"ids"`
		Indicators    []Indicator `json:"indicators"`
		MainPresets   []View      `json:"mainPresets"`
		SubPresets    []View      `json:"subPresets"`
		KnownGaps     []KnownGap  `json:"knownGaps"`
	}{
		SchemaVersion: reg.SchemaVersion,
		AbsTolerance:  reg.Conformance.AbsTolerance,
		FixtureBars:   reg.Conformance.FixtureBars,
		IDs:           reg.IDs(),
		Indicators:    reg.Indicators,
		MainPresets:   reg.Views.MainPresets,
		SubPresets:    reg.Views.SubPresets,
		KnownGaps:     reg.KnownGaps,
	}
	if payload.MainPresets == nil {
		payload.MainPresets = []View{}
	}
	if payload.SubPresets == nil {
		payload.SubPresets = []View{}
	}
	if payload.KnownGaps == nil {
		payload.KnownGaps = []KnownGap{}
	}
	for i := range payload.Indicators {
		if payload.Indicators[i].Params == nil {
			payload.Indicators[i].Params = []Param{}
		}
		if payload.Indicators[i].Series == nil {
			payload.Indicators[i].Series = []Series{}
		}
		if payload.Indicators[i].Storage.Columns == nil {
			payload.Indicators[i].Storage.Columns = []Column{}
		}
		for j := range payload.Indicators[i].Series {
			if payload.Indicators[i].Series[j].Type == "" {
				payload.Indicators[i].Series[j].Type = "line"
			}
		}
	}

	raw, err := marshalIndentStable(payload)
	if err != nil {
		return "", err
	}
	// Python dict keys stay quoted (unlike the TypeScript target, where they are
	// unquoted to read like hand-written TS), so unquoteJSONKeys is deliberately
	// NOT applied here.
	raw = collapseNumericArrays(raw)
	raw = jsonToPythonLiteral(raw)

	var b strings.Builder
	b.WriteString(pythonModuleHeader)
	b.WriteString(strconv.Itoa(payload.SchemaVersion))
	b.WriteString("\n\n")
	b.WriteString("INDICATOR_META: Dict[str, Any] = ")
	b.WriteString(raw)
	b.WriteString("\n")
	b.WriteString(pythonModuleFooter)
	return b.String(), nil
}

// jsonToPythonLiteral rewrites JSON's true/false/null as Python's
// True/False/None. The registry contains no string value with those words in
// it (indicator ids, formula names, labels and colour tokens are all
// lower_snake_case or CJK), so a textual rewrite is safe here.
func jsonToPythonLiteral(s string) string {
	r := strings.NewReplacer(
		": true", ": True",
		": false", ": False",
		": null", ": None",
	)
	return r.Replace(s)
}
