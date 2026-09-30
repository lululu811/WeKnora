package hithink_finance

import (
	"io/fs"
	"sort"
	"strings"
)

// DeadColumn is an indicator column that a hardcoded tool SQL selects but
// nothing ever reads.
type DeadColumn struct {
	// Column is the indicator column name.
	Column string
	// Tools is every embedded source file whose SQL selects the column.
	Tools []string
}

// DeadColumns returns the indicator columns that tool SQL queries but no
// code consumes.
//
// It is deliberately built on the machinery discover.go already ships
// rather than a second implementation of it:
//
//   - toolSourceFS, the `//go:embed */*.go` of the tool subpackages, so the
//     sources are the same text already compiled into the binary;
//   - indicatorColumnsInSource, which extracts the explicit SELECT list of
//     every SQL literal reading v_indicators_daily and applies the same
//     "is this really SQL and not mispaired Go source" guard.
//
// Writing a parallel scanner here would be a second thing to keep in sync
// with the tool sources, and a scanner that drifts reports dead columns
// that are not dead — the same class of lie discover.go's doc comment
// refuses to tell.
//
// "Read" is defined as the complement of discover.go's "consumed" test.
// discover.go decides an indicator is consumed only when a SELECT list
// names it, explicitly rejecting WHERE mentions as "someone is filtering
// on it" rather than "someone is using it". So a column is dead when it
// appears ONLY as a bare element of a SELECT list and nowhere else: not in
// a WHERE/GROUP/ORDER clause, not as a function argument, and not named
// anywhere in the Go sources around the SQL.
func DeadColumns() []DeadColumn {
	type entry struct {
		tools map[string]bool
	}
	dead := make(map[string]*entry)
	live := make(map[string]bool)

	walkToolSources(func(path, src string) {
		spans := sqlLiteralSpans(src)
		sqls := indicatorSQLLiterals(src)
		if len(sqls) == 0 {
			return
		}
		// What the surrounding Go code can possibly reference: the
		// identifiers outside the SQL literals.
		goCode := identifierSet(stripSpans(src, spans))
		// What the SQL itself consumes: function arguments and the text
		// after FROM.
		inSQL := readColumnsInSource(src)

		for _, sql := range sqls {
			for out, source := range selectOutputs(sql) {
				// A column is read if the surrounding Go code references
				// the name this query actually produces, or if the SQL
				// itself uses the source column.
				if goCode[out] || inSQL[source] || inSQL[out] {
					live[out] = true
					continue
				}
				e, ok := dead[out]
				if !ok {
					e = &entry{tools: map[string]bool{}}
					dead[out] = e
				}
				e.tools[path] = true
			}
		}
	})

	out := make([]DeadColumn, 0, len(dead))
	for col, e := range dead {
		if live[col] {
			continue
		}
		tools := make([]string, 0, len(e.tools))
		for t := range e.tools {
			tools = append(tools, t)
		}
		sort.Strings(tools)
		out = append(out, DeadColumn{Column: col, Tools: tools})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Column < out[b].Column })
	return out
}

// selectOutputs maps the name each SELECT element produces to the underlying
// column.
//
// The distinction is the whole point of this check. These tools alias almost
// everything — `volume_obv AS obv` — and the Go code then reads the alias
// out of the returned row map (`r["obv"]`). Judging the alias-less source
// column would report all 42 selected columns as dead when every one of them
// is in active use, which is a false alarm on every finding.
//
// Unaliased function calls (`abs(macd_hist)`) are keyed by their argument,
// matching the Go that indexes them by column name.
func selectOutputs(sql string) map[string]string {
	out := map[string]string{}
	sel := sqlSelectListRe.FindStringSubmatch(sql)
	if sel == nil {
		return out
	}
	for _, item := range splitTopLevel(sel[1]) {
		expr, alias := splitAlias(item)
		if alias != "" {
			out[alias] = columnOf(expr)
			continue
		}
		if inner := sqlFuncArgsRe.FindString(item); inner != "" {
			if col := columnOf(inner); col != "" {
				out[col] = col
				continue
			}
		}
		if col := columnOf(item); col != "" {
			out[col] = col
		}
	}
	return out
}

// splitAlias separates a top-level `x AS y` select element.
//
// Top-level matters: in `CAST(x AS VARCHAR) AS bar` the first AS belongs to
// the cast, and treating VARCHAR as the output name would report a SQL type
// as an unread indicator column.
func splitAlias(item string) (expr, alias string) {
	depth := 0
	for i := 0; i+4 <= len(item); i++ {
		switch item[i] {
		case '(':
			depth++
		case ')':
			if depth > 0 {
				depth--
			}
		}
		if depth != 0 {
			continue
		}
		if item[i] != 'A' || item[i+1] != 'S' {
			continue
		}
		if i > 0 && !isSpace(item[i-1]) {
			continue
		}
		if i+2 >= len(item) || !isSpace(item[i+2]) {
			continue
		}
		rest := strings.TrimSpace(item[i+2:])
		if !sqlIdentRe.MatchString(rest) {
			continue
		}
		return strings.TrimSpace(item[:i]), rest
	}
	return item, ""
}

func isSpace(b byte) bool { return b == ' ' || b == '\t' || b == '\n' || b == '\r' }

// columnOf reduces one SELECT element to the bare column name it reads,
// stripping any table alias, cast target and function wrapper.
//
// It descends into a function's arguments rather than returning the
// function's own name: the column behind `abs(macd_hist)` is macd_hist, and
// "abs" is not a column anything can select.
func columnOf(item string) string {
	cleaned := sqlAliasAsRe.ReplaceAllString(item, " ")
	if inner := sqlFuncArgsRe.FindString(cleaned); inner != "" {
		if col := firstColumnIdent(inner); col != "" {
			return col
		}
	}
	cleaned = sqlFuncArgsRe.ReplaceAllString(cleaned, " ")
	cleaned = sqlAliasRe.ReplaceAllString(cleaned, "$2")
	return firstColumnIdent(cleaned)
}

func firstColumnIdent(text string) string {
	for _, ident := range sqlIdentRe.FindAllString(text, -1) {
		if isIgnorableIdent(ident) {
			continue
		}
		return ident
	}
	return ""
}

// splitTopLevel splits a select list on commas that are not inside
// parentheses, so `abs(a), b` is two elements and not one.
func splitTopLevel(list string) []string {
	var out []string
	depth, start := 0, 0
	for i, r := range list {
		switch r {
		case '(':
			depth++
		case ')':
			if depth > 0 {
				depth--
			}
		case ',':
			if depth == 0 {
				if item := strings.TrimSpace(list[start:i]); item != "" {
					out = append(out, item)
				}
				start = i + 1
			}
		}
	}
	if item := strings.TrimSpace(list[start:]); item != "" {
		out = append(out, item)
	}
	return out
}

func identifierSet(text string) map[string]bool {
	out := map[string]bool{}
	for _, ident := range sqlIdentRe.FindAllString(text, -1) {
		if isIgnorableIdent(ident) {
			continue
		}
		out[ident] = true
	}
	return out
}

// isIgnorableIdent filters SQL keywords and types out of a column set.
//
// It is discover.go's sqlNonColumn list with one deliberate exception:
// `date`. That list drops `date` because of `CAST(x AS DATE)`, but
// v_indicators_daily.date is a real column and the primary time key — every
// one of these queries does `ORDER BY date`. Filtering it would make the
// check report the one column it can prove is read as dead.
func isIgnorableIdent(ident string) bool {
	lower := strings.ToLower(ident)
	if lower == "date" {
		return false
	}
	return sqlNonColumn[lower]
}

// readColumnsInSource returns every column the source consumes somewhere
// other than as a bare element of a SELECT list.
//
// Three places count as a read:
//
//   - inside a function argument, e.g. abs(macd_hist) or
//     moving_average(close, 5): the value is computed on;
//   - anywhere after the first FROM of a statement, i.e. WHERE / ON /
//     GROUP BY / ORDER BY: the value filters, groups or orders rows;
//   - anywhere in the Go code outside the SQL literals.
//
// The third case is the important one and the reason this is not simply "not
// in a select list". QueryDuckDBParams hands rows back as map[string]any, so
// these tools consume their columns by indexing the returned map in Go —
// `row["macd_hist"]`. Judging only by SQL would call every one of them dead,
// which is exactly the false alarm that trains people to ignore a checker.
func readColumnsInSource(src string) map[string]bool {
	out := map[string]bool{}
	add := func(text string) {
		cleaned := sqlAliasAsRe.ReplaceAllString(text, " ")
		cleaned = sqlAliasRe.ReplaceAllString(cleaned, "$2")
		for _, ident := range sqlIdentRe.FindAllString(cleaned, -1) {
			if isIgnorableIdent(ident) {
				continue
			}
			out[ident] = true
		}
	}

	spans := sqlLiteralSpans(src)
	for _, sql := range indicatorSQLLiterals(src) {
		// Function arguments anywhere in the statement.
		for _, m := range sqlFuncArgsRe.FindAllStringSubmatch(sql, -1) {
			add(m[0])
		}
		// Everything from the first FROM onwards.
		if loc := sqlObjectRe.FindStringIndex(sql); loc != nil {
			add(sql[loc[0]:])
		}
	}
	// The Go code, i.e. the source with the SQL literals removed so their
	// select lists cannot be mistaken for consumption.
	add(stripSpans(src, spans))
	return out
}

// sqlLiteralSpans returns the [start,end) byte ranges of every backtick
// literal in src that contains a standalone SELECT.
func sqlLiteralSpans(src string) [][2]int {
	var out [][2]int
	for _, loc := range toolSQLLiteralRe.FindAllStringIndex(src, -1) {
		out = append(out, [2]int{loc[0], loc[1]})
	}
	return out
}

func stripSpans(src string, spans [][2]int) string {
	if len(spans) == 0 {
		return src
	}
	var b strings.Builder
	prev := 0
	for _, s := range spans {
		if s[0] > prev {
			b.WriteString(src[prev:s[0]])
		}
		if s[1] > prev {
			prev = s[1]
		}
	}
	if prev < len(src) {
		b.WriteString(src[prev:])
	}
	return b.String()
}

// walkToolSources walks the same embedded source FS discover.go uses and
// calls fn for every non-test .go file in the tool subpackages.
func walkToolSources(fn func(path, src string)) {
	_ = fs.WalkDir(toolSourceFS, ".", func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") ||
			strings.HasSuffix(path, "_test.go") {
			return nil //nolint:nilerr // an unreadable embedded file means one
			// fewer candidate column, never a wrong verdict.
		}
		src, readErr := toolSourceFS.ReadFile(path)
		if readErr != nil {
			return nil
		}
		fn(path, string(src))
		return nil
	})
}

// indicatorSQLLiterals returns the SQL literals in src that read
// v_indicators_daily, applying exactly the guards discover.go applies: a
// backtick literal containing a standalone SELECT keyword, rejected when it
// looks like mispaired Go source or when it does not actually read the view.
func indicatorSQLLiterals(src string) []string {
	var out []string
	for _, m := range toolSQLLiteralRe.FindAllStringSubmatch(src, -1) {
		sql := m[1]
		if looksLikeGoCode(sql) || !containsObject(sql, indicatorView) {
			continue
		}
		if sqlSelectListRe.FindStringSubmatch(sql) == nil {
			continue
		}
		out = append(out, sql)
	}
	return out
}

// readColumnsOutsideSelectList is superseded by readColumnsInSource.
// somewhere other than as a bare element of its outermost SELECT list.
//
// Two places count as a read, and they are the two that mean a value is
// actually consumed rather than merely carried:
//
//   - inside a function argument, e.g. abs(macd_hist) or
//     moving_average(close, 5): the value is computed on;
//   - anywhere after the first FROM, i.e. WHERE/ON/GROUP BY/ORDER BY: the
//     value filters, groups or orders rows.
//
// A bare column in the outermost select list is neither. It is transported
// and then dropped.
//
// The outermost select list is deliberately *not* itself treated as a read.
// Doing so — which an earlier version did, by re-running the select-list
// regex to catch subqueries — marks every column as read and reports nothing
// at all, which is the exact failure this check exists to prevent.
func readColumnsOutsideSelectList(sql string) map[string]bool {
	out := map[string]bool{}
	add := func(text string) {
		cleaned := sqlAliasAsRe.ReplaceAllString(text, " ")
		cleaned = sqlAliasRe.ReplaceAllString(cleaned, "$2")
		for _, ident := range sqlIdentRe.FindAllString(cleaned, -1) {
			if sqlNonColumn[strings.ToLower(ident)] {
				continue
			}
			out[ident] = true
		}
	}

	// Function arguments anywhere in the statement.
	for _, m := range sqlFuncArgsRe.FindAllStringSubmatch(sql, -1) {
		add(m[0])
	}
	// Everything from the first FROM onwards.
	if loc := sqlObjectRe.FindStringIndex(sql); loc != nil {
		add(sql[loc[0]:])
	}
	return out
}
