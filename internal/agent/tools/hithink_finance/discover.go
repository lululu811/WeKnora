package hithink_finance

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/types"
)

// DiscoverTool is the discovery tool for hithink finance tools.
// It helps agents discover available sub-tools by prefix.
type DiscoverTool struct {
	registry *tools.ToolRegistry
}

// NewDiscoverTool creates a new discovery tool.
func NewDiscoverTool(registry *tools.ToolRegistry) *DiscoverTool {
	return &DiscoverTool{registry: registry}
}

func (t *DiscoverTool) Name() string {
	return "hithink.finance.discover"
}

func (t *DiscoverTool) Description() string {
	return `发现可用的金融数据 tools。传入前缀（如 'hithink.finance.market'）返回该分支下的所有子 tools。

使用示例：
- 查看行情相关 tools：prefix="hithink.finance.market"
- 查看财务相关 tools：prefix="hithink.finance.financial"
- 查看技术指标 tools：prefix="hithink.finance.indicator"
- 查看所有 tools：prefix="hithink.finance"`
}

func (t *DiscoverTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"prefix": map[string]interface{}{
				"type":        "string",
				"description": "tool 名称前缀，如 'hithink.finance.market'。留空返回所有 hithink.finance tools。",
			},
			"depth": map[string]interface{}{
				"type":        "integer",
				"description": "展开深度（默认 1）。1 表示只展开直接子节点，2 表示展开到孙节点。",
				"default":     1,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *DiscoverTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Prefix string `json:"prefix"`
		Depth  int    `json:"depth"`
	}

	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	if params.Prefix == "" {
		params.Prefix = "hithink.finance"
	}
	if params.Depth <= 0 {
		params.Depth = 1
	}

	// Get all registered tools
	allTools := t.registry.ListTools()

	// Filter by prefix and depth
	var matchedTools []ToolInfo
	seen := make(map[string]bool)

	for _, name := range allTools {
		if !strings.HasPrefix(name, params.Prefix) {
			continue
		}

		// Calculate the relative path
		suffix := strings.TrimPrefix(name, params.Prefix)
		if suffix == "" {
			continue // Skip the prefix itself
		}
		suffix = strings.TrimPrefix(suffix, ".")

		parts := strings.Split(suffix, ".")
		if len(parts) > params.Depth {
			continue
		}

		// Avoid duplicates
		if seen[name] {
			continue
		}
		seen[name] = true

		// Get tool description
		tool, err := t.registry.GetTool(name)
		if err != nil {
			continue
		}

		matchedTools = append(matchedTools, ToolInfo{
			Name:        name,
			Description: tool.Description(),
		})
	}

	// Sort by name for stable output
	sort.Slice(matchedTools, func(i, j int) bool {
		return matchedTools[i].Name < matchedTools[j].Name
	})

	// Build result
	result := map[string]interface{}{
		"prefix": params.Prefix,
		"depth":  params.Depth,
		"tools":  matchedTools,
		"count":  len(matchedTools),
	}

	// Add usage hint
	if len(matchedTools) == 0 {
		result["hint"] = fmt.Sprintf("未找到前缀为 '%s' 的 tools。请尝试更短的前缀，如 'hithink.finance'", params.Prefix)
	} else if len(matchedTools) > 20 {
		result["hint"] = fmt.Sprintf("找到 %d 个 tools，建议使用更具体的前缀，如 'hithink.finance.market' 或 'hithink.finance.financial'", len(matchedTools))
	}

	output, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(output),
	}, nil
}

// ToolInfo holds information about a tool.
type ToolInfo struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

// Ensure DiscoverTool implements the Tool interface.
var _ types.Tool = (*DiscoverTool)(nil)
