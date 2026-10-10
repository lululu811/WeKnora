# 设计说明：菜单合并（大盘预览 + 谁在动 → 个股追踪）→ 大盘工作台

- **日期**：2026-10-09 · **阶段**：已落地 · **选定方向**：方案 A（缩编全景）
- **对比页**：`explorer.html`（单文件；4 个候选是活的本地页面）
- **落地页面**：`/platform/watchlist`（合并后的唯一金融入口，四 tab）
- **dev 验收入口**（均不鉴权、仅 DEV 存在）：
  - `/platform/dev/tracking` —— 合并后的真实页面（大盘 / 权重 ETF 两个 tab 的数据不需鉴权）
  - `/platform/design-lab/board` —— 新增三块组件单独预览

## 用户任务（用户的话）

> 就是你现在看下这个我的经融台 谁在动这个菜单 融合到个股追踪里
> 其实我们还有很多板块k线数据啊，还有很多很多数据你没有利用的

收盘后先扫一眼大盘和谁在动，再看自选股；想知道国家队在动哪些 ETF，
但现在分散在三个地方，龙虎榜只有 5 条，ETF 一屏放不下。

## 产出物（先定产出物）

一份「今天该看什么」的速览清单 —— 我的持仓哪只在动、龙虎榜谁在被买、
国家队进了哪只宽基 ETF、市场情绪处在什么位置。

---

## 一、菜单合并（方案 A）

### 已锁定的前提

| # | 决定 | 落地位置 |
| --- | --- | --- |
| 1 | 个股追踪页 tab 化（大盘 / 自选股 / 谁在动 / 权重 ETF）；`/dashboard` 保留，页头「展开全屏」进入 | `finance/views/Tracking.vue` |
| 2 | 侧边栏只剩「个股追踪」；`/platform/watch-pulse` 重定向到 `?tab=pulse` | `finance/index.ts` + registry 补 `hiddenInMenu` / `routeRedirect` |
| 3 | 龙虎榜 50 条（后端上限），标题改成真实条数 | `MarketDashboard.vue:DRAGON_LIMIT` |
| 4 | ETF 扩池 + 分组筛选：宽基 8 + 行业 10 | `finance_panel/etf_pool.yaml` |
| 5 | 视觉沿用暖米色纸张体系 | 无新设计变量 |

### 纠正过的一处方向

初版计划出"3 套视觉风格并排"。错。本项目视觉系统已定死
（`etf-flow-tile.md:3`："视觉严格沿用现有暖米色纸张体系"），
痛点全在结构与信息量。候选之间只比**出现在哪里 / 要几步 / 谁在做事**。

### 落地时定的关键决定

1. **不重写三个子页面，只加 `embedded` 开关。**
   `MarketDashboard.vue` 1600+ 行、`Watchlist.vue` 2600+ 行、`WatchPulse.vue` 890 行
   一行业务逻辑都没改，只把页头让给外壳。合并的是入口，不是重焊业务。

2. **四个 tab 懒挂载 + KeepAlive，不是 `v-show`。**
   `v-show` 会让 `Watchlist`/`WatchPulse` 在用户看大盘时照样发请求
   （自选股清单、事件流、排名、K 线订阅）。

3. **自选股缺名是数据问题不是渲染问题。**
   `stock_watches.name` 新增时写入、之后无人回填，客户端只传代码时存的就是
   `"002859"`，那一行会渲染成「002859」+「002859.SZ」两行同文。
   规则提到 `finance/utils/stockDisplayName.ts`，大盘与个股追踪共用，带 5 条单测。

---

## 二、大盘工作台：把没出口的数据接出来

### 起点：不是"数据不够"，是"数据没接出来"

| 库 | 大小 | 视图 | 落地前用到 |
| --- | --- | --- | --- |
| `indicators` | 30.5 GB | `v_indicators_daily`（SMA/EMA/WMA/DEMA/TEMA/TRIMA/KAMA） | 0 |
| `market` | 727 MB | `v_daily` / `_hfq` / `_qfq` / `v_symbol` | 大盘 + 龙虎榜 |
| `index` | 141 MB | **`v_index_daily`**（858 指数 × 5 年日线）<br>`v_index_constituents`（122,368 行） | **0** |
| `special` | 94 MB | 13 视图：涨停池/跌停池/炸板/**连板梯队**/龙虎榜/热股/**飙升榜**/**集合竞价**/异动 | 2 个 |
| `fund` | 83 MB | 10 视图：ETF 日线/持仓/**前十大持有人**/净值/收益率 | 1 个 |
| `futures` | 122 MB | 期货合约/日线/分钟级 | **0** |
| `financials` | 330 MB | 资产负债表、利润表 | 0 |

实测：`/api/kline?symbol=881121.SH`（半导体板块）返回 `{"code":0,"data":[]}` ——
该端点只读 `market.duckdb/v_daily`，只管个股。`v_index_daily` **没有任何端点在读**，
前端 proxy 路由里也没有 sector/index/board。落地前 `/api/market/*` 一共只有 2 个端点。

### 新增端点（python-service）

| 端点 | 数据源 | 返回 |
| --- | --- | --- |
| `GET /api/market/sectors` | index + special | 848 板块今日榜（tag 筛选 + 排序） |
| `GET /api/market/limit-up-ladder` | special | 近 N 日 × 2/3/4/5/6/7+ 板矩阵 |
| `GET /api/market/basis` | futures + index | IF/IC/IH/IM 基差 + 5 年分位 |
| `POST /api/market/industry-map` | index | 批量 `{thscode: {level1, level2}}` |
| `POST /api/market/technicals` | indicators + market | 批量技术指标读数 + 派生判断 |
| `GET /api/market/auction` | special | 集合竞价：高开低开分布 + 风向标 + 异动榜 |

`technicals` 读的是 `indicators.duckdb` —— **264 列、1036 万行、5573 只标的、
2016-09 起**。按代码取最新日实测 0.16 秒（8 只），所以**不做预聚合也不建索引**：
那个查询形状本来就快，预先加工反而要多维护一份口径。

那份表由**两套实现**算出、时间上劈成两段：

| backend | 行数 | 区间 |
| --- | --- | --- |
| `zettaranc_migrate` | 718 万 | 2016-09-12 → **2024-04-30** |
| `pandas_ta\|talib` | 318 万 | 2024-05-06 → **2026-10-09** |

同一指标列在 2024-05-06 前后由不同算法算出。所以该端点**只取最新日**
（永远落在 `pandas_ta|talib` 段内），并在响应里回带 `backend`。
真要做长周期回看（比如"过去两年在均线下方的时间占比"），那段区间跨了实现
切换点，结论会被两种算法的差异污染 —— 那是另一件事，这里不假装能做。

派生判断（`above_sma20` / `ma_alignment` / `rsi_zone` / `macd_state` /
`bb_position`）在**服务端**算，与 `WatchPulse.vue` 头部"前端不重算任何指标"
是同一条约定：同一个指标只允许有一个地方算。

`industry-map` 是**补那批未提交改动的前端调用**——`finance/api/watchlist.ts`
早就声明了 `fetchIndustryMap`，后端一直 404。段位规则从 `halo.industry_map`
直接 import，不复制。

### 三块新增的界面

**板块榜** —— 默认只显示 Top 12，排序是**字典序**而非加权合成分：
涨停家数 → 放量倍数 → |涨跌幅|。合成分必须给权重而权重是拍的；字典序表达的是
真实判断顺序：先看有没有涨停（封板是最硬的证据），再看有没有放量，最后才看涨多少。
今天文化传媒涨 5.78% 不是最猛的，但它排第一 —— 因为有 10 只涨停。

跨库（涨停池在 special、板块日线在 index）所以排序在 Python 侧做：
不能写进 SQL 的 ORDER BY（那一列直到合并后才存在）。代价是本分类板块全量过网
（最多 390 行），换来实现正确。全量 848 个在「展开全部」里 ——
全量是**可核对性**，不是首屏内容。

**连板梯队** —— 20 日 × 6 档热力矩阵。涨停家数只说"今天热不热"，
梯队说"热度在往上垒还是在塌"。着色用品牌色透明度而非涨跌红绿：
红绿在这里会被读成"涨/跌"，而热力强度没有方向。

**期股联动** —— 一行四个基差率 + 5 年分位，可展开看「基差率 vs 现货日涨跌」
双线背离。三个口径决定了这块怎么搭：

1. **必须用主力连续（`IFZL.CFE`）而非挂牌月。** IF2610/IF2612/IF2703 直接减现货
   算出来的是跨月价差不是基差，换月那天会出现根本不存在的跳空。库里已有 ZL 合成合约。
2. **期货报价就是指数点位**（实测 IF 收 4248 对现货 4310），不需要乘数换算。
3. **基差日取两边共同的最新交易日。** 实测期货滞后一天（10-08 vs 现货 10-09）；
   拿 T-1 的期货减 T 的现货会把一整天涨跌混进"基差"，那不是基差而是跨日差，
   且算完看起来完全正常。响应里 `basis_date` / `futures_latest` / `spot_latest`
   三个日期分开给，前端没法不小心配错。

### 集合竞价（盘前）

`special.v_auction_snapshot`（每日 5471 行）与 `v_auction_benchmark`（短线风向标，
带行业标签）此前同样没有端点在读。两条设计决定来自实测而非偏好：

1. **异动榜必须过流通市值门槛。** 全市场量比>2 的有 910 只，但排前面的
   "量比 70 倍"大半是几亿市值的小盘票 —— 那个倍数是流动性噪音不是资金信号。
   50 亿门槛下同样条件只剩 395 只，而排第一的园林股份（52 亿、开盘涨停、
   量比 70）是**真信号**。门槛可调（`min_float_cap`），理由写在模块头。
2. **分布和榜单分开给。** 「今天多少只高开」是全市场 5471 只的统计，不设门槛
   也成立；「哪几只异动」必须过门槛。混在一张榜里，高开家数会被榜单数量级盖过去。

2026-10-09 实测：总 5471，**高开 1551 / 平开 876 / 低开 2967 / 未报价 77**，
竞价涨停 4 只、跌停 3 只 —— **开盘是普跌的**，而这一条在收盘复盘里完全看不出来。

给分位数而不是绝对值：「-1.44% 贴水」没有信息量（股指期货常年贴水），
「处在 5 年 4% 分位」才是判断。当前读数：IF 4.1% / IC 6.7% / **IH 1.8%** / IM 18.4%。

### ETF 池：扩池的价值被数据证实

原 8 只宽基全部深度贴水（-33% ~ -72%），0 只份额增加。扩到 18 只后：

- **半导体 ETF +104.19%** ← 唯一大幅净流入
- 证券 ETF +5.14% / 医药 ETF +3.70%
- 其余 7 只行业 -3.26% ~ -34.42%

**只看宽基会得出"国家队全面撤退"，而那是错的。** 真实情况是资金从宽基撤出、
集中到半导体。这 10 只当初加的理由（"面板不够饱满"）是错的，价值在这里。

行业池代码逐个用同花顺代码表核对过存在，不是凭记忆写的。首次同步抓到 401 条
份额观测（医药 62 期 / 光伏 25 期），不是刚入库的噪音。

---

## 三、验收状态（逐项，不含糊）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| `vue-tsc --build` 全量类型检查 | ✅ exit 0 | — |
| finance 测试套件 | ✅ 282 / 282 | 含 `stockDisplayName` 新增 5 条 |
| 四个新端点经 :80 nginx | ✅ 全 200 + 真实数据 | 直连 curl |
| 旧端点无回归 | ✅ snapshot / dragon-tiger / etf-flow 全 200 | 直连 curl |
| 三个新组件渲染（真实数据） | ✅ 零控制台错误 / 零溢出 | `evidence/workbench-blocks.png` |
| 期股联动「看背离」展开 | ✅ 双线图正常 | `evidence/workbench-basis-divergence.png` |
| ETF 工作区行业分组筛选 | ✅ 宽基 8 / 行业 10 切换正常 | `evidence/etf-sector-group.png` |
| 技术位置条（indicators 30GB） | ✅ 6 只真实标的读数正确 | `evidence/technical-strip.png` |
| 集合竞价（盘前） | ✅ 分布条 + 风向标 + 异动榜 20 条 | `evidence/auction-strip.png` |
| 新代码进入 :80 生产包 | ✅ 四个端点路径均在 bundle 内 | 见下 |
| 前端**全量**测试 | ⚠️ 1345 / 1350，5 项为**既有失败** | 见下 |
| 语言包键集合 HEAD vs 工作区 | ✅ **6 个包各丢失 0 个 key**，仅新增 | 移动不算丢失，已逐键比对 |
| `check-i18n` | ⚠️ 13/15，2 项为**既有失败** | 见「已知限制」 |
| **大盘 tab 内三块的落位渲染** | ❌ **未验收** | 见下 |

### 新代码确实发出了（结构证据）

:80 的生产包内：

```
/api/market/sectors          → market-CLH80LlW.js
/api/market/basis            → market-CLH80LlW.js
/api/market/limit-up-ladder  → market-CLH80LlW.js
/api/market/industry-map     → watchlist-DxLw42PQ.js
md-full（嵌入态容器样式）      → MarketDashboard-DV7trpHC.js
四个 tab 键                    → Tracking-Bm2yLTTD.js
```

### 为什么大盘 tab 没做视觉验收

该页 `requiresAuth`，未登录时自选股请求 `/api/v1/watchlist` 返回 401，
全局 axios 拦截器随即 `redirectToLogin()` 跳登录页 —— 页面在截图之前就已经
被换掉了（`utils/authRefresh.ts:64`，`isEmbedPage()` 只认 `/embed/` 前缀，
没有可借道的开发后门）。

**没有为此创建账号，也没有向使用者索要密码。** 所以「三块组件本身对不对」
已在 design-lab 预览页验收，「它们在大盘 tab 里的位置与间距对不对」需要
登录后打开 `http://localhost` 看 —— 那一步在正常使用中自然会发生。

### 全量测试的 5 项失败，逐项归属

| 失败测试 | 文件 | 是否本次引入 |
| --- | --- | --- |
| `edit/create: initialization stays clean…`（2 项）<br>`closing during initialization does not warn…`（1 项） | `src/composables/useModalShell.test.ts` | **否** —— 该文件 `git diff` 为空；它只 import `vue` / `tdesign-vue-next` / `vue-i18n`，对 finance、locales、proxyRoutes、request 的引用数为 **0** |
| `locale bundles expose the same translation keys`<br>`all finance keys referenced in code exist…` | `localeKeyAudit.test.ts` | **否** —— 失败项为 `kline.layers` + 4 个 `watchlist.*` 行业板块 key，来自另一批未提交改动 |

**语言包"删除行"是移动不是丢失。** `git diff` 会把块移动渲染成删+增
（`samples.*`、`watchPulse` 等被挪了位置）。逐键比对 HEAD 与工作区：
**zh-CN/zh-TW/en-US/ja-JP/ko-KR/ru-RU 六个包各丢失 0 个 key**，只有新增
（152–189 个/包，含另一批改动的与本次的）。

## 四、未做 / 已知限制

- **B 线「汇金披露持仓」不可用。** 实测 18 只 ETF 全部返回
  「巨潮未查到定期报告公告」——巨潮那条查询规则对场内 ETF 不适用，重试无效。
  卡片文案已从"数据接入中"改成实际状态，不再承诺做不到的事。
- **`check-i18n` 13/15**，2 项失败为既有问题：`kline.layers` 与 4 个
  `watchlist.*` 行业板块 key，来自工作区里另一批未提交的改动，
  `git show HEAD` 证明 HEAD 里既无这些文案也无引用它们的代码。本次新增的 key
  一个都没进失败列表。
- **期货 `futures.duckdb` 的商品品种未接。** 本轮只接了四个股指期货
  （期股联动的主线）。商品→板块映射需要另建一张品种-板块对应表，且
  「螺纹钢期货 → 钢铁股」这类映射本身没有权威来源，不适合代使用者拍板。
- **格式化函数仍有三份历史副本**（`MarketDashboard.vue`、`EtfWorkspace.vue`、
  design-lab 页）。新代码统一走 `finance/utils/format.ts`，存量三处未合并 ——
  改动已验证过的页面风险大于收益。

## 五、一次操作事故（已修复）

重建 python-service 容器时漏带 `HITHINK_DB_DIR`，compose 按默认挂了空占位目录
`.hithink-placeholder`，30GB 投研库断开、接口 503。已用
`/Users/chenlei/.hithink-finance` 恢复，数据完好无损。

**后续任何 `docker compose up python-service` 都要显式带 `HITHINK_DB_DIR`**，否则
投研栈会静默地挂成空目录 —— 接口返回 503 而不是报"路径不对"，排查起来不明显。
