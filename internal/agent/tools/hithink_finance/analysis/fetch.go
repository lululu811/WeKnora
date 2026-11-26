package analysis

import (
	"context"
	"fmt"

	"github.com/Tencent/WeKnora/internal/agent/tools/hithink_finance"
)

// 这一族四个工具共用同一套取数：查两个 DuckDB（行情 + 指标）再按日期在 Go 里
// 合并。两个库是独立的只读连接、ATTACH 不了，合并只能在 Go 里做——所以取数
// 是代码，不是数据，它以 Fetch 钩子的形式留在这一边。
//
// 参数解析与窗口钳制在 spec 包那边（Fetch 收到的是解析好的 values），
// 这里只负责把值取出来用。

// fetchMarketData 是这一族共用的取数。
func fetchMarketData(
	ctx context.Context,
	cfg *hithink_finance.Config,
	values map[string]any,
) (any, error) {
	thscode, _ := values["thscode"].(string)
	days, _ := values["days"].(int)
	return FetchMarketData(ctx, cfg, thscode, days)
}

// fetchMarketDataLabelled 是 trend 用的取数：它的取数失败文案与另外三个不同
// （"数据加载失败：…"），所以自己包一层。
func fetchMarketDataLabelled(
	ctx context.Context,
	cfg *hithink_finance.Config,
	values map[string]any,
) (any, error) {
	rows, err := fetchMarketData(ctx, cfg, values)
	if err != nil {
		return nil, fmt.Errorf("数据加载失败：%w", err)
	}
	return rows, nil
}
