# 金融工作台 —— 全貌

这份文档回答「工作台到底是什么、哪些东西在哪、动之前要满足什么」。
给两类读者：**要改工作台的人**（先读这里，再读代码）与
**要判断某个功能该不该进工作台的人**（读第二节的信息架构就够了）。

决策过程在 [`finance-workbench-decisions.md`](finance-workbench-decisions.md)，
信号档位在 [`signal-admission.md`](signal-admission.md)。

## 1. 主线：收盘后扫一眼

工作台服务的是**收盘后坐下来一屏扫完**这个场景。四个入口共享这个心智，
但分工不同：

```
chat 空态工作台（主应用首页）
  └─ 顶部「大盘预览」入口 ─────────────► 大盘预览大屏 /dashboard
  └─ 自选股 / 个股追踪 ───────────────► 个股追踪 /platform/watchlist
  └─ 点开某只票 ──────────────────────► K 线工作台（弹层）
  └─ 侧栏「公众号工作台」──────────────► 公众号工作台 /wechat（iframe）
```

- **大盘预览大屏** `/dashboard` —— 指数条、市场情绪、连板梯队、龙虎榜、ETF 大资金。
  全屏、一屏无滚动；返回入口在组件内（`goBack` 回工作台），不进平台侧栏。
- **个股追踪** `/platform/watchlist` —— 四个 tab：大盘 / 自选股 / 谁在动 / 权重 ETF。
  自选股是主场景：价格、涨跌、行业、信号、持仓状态。
- **K 线工作台** —— 弹层形态的重工具：形态标注、指标、周期、候选池、HALO 报告。
  它是「在个股之上深挖」的一层。
- **公众号工作台** `/wechat` —— iframe 嵌 chatlog 的 `/biz`。是**内容消费**入口，
  不属于行情链路。

### 不是响应式，是桌面端专用

整个 `frontend/src/finance/` 只有 1 个宽度断点（`MarketDashboard.vue` 的 1200px），
其余组件是零断点的固定多列。**这是有意的**：工作台的价值是坐下来一屏扫完，
不是路上看的。新增组件不要顺手加断点；移动端走 `miniprogram/`。

## 2. 数据面：两条链路，一份口径

```
前端 ──► python-service（FastAPI → 本地 DuckDB）   行情/画像类只读查询
      └─► nginx 反代白名单（frontend/nginx.conf 的 location 正则）
         清单的单一真相源：frontend/src/finance/proxyRoutes.ts
         守卫：frontend/src/finance/proxyRoutes.test.ts

agent ─► Go hithink_finance 工具（薄 SQL over 同一批 DuckDB）
         契约：internal/agent/tools/hithink_finance/testdata/schema.json
         守卫：schema_contract_test.go
```

两条链路是**刻意分层**的：前端要的是「一条 SQL 出可直接渲染的聚合」，
agent 要的是「参数化、可组合、可复述的取数」。强行合一要付出两边都变差的代价。

代价是**同一张表两套 SQL 两套口径**。已知的口径陷阱在 AGENTS.md 的
`hithink_finance` 一节（`period` 不是报告期、`report` 是 `YYYY-N`），
共享口径用机器可校验的契约守住，不要各自写死。

### 反代白名单：漏一条 = 静默少一块数据

python-service 的端点由 nginx 显式枚举放行。**漏一条的后果不是报错**：
请求落到通用 `/api/` 规则、被 Go 后端鉴权拦成 401，python-service 根本没收到
请求，前端又把失败吞成空数组 —— 页面按空态渲染，看起来像「今天没数据」。

所以：新增端点先在 `proxyRoutes.ts` 加一条，再同步 nginx 那条正则。
`proxyRoutes.test.ts` 守着三份清单（数组 / 文档常量 / nginx.conf）的一致性，
别绕过它。

## 3. 三条不能违反的不变量

### 注册 ≠ 可用

Go 工具进了代码不等于模型能调：`allowed_tools` 白名单决定它是否进 tool schema
与能否被调用。加工具必须同步加白名单。

两道守卫：
- `internal/agent/tools/zettaranc/whitelist_test.go` —— 金融/战法这一族的工具
  必须与 Z 哥白名单一一对应；
- `internal/healthcheck/reachability.go` —— 注册了却不在任何 agent 白名单里的
  工具会被报成「没人认领」。按能力注册的（capability）工具登记进
  `capabilityRegisteredTools` 并写明注册点。

### 形态 ≠ 信号

K 线上的形态标注是**描述层**：它说的是「这里长成了某个样子」，不是「该买了」。
判据矩阵没有 edge 证据的形态一律不进通知/建议通道，且必须披露确认延迟
（B1 是 D+3 —— 形态要到 3 根 bar 之后才成立）。

档位、判据与「怎么给新信号定档」见 [`signal-admission.md`](signal-admission.md)。

### 推送有统一判定

「什么值得推」（频率上限 / 去重 / 静默时段 / 渠道档位）集中在
`internal/alertnotify/policy.go`。各消费者不自己写一套 —— 写了就会漂，
而且漂的方向通常是「越来越爱推」。

## 4. 目录地图

| 路径 | 内容 |
|---|---|
| `frontend/src/finance/views/` | 四个入口的主视图（MarketDashboard / Watchlist / WatchPulse / Tracking） |
| `frontend/src/finance/components/kline/` | K 线工作台：core-chart（绘制内核）、KLineWorkspace（重外壳）、LayerFilterDropdown、overlay-drawer（标注绘制） |
| `frontend/src/finance/components/market/` | 大盘预览的各块（SectorBoard / LadderHeat / AuctionStrip / BasisStrip / DividendCalendar / HotStockRadar） |
| `frontend/src/finance/components/watchlist/` | 个股追踪面板（WatchDetailPanel / TechnicalStrip / WatchKLineChart） |
| `frontend/src/finance/api/` | 前端 API 层（market / watchlist / pulse / halo） |
| `frontend/src/finance/proxyRoutes.ts` | 反代清单的单一真相源 |
| `python-service/routers/` | python-service 的路由（core / finance / market），共享机制在 `python-service/core.py` |
| `python-service/zettaranc/` | zettaranc 体系模块（形态识别、指标体系、判据） |
| `internal/agent/tools/hithink_finance/` | Go 侧的薄 SQL 工具族 |
| `internal/alertnotify/` | 推送判定与投递 |
| `scripts/watchlist-replay/` | 判据矩阵的回放工具与报告 |

## 5. 已知债与待办

按优先级（详细清单在 `docs/tech-debt/frontend.md` 与 `docs/tech-debt/go-data-layer.md`）：

1. **K 线双外壳**：`KLineWorkspace.vue`（2561 行，重工具）与
   `WatchDetailPanel.vue` + `WatchKLineChart.vue`（轻量）各有一套图表绘制。
   `core-chart.ts` 已经是共享内核，但两个外壳各自长出了工具栏与指标口径 ——
   同一只票在两处画两套图，指标口径可能不一致。目标：收敛到「一个内核 + 两套外壳配置」。
2. **视图层测试**：21k 行 Vue 组件目前只有纯函数测试（`core-chart.test.ts` 这类），
   组件级为 0。需要 DOM 环境的用例（`useModalShell.test.ts`）曾长期红，
   2026-10-19 补了运行环境替身后已绿。
3. **组件规模**：`Watchlist.vue` 2594 / `KLineWorkspace.vue` 2561 /
   `WatchDetailPanel.vue` 1989 / `MarketDashboard.vue` 1765。
   原则：**拆正在写的，不拆正在稳定使用的** —— 这几个文件刚被改动过，
   等各自稳定一轮再动。
4. **上游特性待移植**：2026-10-19 合并上游后，本 fork 尚未移植
   「知识库文件夹三层文档计数」与「嵌入态草稿保留 / steer 资源域」，
   对应的上游用例已挂起（`test.skip`）并写明原因。
