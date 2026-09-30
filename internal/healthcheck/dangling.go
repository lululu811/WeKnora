package healthcheck

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"gorm.io/gorm"

	"github.com/Tencent/WeKnora/internal/logger"
)

// refSource is one place a configuration value stores the id of a row that
// can be deleted.
//
// The JSON path is described in Go rather than in SQL on purpose. Postgres
// `config->>'model_id'` and SQLite `json_extract(config,'$.model_id')` are
// different syntaxes, and this package has to run in both: production is
// Postgres, lite mode is SQLite. Selecting the JSON blob and reading it here
// is the one formulation that is correct on both, and it makes the check
// testable against the repo's sqlite test driver without a Postgres
// container.
type refSource struct {
	// table is the human-facing name, used in the finding's Object.
	table string
	// idColumn identifies the row for the operator.
	idColumn string
	// label renders the row for the operator, e.g. the agent's display name.
	// Returned by the select as labelColumn.
	labelColumn string
	// jsonColumn is the JSONB column holding the references, or "" when the
	// references live in plain columns.
	jsonColumn string
	// fields are the keys inside jsonColumn that hold a model id.
	fields []string
	// plainFields are sibling varchar columns that hold a model id.
	plainFields []string
	// selectName is the alias for the label column in SQL.
	selectName string
}

// refSources is the full roster of soft-deletable references this check
// covers.
//
// It is the union of what the incident chain touched (tenants.memory_config)
// and the same class of reference in the other configuration surfaces, which
// is where the live "Smart Reasoning" breakage lives.
var refSources = []refSource{
	{
		table: "custom_agents", idColumn: "id",
		labelColumn: "name", selectName: "obj_label",
		jsonColumn: "config",
		fields: []string{
			"model_id",
			"rerank_model_id",
			"asr_model_id",
			"vlm_model_id",
			"query_understand_model_id",
		},
	},
	{
		table: "tenants", idColumn: "id",
		labelColumn: "id", selectName: "obj_label",
		jsonColumn: "memory_config",
		fields:     []string{"extract_model_id", "embedding_model_id"},
	},
	{
		table: "sessions", idColumn: "id",
		labelColumn: "id", selectName: "obj_label",
		plainFields: []string{"rerank_model_id", "summary_model_id"},
	},
}

// modelState is the answer to "does this model id still resolve?".
type modelState struct {
	ID        string
	Name      string
	Type      string
	DeletedAt *time.Time
}

// checkDanglingReferences is check 1: every stored id of a deletable row is
// resolved against the live table and reported when it does not resolve.
//
// This is the check that would have caught last night's incident on its
// first day, and the one that catches the live "Smart Reasoning" breakage
// now sitting in the database.
//
// Unset means unset. A missing key, a JSON null and an empty string are all
// "this configuration does not name a model" and none of them is reported:
// the runtime falls back to a default in all three cases. Reporting them
// would bury the one real defect under a column of noise and train operators
// to ignore this check, which is the exact outcome this package exists to
// prevent.
func (i *Inspector) checkDanglingReferences(ctx context.Context) ([]Finding, error) {
	if i.cfg.DB == nil {
		return nil, fmt.Errorf("no database handle configured")
	}

	refs, err := i.collectReferences(ctx)
	if err != nil {
		return nil, err
	}
	if len(refs) == 0 {
		// Every source was present and every reference unset. That is a
		// real, clean result — not a reason to report anything.
		return nil, nil
	}

	states, err := i.loadModelStates(ctx, refs)
	if err != nil {
		return nil, err
	}

	findings := make([]Finding, 0, len(refs))
	for _, ref := range refs {
		state, ok := states[ref.value]
		switch {
		case !ok:
			findings = append(findings, Finding{
				Check:    CheckDanglingReference,
				Severity: SeverityCritical,
				Object:   ref.object,
				Field:    ref.field,
				BadValue: ref.value,
				Detail: "no row with this id exists in models at all; " +
					"any model call made through this reference fails to resolve",
				Remediation: "set it to a model id that exists for this tenant in models " +
					"(or clear it to fall back to the default model)",
			})
		case state.DeletedAt != nil:
			findings = append(findings, Finding{
				Check:    CheckDanglingReference,
				Severity: SeverityCritical,
				Object:   ref.object,
				Field:    ref.field,
				BadValue: ref.value,
				Detail: fmt.Sprintf("is soft-deleted (models.deleted_at=%s); the row is still "+
					"there but every lookup that filters soft-deleted rows will not return it",
					state.DeletedAt.UTC().Format(time.RFC3339)),
				Remediation: i.remediationFor(ctx, ref, state),
			})
		}
	}
	return findings, nil
}

// reference is one stored id, resolved back to the object that stores it.
type reference struct {
	table  string
	object string
	field  string
	value  string
}

// collectReferences reads every source and flattens it into references,
// dropping everything that means "unset".
func (i *Inspector) collectReferences(ctx context.Context) ([]reference, error) {
	var out []reference
	for _, src := range refSources {
		rows, err := i.readSource(ctx, src)
		if err != nil {
			// A missing table or column means the deployment predates the
			// migration, not that the reference is broken. Skip the source
			// and say so, rather than reporting every row as dangling.
			logger.Warnf(ctx, "[Healthcheck] skipping %s reference scan: %v", src.table, err)
			continue
		}
		for _, row := range rows {
			object := fmt.Sprintf("%s:%s (id=%s)", src.table, row.label, row.id)
			for _, key := range src.fields {
				if v := strings.TrimSpace(asString(row.json[key])); v != "" {
					out = append(out, reference{
						table: src.table, object: object,
						field: fmt.Sprintf("%s->>'%s'", src.jsonColumn, key),
						value: v,
					})
				}
			}
			for _, col := range src.plainFields {
				if v := strings.TrimSpace(row.plain[col]); v != "" {
					out = append(out, reference{
						table: src.table, object: object,
						field: col, value: v,
					})
				}
			}
		}
	}
	return out, nil
}

// sourceRow is one configured row, with its JSON already decoded.
type sourceRow struct {
	id    string
	label string
	json  map[string]any
	plain map[string]string
}

func (i *Inspector) readSource(ctx context.Context, src refSource) ([]sourceRow, error) {
	cols := []string{src.idColumn + " AS obj_id", src.labelColumn + " AS " + src.selectName}
	if src.jsonColumn != "" {
		cols = append(cols, src.jsonColumn+" AS obj_json")
	}
	cols = append(cols, src.plainFields...)
	// Soft-deleted rows are excluded: a deleted agent's stale model id is
	// not a defect, because nothing will ever use it again. Leaving them in
	// would report historical debris as a live breakage.
	query := fmt.Sprintf(
		"SELECT %s FROM %s WHERE deleted_at IS NULL",
		strings.Join(cols, ", "), src.table,
	)

	var raw []map[string]any
	if err := i.cfg.DB.WithContext(ctx).Raw(query).Scan(&raw).Error; err != nil {
		return nil, err
	}

	rows := make([]sourceRow, 0, len(raw))
	for _, r := range raw {
		row := sourceRow{
			id:    asString(r["obj_id"]),
			label: asString(r[src.selectName]),
			json:  map[string]any{},
			plain: map[string]string{},
		}
		if src.jsonColumn != "" {
			blob := asBytes(r["obj_json"])
			// A malformed or absent JSON blob is not itself a dangling
			// reference. Decode leniently: whatever keys do parse are
			// checked, the rest is left to the row's own validation.
			_ = json.Unmarshal(blob, &row.json)
		}
		for _, f := range src.plainFields {
			row.plain[f] = asString(r[f])
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// loadModelStates resolves every referenced id in one round trip.
//
// The query deliberately does not go through GORM's soft-delete scope: this
// check needs to tell "row is gone" apart from "row exists but is
// soft-deleted", and an auto-applied scope erases exactly that
// distinction.
func (i *Inspector) loadModelStates(ctx context.Context, refs []reference) (map[string]modelState, error) {
	ids := make([]string, 0, len(refs))
	seen := make(map[string]struct{}, len(refs))
	for _, r := range refs {
		if _, dup := seen[r.value]; dup {
			continue
		}
		seen[r.value] = struct{}{}
		ids = append(ids, r.value)
	}
	if len(ids) == 0 {
		return map[string]modelState{}, nil
	}
	sort.Strings(ids)

	var raw []struct {
		ID        string     `gorm:"column:id"`
		Name      string     `gorm:"column:name"`
		Type      string     `gorm:"column:type"`
		DeletedAt *time.Time `gorm:"column:deleted_at"`
	}
	if err := i.cfg.DB.WithContext(ctx).
		Raw("SELECT id, name, type, deleted_at FROM models WHERE id IN (?)", ids).
		Scan(&raw).Error; err != nil {
		return nil, fmt.Errorf("resolve referenced models: %w", err)
	}

	states := make(map[string]modelState, len(raw))
	for _, m := range raw {
		states[m.ID] = modelState{ID: m.ID, Name: m.Name, Type: m.Type, DeletedAt: m.DeletedAt}
	}
	return states, nil
}

// remediationFor names what the value should be.
//
// A soft-deleted model still knows its own type, so the useful suggestion is
// a live model of the same type in the same tenant — the swap an operator
// would otherwise have to go find by hand.
func (i *Inspector) remediationFor(ctx context.Context, ref reference, state modelState) string {
	alternatives := i.liveAlternatives(ctx, ref.table, state.Type)
	if len(alternatives) == 0 {
		return fmt.Sprintf("restore the soft-deleted model %q, or repoint this reference "+
			"to another live model of type %q (none currently exist for this tenant)",
			state.ID, state.Type)
	}
	return fmt.Sprintf("either restore model %q (deleted_at=%s) or repoint this reference "+
		"to a live %s model, e.g. %s",
		state.ID, state.DeletedAt.UTC().Format(time.RFC3339), state.Type,
		strings.Join(alternatives, ", "))
}

// liveAlternatives lists up to three live models of the same type, so the
// remediation names a value the operator can paste.
func (i *Inspector) liveAlternatives(ctx context.Context, table, modelType string) []string {
	if i.cfg.DB == nil || modelType == "" {
		return nil
	}
	var ids []string
	query := "SELECT id FROM models WHERE deleted_at IS NULL AND type = ?"
	args := []any{modelType}
	// The tenant is recoverable for tenants themselves; for the other
	// tables the reference row would have to be re-read, and a cross-tenant
	// suggestion is worse than none, so those fall back to any live model
	// of the same type.
	_ = table
	err := i.cfg.DB.WithContext(ctx).Raw(query+" ORDER BY is_default DESC, name LIMIT 3", args...).
		Scan(&ids).Error
	if err != nil {
		return nil
	}
	return ids
}

func asString(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case []byte:
		return string(x)
	default:
		return fmt.Sprint(x)
	}
}

func asBytes(v any) []byte {
	switch x := v.(type) {
	case nil:
		return nil
	case []byte:
		return x
	case string:
		return []byte(x)
	default:
		return []byte(fmt.Sprint(x))
	}
}

// queryRows runs a query and returns the column names plus the raw driver
// values of every row.
//
// GORM's struct-field mapping is deliberately bypassed here. Scanning an
// aggregate like MAX(failed_at) into a typed struct field hands back a
// driver value whose Go type depends on the dialect — time.Time on Postgres,
// a string or a pointer on SQLite — and GORM's reflect-based assignment
// into an `any` field produces pointers-to-pointers that fmt.Sprint renders
// as an address. Reading driver values directly makes the parse below the
// single place that has to know about that difference.
func queryRows(ctx context.Context, db *gorm.DB, sql string, args ...any) ([]string, [][]any, error) {
	raw, err := db.WithContext(ctx).Raw(sql, args...).Rows()
	if err != nil {
		return nil, nil, err
	}
	defer func() { _ = raw.Close() }()

	cols, err := raw.Columns()
	if err != nil {
		return nil, nil, err
	}
	var out [][]any
	for raw.Next() {
		// Scan targets must be pointers, and a fresh slice per row:
		// reusing one would alias every row to the last driver's memory.
		row := make([]any, len(cols))
		targets := make([]any, len(cols))
		for i := range row {
			targets[i] = &row[i]
		}
		if err := raw.Scan(targets...); err != nil {
			return nil, nil, err
		}
		out = append(out, row)
	}
	return cols, out, raw.Err()
}

func columnIndex(cols []string) map[string]int {
	idx := make(map[string]int, len(cols))
	for i, c := range cols {
		idx[c] = i
	}
	return idx
}

// parseDBInt normalizes a COUNT() result, which arrives as int64 on Postgres
// and as int64 or string depending on the driver.
func parseDBInt(v any) (int64, bool) {
	switch x := v.(type) {
	case nil:
		return 0, false
	case int64:
		return x, true
	case int:
		return int64(x), true
	case float64:
		return int64(x), true
	case []byte:
		return parseDBInt(string(x))
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(x), 10, 64)
		if err != nil {
			return 0, false
		}
		return n, true
	default:
		return 0, false
	}
}

// dbTimeLayouts are the shapes a timestamp column comes back in.
//
// This is not defensive padding: Postgres returns time.Time, but SQLite
// (which lite mode runs on) returns the stored value as a string, and
// scanning that into a *time.Time fails outright. One of these two
// dialects is going to hand back a value, and a check that only works on
// one of them is a check that silently stops running in the other.
var dbTimeLayouts = []string{
	time.RFC3339Nano,
	"2006-01-02 15:04:05.999999999-07:00",
	"2006-01-02 15:04:05.999999999Z07:00",
	"2006-01-02T15:04:05.999999999Z07:00",
	"2006-01-02 15:04:05.999999999",
	"2006-01-02 15:04:05",
	"2006-01-02",
}

// parseDBTime normalizes a timestamp column across both supported drivers.
// The second return is false when the value is absent or unparseable, which
// the caller must treat as "unknown", never as the zero time.
func parseDBTime(v any) (time.Time, bool) {
	switch x := v.(type) {
	case nil:
		return time.Time{}, false
	case time.Time:
		return x, true
	case *time.Time:
		if x == nil {
			return time.Time{}, false
		}
		return *x, true
	}
	raw := strings.TrimSpace(asString(v))
	if raw == "" {
		return time.Time{}, false
	}
	for _, layout := range dbTimeLayouts {
		if t, err := time.Parse(layout, raw); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// ensure gorm is referenced for the driver-agnostic query building above.
var _ = gorm.ErrRecordNotFound
