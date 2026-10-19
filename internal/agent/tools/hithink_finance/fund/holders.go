package fund

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
	"github.com/Tencent/WeKnora/internal/types"
)

// ETFHoldersStructureTool 查询指定基金/ETF的持有人结构（机构vs散户比例、户数）与前十大持有人明细。
type ETFHoldersStructureTool struct {
	config *hithink_finance.Config
}

// NewETFHoldersStructureTool 创建 ETFHoldersStructureTool。
func NewETFHoldersStructureTool(config *hithink_finance.Config) *ETFHoldersStructureTool {
	return &ETFHoldersStructureTool{config: config}
}

func (t *ETFHoldersStructureTool) Name() string {
	return "hithink.finance.fund.etf.holders_structure"
}

func (t *ETFHoldersStructureTool) Description() string {
	return `查询基金或 ETF 的持有人结构与前十大持有人。
包含机构投资者比例 (ins_position)、个人散户比例 (psnl_rate)、持有人户数 (holder_amount) 以及前十大持有人（如中央汇金、险资、券商集合理财、QFII）穿透清单。`
}

func (t *ETFHoldersStructureTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"fund_thscode": map[string]interface{}{
				"type":        "string",
				"description": "基金代码，如 158000.SZ 或 510300.SH",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "前十大持有人返回条数上限（默认 10）",
				"default":     10,
			},
		},
		"required": []string{"fund_thscode"},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ETFHoldersStructureTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		FundThscode string `json:"fund_thscode"`
		Limit       int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	code := strings.TrimSpace(params.FundThscode)
	if code == "" {
		return &types.ToolResult{Success: false, Error: "fund_thscode 不能为空"}, nil
	}
	if params.Limit <= 0 || params.Limit > 20 {
		params.Limit = 10
	}

	// 1. 查询持有人结构
	structureQuery := `SELECT fund_thscode, merge_scope, report_date_ms, ins_position, holder_amount, avg_holder_share, psnl_rate, mgmt_staff_hold_rate FROM v_fund_holders WHERE fund_thscode = ? ORDER BY report_date_ms DESC LIMIT 1`
	structureRows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "fund", structureQuery, code)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	// 2. 查询前十大持有人
	topQuery := fmt.Sprintf(`SELECT fund_thscode, holder_name, holder_type, holder_code, rank, hold_share, hold_rate_pct, report_date_ms, publish_date_ms FROM v_fund_top_holders WHERE fund_thscode = ? ORDER BY rank ASC LIMIT %d`, params.Limit)
	topRows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "fund", topQuery, code)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	result := map[string]interface{}{
		"fund_thscode": code,
		"structure":    structureRows,
		"top_holders":  topRows,
	}

	resBytes, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

// ETFInstitutionalRankingTool 查询机构投资者持有比例最高或散户比例最高的 ETF 榜单。
type ETFInstitutionalRankingTool struct {
	config *hithink_finance.Config
}

// NewETFInstitutionalRankingTool 创建 ETFInstitutionalRankingTool。
func NewETFInstitutionalRankingTool(config *hithink_finance.Config) *ETFInstitutionalRankingTool {
	return &ETFInstitutionalRankingTool{config: config}
}

func (t *ETFInstitutionalRankingTool) Name() string {
	return "hithink.finance.fund.etf.institutional_ranking"
}

func (t *ETFInstitutionalRankingTool) Description() string {
	return `查询全市场基金/ETF 机构投资者占比排行（机构重仓定价权 ETF vs 散户扎堆 ETF）。`
}

func (t *ETFInstitutionalRankingTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"order_by": map[string]interface{}{
				"type":        "string",
				"description": "排序依据：'institution' (机构占比从高到低，默认) 或 'retail' (散户占比从高到低)",
				"default":     "institution",
			},
			"limit": map[string]interface{}{
				"type":        "integer",
				"description": "返回数量限制（默认 20，最大 50）",
				"default":     20,
			},
		},
		"required": []string{},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ETFInstitutionalRankingTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	if err := hithink_finance.CheckSyncWindow(); err != nil {
		return &types.ToolResult{Success: false, Error: err.Error()}, nil
	}

	var params struct {
		OrderBy string `json:"order_by"`
		Limit   int    `json:"limit"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("参数解析失败：%v", err)}, nil
	}

	if params.Limit <= 0 || params.Limit > 50 {
		params.Limit = 20
	}

	orderCol := "ins_position DESC"
	if params.OrderBy == "retail" {
		orderCol = "psnl_rate DESC"
	}

	query := fmt.Sprintf(`SELECT fund_thscode, merge_scope, report_date_ms, ins_position, holder_amount, avg_holder_share, psnl_rate, mgmt_staff_hold_rate FROM v_fund_holders WHERE ins_position IS NOT NULL ORDER BY %s LIMIT %d`, orderCol, params.Limit)

	rows, err := hithink_finance.QueryDuckDBParams(ctx, t.config, "fund", query)
	if err != nil {
		return &types.ToolResult{Success: false, Error: hithink_finance.FriendlyQueryError(err, t.Name())}, nil
	}

	resBytes, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		return &types.ToolResult{Success: false, Error: fmt.Sprintf("序列化结果失败：%v", err)}, nil
	}

	return &types.ToolResult{
		Success: true,
		Output:  string(resBytes),
	}, nil
}

var (
	_ types.Tool = (*ETFHoldersStructureTool)(nil)
	_ types.Tool = (*ETFInstitutionalRankingTool)(nil)
)
