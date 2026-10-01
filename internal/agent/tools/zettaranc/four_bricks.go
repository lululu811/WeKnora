package zettaranc

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/Tencent/WeKnora/internal/types"
)

// FourBricksTool returns the 四块砖 state for a single stock.
//
// 2026-10-01 新增。四块砖（短线/趋势/多空/阴阳）此前**只在工作台前端**算得出
// （indicators.ts 的 calcFourBricksDetails），服务端取不到，所以系统提示词要求
// agent 对"四块砖现在什么状态""有没有藏力K"这类问题一律回答"本地算不出来，
// 请去看 K 线工作台"。数值计算搬到 python-service/zettaranc/four_bricks.py
// 之后，agent 与工作台读同一份实现。
//
// 工具**只给数值状态，不给战法解释**："红2 = 黄金买点""红4 高抛"这类解读属于
// 知识库，不在这里生成第二套说法 —— 两套解释必然漂移。
type FourBricksTool struct {
	client *HTTPClient
}

// NewFourBricksTool creates a new four-bricks tool backed by python-service.
func NewFourBricksTool(client *HTTPClient) *FourBricksTool {
	return &FourBricksTool{client: client}
}

func (t *FourBricksTool) Name() string {
	return "zettaranc.four_bricks"
}

func (t *FourBricksTool) Description() string {
	return `查询单只股票的四块砖状态（短线 / 趋势 / 多空 / 阴阳 四项多空）。

返回：四项各自的多/空标记、总分（-4..+4）、红砖数与状态标签。
周期：短线 MA5、趋势 EMA10/EMA14、多空 BBI(3,6,12,24)。

**只给数值状态，不含战法解释。** "红2 是黄金买点""红4 要高抛"这类解读在知识库里，
不要自己推 —— 拿到 bull_count 与 score 之后，去检索知识库再解释。

使用示例：
- 查茅台：thscode="600519.SH"
- 查最近 60 天：thscode="300750.SZ", days=60`
}

func (t *FourBricksTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"thscode": map[string]interface{}{
				"type":        "string",
				"description": "同花顺股票代码，如 600519.SH",
			},
			"days": map[string]interface{}{
				"type":        "integer",
				"description": "回看天数（默认 120）。四块砖用 MA5/EMA10/EMA14/BBI(3,6,12,24)，至少要 24 根以上才稳定",
				"default":     120,
			},
		},
		"required": []string{"thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *FourBricksTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
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
		params.Days = 120
	}
	if params.Days > 500 {
		params.Days = 500
	}

	result, err := t.client.FourBricks(ctx, params.Thscode, params.Days)
	if err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	// insufficient 时不要把一个空壳当答案交出去：明确说清缺多少。
	if inner, ok := result["result"].(map[string]interface{}); ok {
		if insufficient, _ := inner["insufficient"].(bool); insufficient {
			note, _ := inner["note"].(string)
			return &types.ToolResult{
				Success: false,
				Error:   "四块砖数据不足：" + note,
			}, nil
		}
	}

	out, _ := json.MarshalIndent(result, "", "  ")
	return &types.ToolResult{Success: true, Output: string(out)}, nil
}
