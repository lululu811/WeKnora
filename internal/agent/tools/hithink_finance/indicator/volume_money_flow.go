package indicator

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// VolumeMoneyFlowTool 获取股票主力资金流向指标：OBV、CMF、MFI、VWAP。
type VolumeMoneyFlowTool struct {
	config *hithink_finance.Config
}

func NewVolumeMoneyFlowTool(config *hithink_finance.Config) *VolumeMoneyFlowTool {
	return &VolumeMoneyFlowTool{config: config}
}

func (t *VolumeMoneyFlowTool) Name() string {
	return "hithink.finance.indicator.volume.money_flow"
}

func (t *VolumeMoneyFlowTool) Description() string {
	return `获取股票主力资金指标：OBV 能量潮、CMF 蔡金资金流向、MFI 资金流量指标、VWAP 成交量加权平均价。

返回 date, obv, cmf, mfi, vwap。`
}

func (t *VolumeMoneyFlowTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码（如 600519.SH）",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "查询天数（默认 30）",
				"default":     30,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *VolumeMoneyFlowTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		Thscode string `json:"thscode"`
		Days    int    `json:"days"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Thscode == "" {
		return &types.ToolResult{Success: false, Error: "参数错误：thscode 不能为空"}, nil
	}
	if params.Days <= 0 {
		params.Days = 30
	}
	if params.Days > 500 {
		params.Days = 500
	}

	query := `SELECT date, volume_obv AS obv, volume_cmf_20 AS cmf, volume_mfi_14 AS mfi, volume_vwap AS vwap FROM v_indicators_daily WHERE thscode = ? ORDER BY date DESC LIMIT ?`

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "indicators", query, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	output := map[string]interface{}{
		"thscode": params.Thscode,
		"days":    len(rows),
		"data":    rows,
	}

	resBytes, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var _ types.Tool = (*VolumeMoneyFlowTool)(nil)
