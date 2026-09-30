// Package indicators loads config/indicators.yaml — the single source of truth
// for indicator *metadata* (names, parameters, default periods, formula
// version, display precision, main-vs-sub panel) shared by the three indicator
// stacks in this repository:
//
//	Go       internal/agent/tools/hithink_finance/analysis/*.go
//	Python   python-service/zettaranc/
//	Frontend frontend/src/components/workspace/kline/indicators.ts
//
// Formula *implementations* are deliberately NOT unified — each stack keeps its
// own calc functions. What is unified is the parameter set, because that is
// what silently drifts: three copies of "MA20" that disagree on the period are
// worse than three copies of the same formula. See RenderFrontendModule and
// conformance_test.go.
package indicators

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"gopkg.in/yaml.v3"
)

// FileName is the file this package reads out of the config directory.
const FileName = "indicators.yaml"

// SupportedSchemaVersion is the only schema_version this loader understands.
// Bump it in config/indicators.yaml whenever a field changes meaning; a newer
// file must fail loudly rather than be half-interpreted.
const SupportedSchemaVersion = 1

// Indicator kinds.
const (
	KindLine      = "line"      // one formula, one line
	KindComposite = "composite" // several independent formulas on the main chart
	KindSubchart  = "subchart"  // one sub-panel, several related series
	KindOverlay   = "overlay"   // canvas decoration, no series, no numbers
)

// Panels.
const (
	PanelMain = "main"
	PanelSub  = "sub"
)

// Backends.
const (
	BackendFrontend = "frontend"
	BackendDuckDB   = "duckdb"
)

// Dynamic colour tokens. These are resolved at draw time from the current bar
// rather than looked up in the frontend palette; every other `color` value must
// be a ZettarancPalette key.
const (
	ColorTokenVolumeBar = "volume_bar"
	ColorTokenMACDHist  = "macd_hist"
	ColorTokenBrick     = "brick"
)

var (
	paletteColorKeys = map[string]bool{
		"white": true, "yellow": true, "orange": true, "sky": true, "purple": true,
		"neutral": true, "auxAmber": true, "auxSky": true, "auxOrange": true,
		"kdjJ": true, "up": true, "down": true,
	}
	dynamicColorTokens = map[string]bool{
		ColorTokenVolumeBar: true, ColorTokenMACDHist: true, ColorTokenBrick: true,
	}
	validKinds    = map[string]bool{KindLine: true, KindComposite: true, KindSubchart: true, KindOverlay: true}
	validPanels   = map[string]bool{PanelMain: true, PanelSub: true}
	validBackends = map[string]bool{BackendFrontend: true, BackendDuckDB: true}
	validTypes    = map[string]bool{"line": true, "bar": true}
)

// Param is one named, user-visible knob.
type Param struct {
	Name  string `yaml:"name"  json:"name"`
	Value int    `yaml:"value" json:"value"`
}

// Series is one drawn line or bar belonging to an indicator.
type Series struct {
	Key       string   `yaml:"key"                 json:"key"`
	Label     string   `yaml:"label"               json:"label"`
	Formula   string   `yaml:"formula"             json:"formula"`
	Type      string   `yaml:"type"                json:"type,omitempty"`
	BaseValue *float64 `yaml:"base_value"          json:"baseValue,omitempty"`
	Precision int      `yaml:"precision"           json:"precision"`
	Color     string   `yaml:"color"               json:"color"`
	LineWidth float64  `yaml:"line_width"          json:"lineWidth,omitempty"`
	Params    []int    `yaml:"params"              json:"params"`
}

// IsBar reports whether the series renders as a bar (volume / histogram).
func (s Series) IsBar() bool { return s.Type == "bar" }

// Column maps a short alias used by the Go/Python stacks to a DuckDB column.
type Column struct {
	Alias  string `yaml:"alias"  json:"alias"`
	Column string `yaml:"column" json:"column"`
}

// Storage records where the *other* stacks get this indicator's numbers.
type Storage struct {
	Backend    string   `yaml:"backend"      json:"backend"`
	DuckDBView string   `yaml:"duckdb_view"  json:"duckdbView"`
	Columns    []Column `yaml:"columns"      json:"columns"`
}

// Indicator is one entry of config/indicators.yaml.
type Indicator struct {
	ID             string `yaml:"id"              json:"id"`
	ShortName      string `yaml:"short_name"      json:"shortName"`
	Kind           string `yaml:"kind"            json:"kind"`
	Panel          string `yaml:"panel"           json:"panel"`
	FormulaVersion string `yaml:"formula_version" json:"formulaVersion"`
	Precision      int    `yaml:"precision"       json:"precision"`
	DefaultEnabled bool   `yaml:"default_enabled" json:"defaultEnabled"`
	AliasOf        string `yaml:"alias_of"        json:"aliasOf,omitempty"`
	Summary        string `yaml:"summary"         json:"summary,omitempty"`
	// CalcParams are the knobs klinecharts shows in its settings panel. Empty
	// means "same as Params"; Z_MAIN overrides it to keep its historical
	// [10, 14] editable slots (see config/indicators.yaml for why).
	CalcParams []int    `yaml:"calc_params"     json:"calcParams,omitempty"`
	Params     []Param  `yaml:"params"          json:"params"`
	Series     []Series `yaml:"series"         json:"series"`
	Storage    Storage  `yaml:"storage"         json:"storage"`
}

// EffectiveCalcParams returns the knobs to hand to klinecharts: the explicit
// calc_params when present, otherwise the indicator's own parameters.
func (i Indicator) EffectiveCalcParams() []int {
	if len(i.CalcParams) > 0 {
		return i.CalcParams
	}
	return i.ParamValues()
}

// ParamValues flattens Params into their values, in declaration order.
func (i Indicator) ParamValues() []int {
	out := make([]int, 0, len(i.Params))
	for _, p := range i.Params {
		out = append(out, p.Value)
	}
	return out
}

// Param looks a parameter up by name. Returns 0 and false when absent.
func (i Indicator) Param(name string) (int, bool) {
	for _, p := range i.Params {
		if p.Name == name {
			return p.Value, true
		}
	}
	return 0, false
}

// View is a main-chart or sub-chart mode preset.
type View struct {
	ID         string   `yaml:"id"         json:"id"`
	Label      string   `yaml:"label"      json:"label"`
	Hint       string   `yaml:"hint"       json:"hint,omitempty"`
	Indicators []string `yaml:"indicators" json:"indicators"`
}

// Views holds the mode presets consumed by the frontend.
type Views struct {
	MainPresets []View `yaml:"main_presets" json:"mainPresets"`
	SubPresets  []View `yaml:"sub_presets"  json:"subPresets"`
}

// Conformance holds the cross-stack test settings.
type Conformance struct {
	AbsTolerance float64 `yaml:"abs_tolerance" json:"absTolerance"`
	FixtureBars  int     `yaml:"fixture_bars"  json:"fixtureBars"`
}

// KnownGap documents a cross-stack disagreement that is known and still open.
//
// It exists so a conformance failure can name the gap instead of just printing a
// column name: "go_stack_missing_kdj" is actionable, "column not found" is not.
// Declaring a gap does NOT make the test pass — the test still fails, on purpose,
// until the gap is actually closed.
type KnownGap struct {
	ID         string   `yaml:"id"          json:"id"`
	Indicator  string   `yaml:"indicator"   json:"indicator"`
	Stacks     []string `yaml:"stacks"      json:"stacks"`
	DetectedBy string   `yaml:"detected_by" json:"detectedBy"`
	Desc       string   `yaml:"description" json:"description"`
	Resolution string   `yaml:"resolution"  json:"resolution"`
}

// file is the whole YAML document.
type file struct {
	SchemaVersion int         `yaml:"schema_version"`
	Conformance   Conformance `yaml:"conformance"`
	Indicators    []Indicator `yaml:"indicators"`
	Views         Views       `yaml:"views"`
	KnownGaps     []KnownGap  `yaml:"known_gaps"`
}

// Registry is a loaded, validated config/indicators.yaml.
type Registry struct {
	SchemaVersion int
	Conformance   Conformance
	Indicators    []Indicator
	Views         Views
	KnownGaps     []KnownGap

	byID map[string]*Indicator
}

// GapFor returns the known gap covering an indicator+stack pair, or nil.
func (r *Registry) GapFor(indicator, stack string) *KnownGap {
	for i := range r.KnownGaps {
		g := &r.KnownGaps[i]
		if g.Indicator != indicator {
			continue
		}
		for _, s := range g.Stacks {
			if s == stack {
				return g
			}
		}
	}
	return nil
}

// Get returns the indicator with the given id, or nil.
func (r *Registry) Get(id string) *Indicator {
	if r == nil {
		return nil
	}
	return r.byID[id]
}

// IDs returns every indicator id in declaration order.
func (r *Registry) IDs() []string {
	out := make([]string, 0, len(r.Indicators))
	for i := range r.Indicators {
		out = append(out, r.Indicators[i].ID)
	}
	return out
}

// MainPresets returns the main-chart mode presets.
func (r *Registry) MainPresets() []View { return r.Views.MainPresets }

// SubPresets returns the sub-chart mode presets.
func (r *Registry) SubPresets() []View { return r.Views.SubPresets }

// Load reads indicators.yaml out of configDir (e.g. "./config"), validates it,
// and returns the registry. A missing file is an error: unlike builtin_agents.yaml
// this file is not optional — the whole point is that there is exactly one
// place where indicator parameters live.
func Load(configDir string) (*Registry, error) {
	return LoadFromFile(filepath.Join(configDir, FileName))
}

// LoadFromFile reads and validates an explicit indicators.yaml path.
func LoadFromFile(path string) (*Registry, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", FileName, err)
	}
	var f file
	if err := yaml.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("parse %s: %w", FileName, err)
	}
	reg := &Registry{
		SchemaVersion: f.SchemaVersion,
		Conformance:   f.Conformance,
		Indicators:    f.Indicators,
		Views:         f.Views,
		KnownGaps:     f.KnownGaps,
		byID:          make(map[string]*Indicator, len(f.Indicators)),
	}
	if err := reg.Validate(); err != nil {
		return nil, fmt.Errorf("validate %s: %w", FileName, err)
	}
	return reg, nil
}

// Validate enforces every invariant the three stacks rely on. It is deliberately
// strict: a malformed registry that still "mostly works" is how parameters drift
// back out of sync in the first place.
//
// Validate is idempotent — the id index is rebuilt from scratch on every call,
// so a caller may validate, mutate and validate again (the tests do exactly
// that).
func (r *Registry) Validate() error {
	// Rebuild rather than mutate: a partially populated index from an earlier
	// failed call must not turn every later call into "duplicate id".
	r.byID = make(map[string]*Indicator, len(r.Indicators))
	byID := r.byID

	if r.SchemaVersion != SupportedSchemaVersion {
		return fmt.Errorf("schema_version %d unsupported (want %d)", r.SchemaVersion, SupportedSchemaVersion)
	}
	if r.Conformance.AbsTolerance <= 0 {
		return fmt.Errorf("conformance.abs_tolerance must be > 0, got %v", r.Conformance.AbsTolerance)
	}
	if r.Conformance.FixtureBars <= 114 {
		// The MA114 leg of DG_YELLOW / Z_MAIN is null below 115 bars, so a
		// shorter fixture would silently skip the most drift-prone indicator.
		return fmt.Errorf("conformance.fixture_bars must be > 114 (MA114 leg), got %d", r.Conformance.FixtureBars)
	}
	if len(r.Indicators) == 0 {
		return fmt.Errorf("indicators: must not be empty")
	}

	for i := range r.Indicators {
		ind := &r.Indicators[i]
		if ind.ID == "" {
			return fmt.Errorf("indicators[%d]: id must not be empty", i)
		}
		if _, dup := byID[ind.ID]; dup {
			return fmt.Errorf("indicators: duplicate id %q", ind.ID)
		}
		if !validKinds[ind.Kind] {
			return fmt.Errorf("%s: kind %q not in {line,composite,subchart,overlay}", ind.ID, ind.Kind)
		}
		if !validPanels[ind.Panel] {
			return fmt.Errorf("%s: panel %q not in {main,sub}", ind.ID, ind.Panel)
		}
		if ind.FormulaVersion == "" {
			return fmt.Errorf("%s: formula_version must not be empty", ind.ID)
		}
		if ind.ShortName == "" {
			return fmt.Errorf("%s: short_name must not be empty", ind.ID)
		}
		if ind.Precision < 0 || ind.Precision > 8 {
			return fmt.Errorf("%s: precision %d out of range 0..8", ind.ID, ind.Precision)
		}
		if ind.Kind == KindOverlay && len(ind.Series) != 0 {
			return fmt.Errorf("%s: kind=overlay must have no series", ind.ID)
		}
		if ind.Kind != KindOverlay && len(ind.Series) == 0 {
			return fmt.Errorf("%s: kind=%s requires at least one series", ind.ID, ind.Kind)
		}
		if len(ind.Params) != len(ind.ParamValues()) {
			return fmt.Errorf("%s: params must all carry a value", ind.ID)
		}
		for _, p := range ind.Params {
			if p.Name == "" || p.Value <= 0 {
				return fmt.Errorf("%s: param %q has empty name or non-positive value %d", ind.ID, p.Name, p.Value)
			}
		}
		for _, v := range ind.EffectiveCalcParams() {
			if v <= 0 {
				return fmt.Errorf("%s: calc_params entry %d must be > 0", ind.ID, v)
			}
		}
		seenKey := map[string]bool{}
		for _, s := range ind.Series {
			if s.Key == "" {
				return fmt.Errorf("%s: series key must not be empty", ind.ID)
			}
			if seenKey[s.Key] {
				return fmt.Errorf("%s: duplicate series key %q", ind.ID, s.Key)
			}
			seenKey[s.Key] = true
			if s.Formula == "" {
				return fmt.Errorf("%s/%s: formula must not be empty", ind.ID, s.Key)
			}
			if s.Type != "" && !validTypes[s.Type] {
				return fmt.Errorf("%s/%s: type %q not in {line,bar}", ind.ID, s.Key, s.Type)
			}
			if s.Precision < 0 || s.Precision > 8 {
				return fmt.Errorf("%s/%s: precision %d out of range 0..8", ind.ID, s.Key, s.Precision)
			}
			for _, v := range s.Params {
				if v <= 0 {
					return fmt.Errorf("%s/%s: period %d must be > 0", ind.ID, s.Key, v)
				}
			}
			if !paletteColorKeys[s.Color] && !dynamicColorTokens[s.Color] {
				return fmt.Errorf("%s/%s: color %q is neither a palette key nor a dynamic token", ind.ID, s.Key, s.Color)
			}
			if !s.IsBar() && s.LineWidth <= 0 {
				return fmt.Errorf("%s/%s: line series requires line_width > 0", ind.ID, s.Key)
			}
			if s.IsBar() && s.LineWidth != 0 {
				return fmt.Errorf("%s/%s: line_width only allowed on line series", ind.ID, s.Key)
			}
			if s.IsBar() && s.BaseValue == nil {
				return fmt.Errorf("%s/%s: bar series requires base_value", ind.ID, s.Key)
			}
			if !s.IsBar() && s.BaseValue != nil {
				return fmt.Errorf("%s/%s: base_value only allowed on bar series", ind.ID, s.Key)
			}
		}
		if !validBackends[ind.Storage.Backend] {
			return fmt.Errorf("%s: storage.backend %q not in {frontend,duckdb}", ind.ID, ind.Storage.Backend)
		}
		if ind.Storage.Backend == BackendDuckDB {
			if ind.Storage.DuckDBView == "" {
				return fmt.Errorf("%s: storage.backend=duckdb requires duckdb_view", ind.ID)
			}
			if len(ind.Storage.Columns) == 0 {
				return fmt.Errorf("%s: storage.backend=duckdb requires at least one column", ind.ID)
			}
		} else if ind.Storage.DuckDBView != "" || len(ind.Storage.Columns) > 0 {
			return fmt.Errorf("%s: storage.backend=frontend must not declare duckdb_view/columns", ind.ID)
		}
		byID[ind.ID] = ind
	}

	// Aliases must be indistinguishable from their target, otherwise the two ids
	// drift apart the moment one of them is edited.
	for i := range r.Indicators {
		ind := &r.Indicators[i]
		if ind.AliasOf == "" {
			continue
		}
		target := byID[ind.AliasOf]
		if target == nil {
			return fmt.Errorf("%s: alias_of %q does not exist", ind.ID, ind.AliasOf)
		}
		if ind.AliasOf == ind.ID {
			return fmt.Errorf("%s: alias_of must not point at itself", ind.ID)
		}
		if !sameInts(ind.ParamValues(), target.ParamValues()) {
			return fmt.Errorf("%s: params %v differ from alias target %s %v",
				ind.ID, ind.ParamValues(), target.ID, target.ParamValues())
		}
		if len(ind.Series) != len(target.Series) {
			return fmt.Errorf("%s: series count %d differs from alias target %s (%d)",
				ind.ID, len(ind.Series), target.ID, len(target.Series))
		}
		for j := range ind.Series {
			if ind.Series[j].Formula != target.Series[j].Formula ||
				!sameInts(ind.Series[j].Params, target.Series[j].Params) {
				return fmt.Errorf("%s/%s: formula/params differ from alias target %s/%s",
					ind.ID, ind.Series[j].Key, target.ID, target.Series[j].Key)
			}
		}
	}

	if len(r.Views.MainPresets) == 0 {
		return fmt.Errorf("views.main_presets must not be empty")
	}
	if len(r.Views.SubPresets) == 0 {
		return fmt.Errorf("views.sub_presets must not be empty")
	}
	if err := r.validateViews("main_presets", r.Views.MainPresets, PanelMain, true); err != nil {
		return err
	}
	if err := r.validateViews("sub_presets", r.Views.SubPresets, PanelSub, true); err != nil {
		return err
	}
	for _, g := range r.KnownGaps {
		if g.ID == "" || g.Indicator == "" {
			return fmt.Errorf("known_gaps: 每条都需要 id 与 indicator")
		}
		if byID[g.Indicator] == nil {
			return fmt.Errorf("known_gaps[%s]: 指标 %s 不存在", g.ID, g.Indicator)
		}
		if len(g.Stacks) == 0 {
			return fmt.Errorf("known_gaps[%s]: stacks 不能为空", g.ID)
		}
	}
	return nil
}

// validateViews checks preset ids are unique, labels present, and that every
// referenced id is either a declared indicator on the right panel or a
// klinecharts built-in.
func (r *Registry) validateViews(field string, views []View, panel string, requireHint bool) error {
	seen := map[string]bool{}
	for i, v := range views {
		where := fmt.Sprintf("views.%s[%d]", field, i)
		if v.ID == "" {
			return fmt.Errorf("%s: id must not be empty", where)
		}
		if seen[v.ID] {
			return fmt.Errorf("views.%s: duplicate id %q", field, v.ID)
		}
		seen[v.ID] = true
		if v.Label == "" {
			return fmt.Errorf("views.%s[%d] (%s): label must not be empty", field, i, v.ID)
		}
		if requireHint && v.Hint == "" {
			return fmt.Errorf("views.%s[%d] (%s): hint must not be empty (every mode button needs a tooltip)", field, i, v.ID)
		}
		if len(v.Indicators) == 0 {
			return fmt.Errorf("views.%s[%d] (%s): indicators must not be empty", field, i, v.ID)
		}
		for _, id := range v.Indicators {
			ind := r.byID[id]
			if ind == nil {
				// klinecharts built-ins (MA, BOLL, …) are not declared here.
				continue
			}
			if ind.Panel != panel {
				return fmt.Errorf("views.%s[%d] (%s): indicator %s is panel=%s, expected %s", field, i, v.ID, id, ind.Panel, panel)
			}
		}
	}
	return nil
}

func sameInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// FindRepoRoot walks up from start looking for config/indicators.yaml. Returns
// "" when the file cannot be found, so callers can skip rather than fail.
//
// start is resolved to an absolute path first: filepath.Dir(".") == ".", so
// walking up from a relative "." would never leave the starting directory (and
// `go test` runs with the package directory as the working directory).
func FindRepoRoot(start string) string {
	dir, err := filepath.Abs(start)
	if err != nil {
		return ""
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "config", FileName)); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

// LoadRepoRegistry loads config/indicators.yaml by walking up from start.
func LoadRepoRegistry(start string) (*Registry, string, error) {
	root := FindRepoRoot(start)
	if root == "" {
		return nil, "", fmt.Errorf("could not locate config/%s above %s", FileName, start)
	}
	reg, err := Load(filepath.Join(root, "config"))
	return reg, root, err
}

// ---------------------------------------------------------------------------
// Process-wide registry (mirrors the builtin_agents / agent_type_presets pattern)
// ---------------------------------------------------------------------------

var (
	defaultRegistry *Registry
	defaultOnce     sync.Once
	defaultErr      error
)

// Default returns the process-wide registry, loading it from ./config on first
// use. Callers that need a specific config dir should use Load instead.
func Default() (*Registry, error) {
	defaultOnce.Do(func() {
		defaultRegistry, defaultErr = Load("config")
		if defaultErr != nil {
			// The server may run with the config dir elsewhere on the command
			// line; fall back to walking up from the working directory.
			reg, _, err := LoadRepoRegistry(".")
			if err == nil {
				defaultRegistry, defaultErr = reg, nil
			}
		}
	})
	return defaultRegistry, defaultErr
}

// SetDefault installs a registry programmatically (used by tests and by
// callers that already resolved a config dir).
func SetDefault(reg *Registry) {
	defaultOnce.Do(func() {})
	defaultRegistry = reg
	defaultErr = nil
}

// ---------------------------------------------------------------------------
// DuckDB column helpers — used by the cross-stack conformance test
// ---------------------------------------------------------------------------

// AllDuckDBColumns returns every declared DuckDB column as
// {alias, column, indicator} sorted by column name.
type DeclaredColumn struct {
	Alias     string
	Column    string
	Indicator string
	View      string
}

// DeclaredColumns returns all storage columns declared across the registry.
func (r *Registry) DeclaredColumns() []DeclaredColumn {
	var out []DeclaredColumn
	for i := range r.Indicators {
		ind := &r.Indicators[i]
		if ind.Storage.Backend != BackendDuckDB {
			continue
		}
		for _, c := range ind.Storage.Columns {
			out = append(out, DeclaredColumn{
				Alias: c.Alias, Column: c.Column,
				Indicator: ind.ID, View: ind.Storage.DuckDBView,
			})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Column < out[j].Column })
	return out
}

// FormatSeriesKey builds the "ID/key" identifier used in test failure messages.
func FormatSeriesKey(indicator, key string) string {
	return strings.TrimSpace(indicator + "/" + key)
}
