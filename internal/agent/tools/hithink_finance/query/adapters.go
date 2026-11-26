package query

import (
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance/spec"
	"github.com/Tencent/WeKnora/internal/types"
)

// SQLQueryTool 执行只读 SQL。
//
// 声明在 spec 包里——连那份给模型看的 schema 目录也在那边（目录是 go:generate
// 从 testdata/schema.json 生成的静态字符串，见 spec/sql_query_catalog.go）。
// 这里只剩一个 Name()。
type SQLQueryTool struct{ spec.Tool }

// NewSQLQueryTool 创建自由 SQL 工具。
func NewSQLQueryTool(config *hithink_finance.Config) *SQLQueryTool {
	return &SQLQueryTool{spec.Tool{Config: config, Spec: spec.QuerySQL}}
}

// Name 返回工具名。
func (t *SQLQueryTool) Name() string { return "hithink.finance.query.sql" }

// Ensure tool implements types.Tool
var _ types.Tool = (*SQLQueryTool)(nil)
