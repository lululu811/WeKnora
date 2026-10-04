# 大盘预览 · 实施交接（给 mcode）

设计已定稿（独立评审两轮，8.5/10，结论「可以走实现」）。视觉稿与证据都在本目录：

- `design.md` — 设计说明、信息架构、数据口径、评审记录
- `dashboard.html` — 大屏高保真样稿（可交互：`?state=open|closed|holiday`、`?watchlist=empty`、`?theme=dark`、刷新/主题切换可点）
- `workbench-entry.html` — chat 工作台 + 入口横幅样稿
- `shots/` — 各状态截图、录屏 `record.mp4`、200% 局部

实现时**视觉以样稿为准逐格对照**，本文只写样稿表达不了的工程信息。

## 需求一句话

chat 空态工作台（`frontend/src/views/creatChat/creatChat.vue`）加一张「大盘预览」入口横幅，点击跳转新顶级全屏路由 `/dashboard`，一屏展示：四大指数 60 日走势 + 当日快照条、指数快照条、市场情绪（涨跌停/炸板/连板/近5日涨停/涨跌家数）、自选股、全市场龙虎榜净买入前五、权重宽基 ETF。

## 前端落点

1. **路由**：`frontend/src/router/index.ts` 加顶级路由 `/dashboard`（参照现有 design-lab 全屏先例，line ~231 注释「render full-bleed, outside the platform sidebar shell」），`meta: { requiresInit: true, requiresAuth: true }`。
2. **入口横幅**：`creatChat.vue` 的 composer 与 `workbench-recents` 之间插一个 section（样稿 `workbench-entry.html`）。交互完全沿用 `recent-card`：hover 抬起 -2px + 暖光阴影、active scale(.98)。横幅内三组微型指标（上证指数最新+涨跌幅、涨停/跌停家数、自选股触发数）调下方第 4 条的聚合接口；自选股触发数来自 `GET /api/v1/watchlist/events` 或 watchlist 列表的 state 字段。i18n key 走 `createChat.*` 命名空间。
3. **大屏页面组件**：建议放 `frontend/src/finance/views/MarketDashboard.vue`（finance 模块自留地），样式全部引用 `theme.css` 现有变量（样稿 CSS 顶部注释标了每个变量对应哪个 `--td-*`），**不要新增设计变量**。数字用 `--app-font-family-mono` + `tabular-nums`；标题衬线用 `--app-font-display`。
4. **图表**：60 日折线用 inline SVG（样稿就是 SVG，零依赖；klinecharts 对这种迷你折线是杀鸡用牛刀，不引入 echarts）。昨收虚线、当日快照条都是简单 SVG/div，照抄样稿结构。
5. **动效**：三处——入场各格 stagger rise 60ms（复用 `useStaggerRise` / `data-rise` 模式）；刷新按钮旋转 + 数字 300ms 透明度起伏；入口横幅 hover/press（同 recent-card）。全部遵守 `prefers-reduced-motion`。
6. **API 层**：新接口走 `frontend/src/finance/api/` 下新建 `market.ts`，python-service 路径要在 `frontend/src/finance/proxyRoutes.ts` 登记（**并同步 `frontend/nginx.conf` 正则**，该文件头部有同步说明）。

## 后端缺口（python-service，`python-service/main.py`）

三个新只读接口，全部打本地 DuckDB（参照 `python-service/main.py:2257` `/api/stock-profile` 里 dragon_tiger 块的查询写法；SQL 参考 `internal/agent/tools/hithink_finance/special/dragon_tiger.go`、`special/limit_up_pool.go`）：

1. `GET /api/market/snapshot` — 聚合接口：指数快照（`index.v_index_latest`，代码清单前端传或后端常量）、ETF 快照（`fund.v_etf_latest`）、涨跌停/炸板计数与最高连板（`special.v_limit_up_pool` / `v_limit_down_pool` / `v_limit_break_pool` 按最新 `trade_date` COUNT、MAX(continue_day_cnt)）、近 5 日涨停家数（limit_up_pool GROUP BY trade_date 取近 5）。入口横幅和大屏共用这一个接口。
2. `GET /api/market/dragon-tiger?limit=5` — `special.v_dragon_tiger` 按最新 `trade_date`、净买入额排序。**上榜原因字段（board_type）要转译**，样稿里的写法：「科创板涨跌幅限制」「日涨幅偏离值达 7%」「日换手率达 20%」「三日累计涨幅偏离达 20%」，不要直出交易所枚举原文。
3. 指数 60 日 K 线**不用新接口**——`GET /api/kline` 已支持 `.TI` 指数（`main.py:1774`）。自选股列表走 Go `/api/v1/watchlist` + `/api/quotes` 批量报价（注意 `/api/quotes` 拒 `.TI`，自选股都是股票所以没问题）。

已知可选项：**涨跌家数条**（上涨/平盘/下跌）需要全市场聚合，偏重。设计允许砍掉——砍掉后情绪格依然成立。如果做，建议只在 `/api/market/snapshot` 里顺带算，不要单独接口。

## 硬约束

- 红涨绿跌；浅色 `--up:#dc2626 / --down:#047857`，深色 `--up:#f87171 / --down:#34d399`（深色提档是评审定的，理由见 design.md）。
- 一屏无滚动：1440×900 与 1366×768 都要放下（样稿有 `max-height:800px` 媒体查询，自选股 7 行减 6 行）。
- 盘后/非交易日状态：快照条圆点标签从「最新」变「收盘」，状态 chip 变灰；非交易日在日期后注明「展示最近交易日」。
- 数据为空的处理：自选股空态照样稿（快速添加 chip + 实心主按钮，chip 点击跳 `/platform/watchlist`）；龙虎榜/涨跌停当日无数据时格子显示「今日暂无数据」，不要留白也不要报错。
- 这是 fork 的 `mine` 分支工作；改 `config/` 之外的东西前读 `GIT_WORKFLOW.md`。测试：前端 `npm run type-check`，有新组件就顺手跑 `npm test`；python-service 新接口加 pytest（`python-service/tests/` 现有结构）。

## 验收（对照样稿截图）

1. `npm run dev` 打开 `/dashboard`，与 `shots/open.png` 逐格对照（布局、字号、间距、配色）。
2. 工作台出现入口横幅，三组指标是真实数据；点击进 `/dashboard`，返回键回工作台。
3. 切深色主题对照 `shots/dark.png`；窗口调到 1366×768 对照 `shots/small-1366.png`。
4. 非交易日/盘后检查 chip 与「收盘」文案；清空自选股检查空态。
