package agent

import (
	"embed"
	"io/fs"
	"regexp"
	"strings"
	"sync"
)

// toolSourceTree embeds the whole internal/agent/tools source tree.
//
// Why this file lives in package `agent` rather than in `tools`: go:embed
// can only reach into the embedding package's own directory and below, and a
// file in `internal/agent/tools` cannot see its own package root. Most core
// tools — search_knowledge, read_document, query_knowledge_graph,
// list_documents, data_analysis, database_query, browserskill — are declared
// as files in the `tools` package *root*, while the family tools
// (hithink.finance.*, zettaranc.*) sit two or three
// directories down. A `*/*.go` glob therefore sees neither, and the
// resulting "registered tool set" was missing the majority of the tools,
// which turned the allowlist check into 25 false positives on production
// agents. Naming a directory embeds its whole subtree, so one pattern covers
// every depth and both the root and the subpackages.
//
// The `all:` prefix is deliberately NOT used: without it, go:embed skips
// files whose names begin with '.' or '_', which keeps scratch state such as
// zettaranc/.omc/ out of the binary.
//
// The cost is roughly a megabyte of embedded source. That is the same
// trade hithink_finance/discover.go already makes for the tool subpackages,
// and it buys the property that matters: the set cannot drift, because
// go:embed re-collects on every build. A hand-maintained or generated list
// would drift, and a list that has drifted reports tools that do not exist
// and hides tools that do — which is the exact failure this package exists
// to catch.
//
//go:embed tools
var toolSourceTree embed.FS

// toolNameRe captures the whole return expression of a tool's Name() method.
//
// The expression is captured rather than a quoted literal because the two
// families of tool declare their name differently: the family tools
// (hithink.finance.*, zettaranc.*) write `return "zettaranc.screener"`,
// while the core tools write `return ToolSearchKnowledge`. Matching only the
// literal form found the family tools and missed every core tool, which is
// the same class of near-miss that produced 25 false positives before it.
var toolNameRe = regexp.MustCompile(
	`func\s*\(\s*\w+\s+\*?\w+\s*\)\s*Name\(\)\s*string\s*\{\s*return\s+([^}\n]+)`)

// constStringRe captures `Name = "value"` and `Name string = "value"`, the
// two shapes the tool-name constants are declared in.
var constStringRe = regexp.MustCompile(`\b([A-Za-z_]\w*)\s*(?:string\s+)?=\s*"([\w.]*)"`)

// baseToolNameRe captures the `name:` field of a BaseTool literal.
//
// Most core tools (search_knowledge, read_document, query_knowledge_graph,
// list_documents, database_query, the sandbox tools, ...) do not declare a
// Name() method at all: they embed BaseTool, which supplies Name() from an
// unexported field set at construction. Scanning only Name() methods finds
// the family tools and none of the core ones, so a pattern for the
// construction site is required as well.
var baseToolNameRe = regexp.MustCompile(`\bname:\s*([A-Za-z_]\w*)\s*,`)

// newBaseToolRe captures the name argument of NewBaseTool(...), the
// constructor form of the same thing.
var newBaseToolRe = regexp.MustCompile(`NewBaseTool\(\s*([A-Za-z_]\w*)\s*,`)

var (
	registeredNamesOnce sync.Once
	registeredNames     map[string]bool
)

// RegisteredToolNames returns every tool name the binary can register.
//
// The result is cached — the embedded sources cannot change without a
// rebuild — and handed back as a copy so a caller mutating it cannot poison
// every later caller in the process.
//
// Names come from source rather than from a live ToolRegistry because the
// registry is per-request (agent_service.go calls tools.NewToolRegistry per
// turn) and its contents depend on KB capabilities, MCP servers, sandbox and
// the memory switches. At startup there is no registry to ask, and the
// names that *can* be registered are a fixed property of the source.
func RegisteredToolNames() map[string]bool {
	registeredNamesOnce.Do(func() {
		registeredNames = scanRegisteredToolNames()
	})
	out := make(map[string]bool, len(registeredNames))
	for name, ok := range registeredNames {
		out[name] = ok
	}
	return out
}

func scanRegisteredToolNames() map[string]bool {
	sources := readToolSources()

	// Constants first: a Name() method that returns a named constant can
	// only be resolved once the constant's value is known.
	consts := make(map[string]string, len(sources))
	for _, src := range sources {
		for _, m := range constStringRe.FindAllStringSubmatch(src.text, -1) {
			consts[m[1]] = m[2]
		}
	}

	out := make(map[string]bool)
	for _, src := range sources {
		for _, m := range toolNameRe.FindAllStringSubmatch(src.text, -1) {
			if name, ok := resolveToolName(strings.TrimSpace(m[1]), consts); ok {
				out[name] = true
			}
		}
		for _, re := range []*regexp.Regexp{baseToolNameRe, newBaseToolRe} {
			for _, m := range re.FindAllStringSubmatch(src.text, -1) {
				if name, ok := resolveToolName(m[1], consts); ok {
					out[name] = true
				}
			}
		}
	}
	return out
}

type toolSource struct {
	path string
	text string
}

func readToolSources() []toolSource {
	var out []toolSource
	_ = fs.WalkDir(toolSourceTree, "tools", func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") ||
			strings.HasSuffix(path, "_test.go") {
			// An unreadable embedded file means one fewer candidate name,
			// never a wrong verdict, so the walk continues.
			return nil //nolint:nilerr
		}
		src, readErr := toolSourceTree.ReadFile(path)
		if readErr != nil {
			return nil
		}
		out = append(out, toolSource{path: path, text: string(src)})
		return nil
	})
	return out
}

// resolveToolName turns a Name() return expression into a tool name, whether
// it is a literal or a reference to a declared constant.
//
// An expression that is neither is skipped rather than guessed at. Guessing
// here would put a name in the set that no tool answers to, and a name in
// the set that nothing provides is exactly how an allowlist typo stops being
// reported.
func resolveToolName(expr string, consts map[string]string) (string, bool) {
	if strings.HasPrefix(expr, `"`) && strings.HasSuffix(expr, `"`) && len(expr) >= 2 {
		return expr[1 : len(expr)-1], true
	}
	if name, ok := consts[expr]; ok && name != "" {
		return name, true
	}
	return "", false
}
