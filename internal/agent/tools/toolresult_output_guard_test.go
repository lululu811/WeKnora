package tools

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

// A ToolResult that reports Success:true with an empty Output reaches the model
// as an empty string: the success branch of modelcontext/registry.go reads only
// result.Output, and observe.go feeds that same field back through
// ModelToolResultForTool. The call fails silently — no error surfaces, the model
// just sees nothing.
//
// hithink.finance.analysis.trend shipped exactly that way: it filled only Data
// (which exists purely so Langfuse can log data_keys) and never Output. The
// model got an empty string while the tool reported success, and the tool's own
// data-quality block — the whole reason the tool exists — never arrived.
//
// This walks the tool sources and fails on any success literal that leaves
// Output unset, so the next one cannot ship quietly.
// Shared with checkFunc, which walks per-function. A single test run owns these;
// they are reset at the top of the test rather than being package state that
// leaks between runs.
var (
	guardViolations []string
	guardChecked    int
)

func TestToolResultSuccessAlwaysCarriesOutput(t *testing.T) {
	root := "."
	fset := token.NewFileSet()
	guardViolations = nil
	guardChecked = 0

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			// Skip vendor-ish and testdata trees; the contract is about shipped
			// tool sources, not fixtures.
			if name := info.Name(); name == "testdata" || strings.HasPrefix(name, ".") && name != "." {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}

		file, parseErr := parser.ParseFile(fset, path, nil, 0)
		if parseErr != nil {
			return nil // not our problem here; the compiler will report it
		}

		ast.Inspect(file, func(n ast.Node) bool {
			fn, ok := n.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				return true
			}
			checkFunc(t, fset, fn)
			return false
		})
		return nil
	})
	require.NoError(t, err)

	require.Positive(t, guardChecked, "no successful ToolResult literals found — the walk is broken, not the code")
	require.Empty(t, guardViolations,
		"these ToolResult literals report Success:true but never set Output, so the model receives an empty result: %v",
		guardViolations)
}

// checkFunc reports success-literals inside one function that neither set Output
// in the literal nor assign to it afterwards. The second form is real and
// idiomatic here — read_web_page.go builds the literal, then fills
// `result.Output` with a formatted block — so a literal-only check produces
// false positives that train people to ignore the guard.
func checkFunc(t *testing.T, fset *token.FileSet, fn *ast.FuncDecl) {
	t.Helper()

	type pending struct {
		pos  string
		name string // variable the literal was assigned to, if any
	}
	var pendingSuccess []pending
	assigned := map[string]bool{}

	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.AssignStmt:
			// lhs.Output = ... marks that variable as carrying output.
			for _, lhs := range node.Lhs {
				sel, ok := lhs.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "Output" {
					continue
				}
				if id, ok := sel.X.(*ast.Ident); ok {
					assigned[id.Name] = true
				}
			}
		case *ast.CompositeLit:
			sel, ok := node.Type.(*ast.SelectorExpr)
			if !ok || sel.Sel.Name != "ToolResult" {
				return true
			}
			var successTrue, hasOutput bool
			for _, elt := range node.Elts {
				kv, ok := elt.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				ident, ok := kv.Key.(*ast.Ident)
				if !ok {
					continue
				}
				switch ident.Name {
				case "Output":
					hasOutput = true
				case "Success":
					if id, ok := kv.Value.(*ast.Ident); ok && id.Name == "true" {
						successTrue = true
					}
				}
			}
			if !successTrue || hasOutput {
				return true
			}
			// Look one level up for `x := &types.ToolResult{...}`.
			name := ""
			if assign, ok := enclosingAssign(fn.Body, node); ok {
				if id, ok := assign.Lhs[0].(*ast.Ident); ok {
					name = id.Name
				}
			}
			pendingSuccess = append(pendingSuccess, pending{
				pos:  fset.Position(node.Pos()).String(),
				name: name,
			})
		}
		return true
	})

	for _, p := range pendingSuccess {
		guardChecked++
		if p.name != "" && assigned[p.name] {
			continue
		}
		guardViolations = append(guardViolations, p.pos)
	}
}

// enclosingAssign returns the assignment statement whose RHS is the given node.
// The RHS is normally `&types.ToolResult{...}`, so the address-of wrapper has to
// be unwrapped before comparing.
func enclosingAssign(body *ast.BlockStmt, target ast.Node) (*ast.AssignStmt, bool) {
	unwrap := func(n ast.Node) ast.Node {
		if unary, ok := n.(*ast.UnaryExpr); ok {
			return unary.X
		}
		return n
	}
	var found *ast.AssignStmt
	var ok bool
	ast.Inspect(body, func(n ast.Node) bool {
		assign, isAssign := n.(*ast.AssignStmt)
		if !isAssign || found != nil {
			return found == nil
		}
		if len(assign.Rhs) == 1 && unwrap(assign.Rhs[0]) == target {
			found, ok = assign, true
			return false
		}
		return true
	})
	return found, ok
}
