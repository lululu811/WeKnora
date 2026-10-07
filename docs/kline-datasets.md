# K 线数据集与板块/指数支持

这份文档回答两个容易搞错的问题：**K 线数据来自哪个库**，以及**板块/指数（同花顺
`.TI` 代码）能画什么、不能画什么**。指标公式的归属也在这里写明，因为它是全仓最
容易产生"第二套实现"的地方。

## 1. 数据集路由

`/api/kline`（python-service）按**代码后缀**选数据集，抽成 `_Dataset`
（`python-service/main.py` 的 `_dataset_for`）：

| 后缀 | 数据源 | 复权视图 | 日期列 |
|---|---|---|---|
| `.SH` `.SZ` `.BJ` `.HK` `.US` | `market.duckdb` | `none→v_daily` / `forward→v_daily_qfq` / `backward→v_daily_hfq` | `date` |
| `.TI`（板块/指数） | `index.duckdb` | 三档都指向 `v_index_daily`（没有 `*_qfq/_hfq`） | `trade_date` |

差异只有三处：**数据源、复权视图、日期列名**。`/api/kline` 的 SQL 有
day/week/month × 有/无起始日 共 6 条分支，三处差异由适配器一处提供——不要在分支里
写 `if board`，那必然会漏掉一条。

板块的 `adjust` 参数**接受但不生效**（三档等价）：前端默认发 `forward`，若这里报
400，板块就永远画不出来。前端相应地也不再对板块发 `forward`：`KLineWorkspace`
切到板块时把口径归到 `none`，`kline-cache.ts` 对未指定 adjust 的调用方（对比条、
悬浮卡）按后缀给默认值。

## 2. 板块/指数能画什么

| 能力 | 板块/指数 | 说明 |
|---|---|---|
| 蜡烛 + 成交量 | ✅ | `index.v_index_daily`：OHLCV + turnover |
| MA / BOLL / MACD / KDJ / VOL / 九转 | ✅ | **前端计算**（见第 3 节），不是后端返回 |
| 复权（前/后） | ❌ | 本地只有裸行情；复权选择器置灰并标注"板块不复权" |
| 形态识别 / 背离 / 信号 | ❌ | 依赖 `indicators.duckdb` 里**只对个股落库**的 `candles_cdl_*` 与指标列 |
| `/api/annotate`、`/api/chart-pattern` | ❌ | 只服务个股，传 `.TI` 返回 422（带原因，不是"格式非法"） |
| `/api/stock-profile`（资金面/估值/归属） | ❌ | 返回 `unavailable: ["板块/指数不提供速览数据（仅个股有）"]` |
| `/api/quotes`（批量快照） | ❌ | 单独列进 `unsupported`，**不混进 `missing`**——"不支持"不等于"没有数据" |
| 搜索 | ✅ | `/api/symbols/search` 会 union `index.v_index_universe`（848 个板块，带 `tag`） |
| 正文点票签 / 提及条 | ⏳ 未做 | 文本里的 6 位数字要区分板块与订单号，见下文"边界" |

前端的处理原则：**能算的照常画，不能算的说清原因**。形态图层与复权按钮置灰并给出
一句说明，而不是静默留空——空图层会被读成"识别失败"，与"本地无此标的行情"是同
一类误诊（nginx 502 曾被吞成"无数据"）。

## 3. 指标公式归谁算

三条路径，**不要新增第四条**：

| 用途 | 实现位置 | 数据来源 |
|---|---|---|
| 图表上的 11 个指标（MA/BBI/MACD/KDJ/VOL/涨跌幅/ZX砖型/九转…） | **浏览器** `frontend/src/finance/components/kline/indicators.ts` 等 | 每次用 K 线 bars 现算 |
| Agent / 分析工具（231 列） | **仓库外**的 `a-stock/scripts/indicators_sync.py`（宿主 cron，每天 19:00，2–3 小时） | 写入 `indicators.duckdb` |
| `/api/indicators` 端点 | 只是把上表第二行的列 SELECT 出来 | 本地 DuckDB |

两个必须知道的事实：

- **`/api/indicators` 目前没有任何前端调用者**（`frontend/src` 里搜不到）。图表上的
  指标全部由浏览器计算；`config/indicators.yaml` 已把这 11 个指标登记为
  `backend: frontend`（2026-10-01 从 duckdb 切过去），并由 conformance 测试守着。
- **231 列的生成脚本不在本仓库**（`a-stock/` 不在这个 checkout 里）。所以
  `indicators.duckdb` 是"外部产物"，本仓库只读它；改指标口径要动那个仓库。

### 3.1 白线 / 黄线 / BBI：库里有列，且与工作台逐点一致（2026-10-07 更正）

这一节修正本文档早期版本和 `config/indicators.yaml` 里的一处错误陈述：
"库里的 `v_indicators_daily` 没有 DEMA / 多空线 / BBI 列"。**它们一直存在**：

| 列 | 含义 | 最新交易日覆盖率 |
|---|---|---|
| `zettaranc_zg_white_10` | 白线 DEMA(10) | 99.7% |
| `zettaranc_dg_yellow_14` | 黄线 多空线(14/28/57/114) | 98.5% |
| `zettaranc_bbi` | BBI 牵牛绳(3/6/12/24) | 99.6% |
| `zettaranc_brick_value` | 知行 ZX 砖型（通达信口径） | 100.0% |

因此板块指标**没有走后端**：`v_indicators_daily` 里 `.TI` 行数为 0，而图表本来就
自己算，所以板块拿到的 MA/MACD/KDJ/VOL 与个股同源（同一份前端实现、同一批 bars），
不存在两套公式漂移的问题。

### 3.2 两端是同一个指标，差别只在取整纪律

`python-service/scripts/compare_zettaranc_columns.py` 用真实数据逐点比对
（60 只抽样 × 104,820 bar × 3 列，行情源 `v_daily_qfq` 前复权）：

| 实现 | 与库侧的关系 |
|---|---|
| 全精度递归 | 逐点一致 **100.00%**（白线 99.9868%，残差 4.66e-09 = float64 末位噪声） |
| 每级 `toFixed(2)`（前端） | 最大偏差 **≤0.0097** |

结论：**库侧是全精度栈，前端是显示取整栈，算的是同一个指标。** 因此

- 跨栈断言**必须用容差**（约 0.011，见 `conformance.abs_tolerance`），不能用相等；
- 这三列现在正式声明给其他栈用（`config/indicators.yaml` 中
  `ZG_WHITE` / `DG_YELLOW` / `Z_BBI` 的 `storage.backend: duckdb`），
  `TestDuckDBColumnContractHoldsInBothStacks` 会逐列核对 Go / Python 两侧的拼写。

注意 `Z_MAIN` 只声明了白线：它的真实 `params` 只有 `white_period=10`
（`calc_params` 里的 14 是有注释的历史残留槽位，calc 不读它），
`TestPeriodsMatchTheDuckDBColumnNames` 会拒绝把 14 周期的列挂在它名下。

### 3.3 触发定义在 yaml，不在评估脚本里

`config/indicators.yaml` 的 `triggers:` 块（`schema_version: 2` 新增）定义
"某根 K 线上发生了某件事"的**机器可判定**形式，经 `genmeta.go` 生成进
`python-service/zettaranc/indicator_meta.py` 的 `TRIGGERS`，评估脚本
`scripts/eval_trigger_power.py` 从那里读。

定义若只活在脚本里，第二个消费者就得重新推导一遍，"图上标的那次"和
"报告统计的那次"会悄悄分叉 —— 那正是 Z_RSL 与砖型图两次同名不同义的成因。
战法的**解释**（红2=黄金买点那类）刻意留在知识库，不进计算层。

## 4. SQL 目录（工具描述）

`hithink.finance.query.sql` 是 7 个库的通用只读入口，它给模型看的表/列目录
**由 `testdata/schema.json` 生成**：

```bash
go generate ./internal/agent/tools/hithink_finance/query   # 或 cd 该目录 && go run ./gencatalog
```

产物是静态字符串 `query/sql_query_catalog.go`（不依赖运行时文件系统）。生成器入口
`query/gencatalog/main.go`。`schema_contract_test.go` 里
`TestToolDescriptionColumnsMatchSchema` 会逐列核对描述与快照——**描述写了一个不存在
的列就会红**。此前手写的目录已经腐烂到把 `v_fund_nav` 的列名全写错、给
`v_futures_daily` 编了一个不存在的 `open_interest`，模型照着写 SQL 只会白跑。

范围：`v_*` 全部 + 没有对应 `v_` 视图的 `raw_*` + `_meta`/`_import_batches`（只读元
数据）；`stg_*` / `dim_*` / `calc_*` 是实现细节，不进目录。快照只收 `v_*`，因此
未列入快照的表**可查但不列列名**（描述开头已声明）。

## 5. 边界与前置

- 本地 DuckDB 在 `~/.hithink-finance/`（7 个库），**不随仓库分发**；容器通过
  `HITHINK_DB_DIR` 挂载，未设置时挂到仓库内的空占位目录，此时所有工具返回"数据源
  不可用"，主流程不受影响。
- 板块代码在文本里与订单号、日期长得一样（都是 6 位数字），所以**正文里的板块代码
  暂不自动变成可点入口**：`/api/symbols/resolve` 的入参白名单只含 `.SH/.SZ/.BJ`，
  前端两处文本抽取器也按个股判据收敛。放开它需要同时改这两处，属于单独一期。
- 跟踪池（watchlist）按个股设计：它的搜索会**滤掉**板块结果（行情快照与阈值判定
  都只支持个股），避免池子里出现永远没有价格的行。
