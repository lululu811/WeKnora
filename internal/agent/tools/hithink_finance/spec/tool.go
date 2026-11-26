package spec

import (
	"context"
	"encoding/json"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// Fetch 是「取数 → Go 侧计算 → 输出」这一族工具的取数钩子。
//
// values 是按声明解析好的参数（缺省值、钳制、判空都已处理）。取数可以是多条
// 查询——analysis 的四个技术分析工具就要查两个 DuckDB 再在 Go 里合并。
type Fetch func(ctx context.Context, cfg *hithink_finance.Config, values map[string]any) (any, error)

// Compose 是 Go 侧计算的钩子：把取回来的数据变成最终结果。
//
// 这一族工具的结果不是行，是算出来的（道氏理论、威科夫、形态识别），
// 表达不成数据，于是它以钩子的形式留在工具那边。进声明的是这个工具的
// **身份与契约**：名字、说明、参数、窗口。
//
// values 是按声明解析好的参数——工具的输出里要回显代码，就是从这儿拿。
type Compose func(rows any, values map[string]any) (*types.ToolResult, error)

// Tool 是声明式工具的适配器基类。
//
// 家族包里的工具类型内嵌它，只需要再补一个 Name()——而 Name() 必须返回字面量：
// zettaranc 白名单测试用正则从源码里取工具名，认不出从声明里查表。spec 包的
// 契约测试保证这份字面量与声明里的 Name 一致。
type Tool struct {
	// Config 是 DuckDB 连接配置。
	Config *hithink_finance.Config
	// Spec 是这个工具的声明。
	Spec Spec
	// Fetch 与 Compose 是「取数 → Go 侧计算 → 输出」这一族工具用的钩子。
	//
	// 两个都是 nil 时，工具走"查一批行、装进信封"那条路（前面几批都是）。
	// Fetch 非 nil 时走计算那条路：执行器负责同步窗口与参数解析，取数与计算
	// 由工具自己提供——因为它们是这一族工具的**价值本身**，不是样板。
	Fetch   Fetch
	Compose Compose
}

// Description 返回给模型看的说明。
func (t *Tool) Description() string { return Description(t.Spec) }

// Parameters 返回给模型看的参数 schema。
func (t *Tool) Parameters() json.RawMessage { return Parameters(t.Spec) }

// Execute 按声明执行这个工具。
//
// 这一族（analysis 的四个技术分析工具）取数与计算都在工具那边，于是执行器
// 只负责同步窗口与参数解析；结果由工具的 Compose 组装。
func (t *Tool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if t.Spec.Composed {
		return executeComposed(ctx, t.Config, t.Spec, t.Fetch, t.Compose, args)
	}
	return Execute(ctx, t.Config, t.Spec, args)
}
