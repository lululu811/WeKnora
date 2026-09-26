package kline_studio

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/Tencent/WeKnora/internal/types"
)

// Pick is the wire shape for one entry pushed to kline-studio's /api/picks.
type Pick struct {
	Ticker   string `json:"ticker"`
	Exchange string `json:"exchange"`
}

// ShowTool pushes a list of stocks to the kline-studio "今日 picks" list and
// returns a frontend URL the user can open to review them on K-line charts.
type ShowTool struct {
	cfg    *Config
	client *http.Client
}

// NewShowTool creates a ShowTool bound to the given config.
func NewShowTool(cfg *Config) *ShowTool {
	if cfg == nil {
		cfg = DefaultConfig()
	}
	return &ShowTool{
		cfg:    cfg,
		client: &http.Client{Timeout: cfg.Timeout},
	}
}

func (t *ShowTool) Name() string {
	return "kline_studio.show"
}

func (t *ShowTool) Description() string {
	return `将一组股票推送到 kline-studio 复盘终端的「今日 picks」列表，并返回前端 URL。

适用场景：
- 复盘时把今日筛出来的标的集中展示在 K 线图上
- 把 KB 检索出来的相关标的送进 kline-studio 看走势
- 配合 zettaranc.screener：先用 screener 选出 N 只，再用本工具一次性推送到 kline-studio
- 用户问完 KB 后追加「去 K 线图上看一下」「推到 kline-studio」「用 k 线图复盘」时自动调用

调用流程（KB 路径）：
1. 若刚做完 search_knowledge / knowledge_search，从返回文本里抽取 6 位 ticker 与交易所（SH/SZ/BJ）
2. 调用本工具，把 ticker 列表传过去（tickers 或 thscode 两种格式任选）
3. 把返回的 url 转给用户；用户点击即可在 kline-studio 中查看

参数支持两种格式（任选其一）：
- tickers: [{ticker, exchange}, ...]   例如 [{ticker:"600519", exchange:"SH"}]
- thscode: ["600519.SH", ...]          自动拆出 ticker 与 exchange

返回内容：
- url：前端深链 URL（已带 ?tab=picks 参数），用户点击即可打开 picks tab
- count：推送的标的数量
- tickers：实际写入的标的列表

使用示例：
- 把 screener 结果送到 kline-studio：tickers=[{ticker:"600519", exchange:"SH"}, ...]
- 用 thscode 列表：thscode=["600519.SH", "000001.SZ"]
- KB 检索后推到 kline-studio：先 search_knowledge，抽取 ticker，再调本工具`
}

func (t *ShowTool) Parameters() json.RawMessage {
	schema := map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"tickers": map[string]interface{}{
				"type":        "array",
				"description": "股票列表，每项 {ticker, exchange}",
				"items": map[string]interface{}{
					"type": "object",
					"properties": map[string]interface{}{
						"ticker":   map[string]interface{}{"type": "string"},
						"exchange": map[string]interface{}{"type": "string", "enum": []string{"SH", "SZ", "BJ"}},
					},
					"required": []string{"ticker", "exchange"},
				},
			},
			"thscode": map[string]interface{}{
				"type":        "array",
				"description": "同花顺代码列表（自动拆 ticker/exchange），如 [\"600519.SH\", \"000001.SZ\"]",
				"items":       map[string]interface{}{"type": "string"},
			},
		},
		"anyOf": []map[string]interface{}{
			{"required": []string{"tickers"}},
			{"required": []string{"thscode"}},
		},
	}
	data, _ := json.Marshal(schema)
	return data
}

func (t *ShowTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	var params struct {
		Tickers []Pick  `json:"tickers"`
		Thscode []string `json:"thscode"`
	}
	if err := json.Unmarshal(args, &params); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("参数解析失败：%v", err),
		}, nil
	}

	picks := normalizePicks(params.Tickers, params.Thscode)
	if len(picks) == 0 {
		return &types.ToolResult{
			Success: false,
			Error:   "参数错误：tickers 与 thscode 至少需要提供一个，且内容不能为空",
		}, nil
	}
	if len(picks) > 200 {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("单次推送上限 200 只，当前 %d 只", len(picks)),
		}, nil
	}

	if err := t.pushPicks(ctx, picks); err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("推送到 kline-studio 失败：%v", err),
		}, nil
	}

	url := strings.TrimRight(t.cfg.BaseURL, "/") + "/?tab=picks"
	data := map[string]interface{}{
		"display_type": "kline_studio",
		"url":          url,
		"count":        len(picks),
		"tickers":      picks,
	}
	outputJSON, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return &types.ToolResult{
			Success: false,
			Error:   fmt.Sprintf("结果序列化失败：%v", err),
		}, nil
	}
	return &types.ToolResult{
		Success: true,
		Output:   string(outputJSON),
		Data:     data,
	}, nil
}

func (t *ShowTool) pushPicks(ctx context.Context, picks []Pick) error {
	body, err := json.Marshal(picks)
	if err != nil {
		return fmt.Errorf("请求序列化失败：%v", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		strings.TrimRight(t.cfg.APIURL, "/")+"/api/picks", bytes.NewBuffer(body))
	if err != nil {
		return fmt.Errorf("创建请求失败：%v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := t.client.Do(req)
	if err != nil {
		return fmt.Errorf("kline-studio 后端不可用（%s）：%v。请检查 kline-studio 是否启动", t.cfg.APIURL, err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("读取响应失败：%v", err)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d：%s", resp.StatusCode, string(respBody))
	}
	return nil
}

// normalizePicks 合并 tickers + thscode 两种入参，去重并按 ticker+exchange 排序。
func normalizePicks(tickers []Pick, thscode []string) []Pick {
	seen := make(map[string]bool)
	out := make([]Pick, 0, len(tickers)+len(thscode))
	for _, p := range tickers {
		key := p.Ticker + "." + p.Exchange
		if p.Ticker == "" || p.Exchange == "" || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, Pick{Ticker: p.Ticker, Exchange: strings.ToUpper(p.Exchange)})
	}
	for _, raw := range thscode {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		parts := strings.Split(raw, ".")
		if len(parts) != 2 {
			continue
		}
		ticker, exchange := parts[0], strings.ToUpper(parts[1])
		if exchange != "SH" && exchange != "SZ" && exchange != "BJ" {
			continue
		}
		key := ticker + "." + exchange
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, Pick{Ticker: ticker, Exchange: exchange})
	}
	return out
}

// Ensure ShowTool implements the Tool interface.
var _ types.Tool = (*ShowTool)(nil)