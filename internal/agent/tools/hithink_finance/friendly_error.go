package hithink_finance

import (
	"fmt"
	"regexp"
	"strings"
)

// FriendlyQueryError 把 DuckDB 的原始报错翻译成模型和用户都能据以行动的话。
//
// 为什么要做这一层：工具的 SQL 是硬编码的，而 schema 活在用户机器上的
// *.duckdb 里，两者之间没有编译期联系。一次排查（2026-09-27）发现 11 处硬编码
// SQL 有 6 处从未对上过真实 schema，用户看到的是这样的东西：
//
//	查询失败 (HTTP 400): {"success":false,"error":"查询失败: Binder Error:
//	Referenced column \"date\" not found in FROM clause! candidate bindings:
//	\"captured_at\", \"name\", \"ticker\", \"snapshot_date\" ..."
//
// 这段东西同时劝退用户、也让模型浪费轮次去猜（实测模型会连试 4 次，然后才
// 改走 query.sql 绕过去）。这里把「字段不存在」这类**可归因于工具自身**的
// 错误翻译成明确的降级指引；其余错误原样透传，不做过度包装。
//
// 注意：这层**不能替代** schema 契约测试（schema_contract_test.go）。它是
// 兜底，不是防线。
func FriendlyQueryError(err error, toolName string) string {
	if err == nil {
		return ""
	}
	raw := err.Error()

	// DuckDB 的 Binder Error：字段或表不存在。这几乎总是本工具的 SQL 落后于
	// schema，而不是用户参数错。
	if isBinderError(raw) {
		var b strings.Builder
		fmt.Fprintf(&b, "工具 %s 查询失败：底层数据源的字段与本工具的 SQL 不匹配。", toolName)
		b.WriteString("\n这不是你的参数写错了，不要重试这个工具。")
		b.WriteString("\n可行做法：改用 hithink.finance.query.sql 直接查——它的 schema 提示里列出了各库的真实视图与字段名。")
		if hint := candidateHint(raw); hint != "" {
			fmt.Fprintf(&b, "\n底层提示：%s", hint)
		}
		b.WriteString("\n原始错误：" + truncateForUser(raw, 400))
		return b.String()
	}

	// 同步窗口：已有专门的中文提示，不要在这里二次包装。
	if strings.Contains(raw, "数据同步") || strings.Contains(raw, "同步中") {
		return raw
	}

	return raw
}

var binderErrRe = regexp.MustCompile(`(?i)Binder Error|not found in FROM clause|Candidate bindings|referenced column .* not found`)

func isBinderError(msg string) bool {
	return binderErrRe.MatchString(msg)
}

// candidateHint 从 DuckDB 报错里挑出 "candidate bindings" 那段——它是 DuckDB
// 主动给出的正确列名清单，对用户和模型都很有用，不该被截断掉。
func candidateHint(msg string) string {
	idx := strings.Index(strings.ToLower(msg), "candidate bindings")
	if idx < 0 {
		return ""
	}
	rest := msg[idx:]
	// candidate bindings 后面通常紧跟一行 LINE 1: SELECT ...，在这里截断。
	if line := strings.Index(rest, "LINE 1:"); line > 0 {
		rest = rest[:line]
	}
	return truncateForUser(strings.TrimSpace(rest), 300)
}

func truncateForUser(s string, max int) string {
	s = strings.TrimSpace(s)
	runes := []rune(s)
	if len(runes) <= max {
		return s
	}
	return string(runes[:max]) + "…（已截断）"
}
