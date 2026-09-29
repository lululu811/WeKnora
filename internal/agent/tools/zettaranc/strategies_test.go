package zettaranc

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// 策略清单曾在本文件和 python-service/main.py 的 STRATEGY_RULES 里各存一份。
// 两边各改各的：加策略只改 Python 侧，模型按 Go 的 enum 拼不出那个值，
// 于是新策略存在但永远调不到。
//
// 这个测试把两份清单对齐。它不试图让两边共用一份代码（跨语言做不到），
// 只是让不一致**在 CI 里变红**，并把差集直接打印出来。
func TestScreenerStrategiesMatchPythonDict(t *testing.T) {
	mainPy := findMainPy(t)

	raw, err := os.ReadFile(mainPy)
	if err != nil {
		t.Fatalf("读不到 %s：%v", mainPy, err)
	}
	pythonKeys := parseStrategyKeys(t, string(raw))

	goKeys := ScreenerStrategies()

	var missingInGo, missingInPython []string
	goSet := make(map[string]bool, len(goKeys))
	for _, k := range goKeys {
		goSet[k] = true
	}
	pySet := make(map[string]bool, len(pythonKeys))
	for _, k := range pythonKeys {
		pySet[k] = true
	}
	for _, k := range pythonKeys {
		if !goSet[k] {
			missingInGo = append(missingInGo, k)
		}
	}
	for _, k := range goKeys {
		if !pySet[k] {
			missingInPython = append(missingInPython, k)
		}
	}

	sort.Strings(missingInGo)
	sort.Strings(missingInPython)
	if len(missingInGo) > 0 {
		t.Errorf("python-service 的 STRATEGY_RULES 里有这些策略，ScreenerStrategies() 没列：%v\n"+
			"模型按 enum 拼不出这些值，新策略存在但调不到", missingInGo)
	}
	if len(missingInPython) > 0 {
		t.Errorf("ScreenerStrategies() 列了这些策略，python-service 里没有：%v\n"+
			"模型会拿到「策略不存在」", missingInPython)
	}
}

func TestScreenerStrategiesHasNoDuplicates(t *testing.T) {
	seen := map[string]bool{}
	for _, k := range ScreenerStrategies() {
		if seen[k] {
			t.Errorf("策略 %q 重复出现", k)
		}
		seen[k] = true
	}
}

// findMainPy 定位 python-service/main.py。测试从本包出发向上找仓库根。
func findMainPy(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd: %v", err)
	}
	for i := 0; i < 8; i++ {
		p := filepath.Join(dir, "python-service", "main.py")
		if _, err := os.Stat(p); err == nil {
			return p
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("找不到 python-service/main.py")
	return ""
}

// strategyKeyRe 匹配 STRATEGY_RULES 字典的顶层 key：
// 行首 4 个空格 + 引号 + 标识符 + 引号 + 冒号。
var strategyKeyRe = regexp.MustCompile(`(?m)^    "([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*\{`)

// parseStrategyKeys 从 main.py 源码里抽出策略名。
//
// 用源码文本而不是导入 Python：这是 Go 测试，跑不起 python-service 的依赖
// （duckdb 之类），CI 的 Go 阶段也未必装了 uv。正则只匹配 STRATEGY_RULES 那个
// 4 空格缩进的字典块，靠缩进把它和文件里其它同形字典分开。
func parseStrategyKeys(t *testing.T, src string) []string {
	t.Helper()
	start := strings.Index(src, "STRATEGY_RULES")
	if start < 0 {
		t.Fatal("main.py 里找不到 STRATEGY_RULES")
	}
	rest := src[start:]
	// 只在字典体内扫描：找到第一个 `}` 就停，避免把后面的路由字典也算进来。
	end := strings.Index(rest, "\n}")
	if end < 0 {
		end = len(rest)
	}
	body := rest[:end]

	var out []string
	for _, m := range strategyKeyRe.FindAllStringSubmatch(body, -1) {
		out = append(out, m[1])
	}
	if len(out) == 0 {
		t.Fatalf("从 STRATEGY_RULES 里没解析出任何策略名，正则可能已失效：\n%s", head(body, 400))
	}
	return out
}

func head(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
