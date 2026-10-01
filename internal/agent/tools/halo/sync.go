package halo

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// SyncTool 把巨潮年报 PDF 里的事实抽取出来落进本地事实库。
type SyncTool struct {
	client *HTTPClient
}

// NewSyncTool 创建同步工具。
func NewSyncTool(client *HTTPClient) *SyncTool {
	return &SyncTool{client: client}
}

func (t *SyncTool) Name() string {
	return "halo.filing.sync"
}

func (t *SyncTool) Description() string {
	return `从巨潮资讯网抓取指定股票的年报/半年报/季报 PDF，抽取资产负债表事实并落库。

**为什么需要它**：本地财务数据源（hithink）覆盖利润表、资产负债表汇总项、
现金流量表和财务指标，但**没有**固定资产、在建工程、存货、无形资产、商誉、
员工人数——这些只在年报 PDF 原文里。而基本面分析框架（如 HALO）的多个维度
直接依赖这些字段（资本-劳动力比率要员工数，有形资产密集度要存货与在建工程）。
本工具补的就是这块缺口。

**取哪些字段**：
  fixed_assets 固定资产 / construction_in_progress 在建工程 /
  inventory 存货 / intangible_assets 无形资产 / goodwill 商誉 /
  employees_total 在职员工数量合计 / total_assets 资产总计 / net_profit 净利润

**权威源是巨潮原文**（法定披露平台），不是本地库。本地 hithink 数据只用来
**对账**：对得上的标记为可信，对不上的标为存疑并保留原文页码等人工裁决。
本工具**不会**在抽取失败时用本地库的数去顶替——那等于编造。

**report_type 取值**：
  annual 年报（含员工人数，是唯一能拿到员工数的口径）
  h1     半年报   q1 一季报   q3 三季报

**耗时**：要下载 PDF（单份 1–10 MB）并逐页解析，一份年报约 1–3 分钟。
**先查再同步**：同一只股票同一 report_type 默认走缓存（年报一年只变一次），
只有首次或修好抽取逻辑后用 force=true 重跑才真正下载解析。

**返回里的 status_summary / disputed / missing 必须如实转述给用户**：
  verified  可信，两条抽取通道一致且对账通过
  disputed  存疑，通道间不一致，需人工回原文（raw_text/source_page 里有线索）
  pending   只有一路产出，尚未交叉验证
存疑和待核的数字**不能**当作分析依据，也不能自己补一个"差不多的值"。

使用示例：thscode="600519", report_type="annual"`

}

func (t *SyncTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "6 位股票代码，如 600519",
			},
			"report_type": map[string]interface{}{
				"type":        "string",
				"description": "披露类型。annual=年报（唯一含员工人数的口径）/ h1=半年报 / q1=一季报 / q3=三季报",
				"enum":        []string{"annual", "h1", "q1", "q3"},
				"default":     "annual",
			},
			"force": map[string]interface{}{
				"type":        "boolean",
				"description": "强制重跑，忽略缓存。抽取逻辑修好后需要用它让修复生效；日常查询不要开。",
				"default":     false,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *SyncTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Thscode    string `json:"thscode"`
		ReportType string `json:"report_type"`
		Force      bool   `json:"force"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}
	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.ReportType == "" {
		params.ReportType = "annual"
	}

	result, err := t.client.Sync(ctx, params.Thscode, params.ReportType, params.Force)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	outputJSON, _ := json.MarshalIndent(result, "", "  ")
	return &types.ToolResult{Success: true, Output: string(outputJSON)}, nil
}

var _ types.Tool = (*SyncTool)(nil)
