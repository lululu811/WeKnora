# Hithink Finance Tools

金融数据查询工具集，基于 DuckDB 本地数据和 Python CLI 计算。

## 架构概览

```
用户提问 → Z哥智能体（WeKnora）
  │
  ├─ 需要行情/财务/指标数据 → hithink.finance.* (Go DuckDB 直连)
  │   └─ 延迟 <10ms
  │
  ├─ 需要战法识别/回测/选股 → zettaranc.* (Go 调用 Python CLI)
  │   └─ 延迟 500ms-2s
  │
  └─ 需要交易规则/决策框架 → WeKnora RAG（knowledge/*.md）
```

## 公共 Tools（hithink.finance.*）

### Discovery Tool

```
hithink.finance.discover
```

发现可用的子 tools。传入前缀返回该分支下的所有子 tools。

**示例：**
- 查看行情相关 tools：`prefix="hithink.finance.market"`
- 查看所有 tools：`prefix="hithink.finance"`

### Market Tools（market.duckdb）

#### hithink.finance.market.price.snapshot

获取单只股票的最新行情快照（前复权）。

**参数：**
- `thscode`: 同花顺股票代码，如 `600519.SH`（茅台）

**返回：**
- `date`: 交易日期
- `open/high/low/close`: 开高低收
- `volume`: 成交量（股）
- `amount`: 成交额（元）

#### hithink.finance.market.price.historical

获取股票的历史 K 线数据（前复权）。

**参数：**
- `thscode`: 同花顺股票代码
- `days`: 查询天数（默认 30，最大 500）

**返回：**
- 每日的 date, open, high, low, close, volume, amount

### Financial Tools（financials.duckdb）

#### hithink.finance.financial.valuation.snapshot

获取单只股票的最新估值快照。

**参数：**
- `thscode`: 同花顺股票代码

**返回：**
- `pe_ttm`: 市盈率（TTM）
- `pb`: 市净率
- `ps_ttm`: 市销率（TTM）
- `market_cap`: 总市值（元）
- `circ_market_cap`: 流通市值（元）

### Indicator Tools（indicators.duckdb）

#### hithink.finance.indicator.trend.ma

获取股票的均线指标（MA）。

**参数：**
- `thscode`: 同花顺股票代码
- `days`: 查询天数（默认 30）

**返回：**
- 每日的 ma5, ma10, ma20, ma60, ma120, ma250

### Query Tools

#### hithink.finance.query.sql

执行只读 SQL 查询。支持查询所有 DuckDB 数据库的 v_* 视图。

**参数：**
- `sql`: SQL 查询语句（只允许 SELECT）
- `db`: 数据库名称（market, financials, indicators, special, index, fund, futures）

**安全限制：**
- 只允许 SELECT 查询
- 默认 LIMIT 1000 行
- 禁止 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER 等操作

**示例：**
```sql
-- 查询茅台最近 5 天行情
sql="SELECT * FROM v_daily_qfq WHERE thscode='600519.SH' ORDER BY date DESC LIMIT 5"
db="market"

-- 查询涨停池
sql="SELECT * FROM v_limit_up_pool WHERE trade_date='2026-09-24'"
db="special"
```

## Zettaranc 专属 Tools（zettaranc.*）

这些 tools 调用 python-service 的 `/zettaranc/*` 端点，延迟较高（500ms-2s）。

### 单票技术分析：四个原子工具（`hithink.finance.analysis.*`）

单票的趋势 / 量价 / 形态 / 支撑阻力**不再有复合入口**。此前的 `zettaranc.analyze`
一次调用返回四段，已删除，改由四个独立原子工具各自回答一段（本地 DuckDB 计算，
不经 python-service）：

| 维度 | 工具 |
|---|---|
| 趋势与均线 | `hithink.finance.analysis.trend` |
| 量价与威科夫 | `hithink.finance.analysis.volume` |
| 形态识别 | `hithink.finance.analysis.pattern` |
| 支撑阻力 | `hithink.finance.analysis.levels` |

需要多维结论时由模型分别调用再汇总 —— 这是刻意的：单点问题只付一段的代价，
多维问题才付多维的代价。

### zettaranc.backtest

**【未实现，调用会直接失败】** 真实回测（逐日重放信号、持仓与撮合、绩效统计）尚未落地。

这个工具过去会拿**选股结果**冒充回测结果，现已停止该行为：`Execute` 一律返回
`Success:false` 并附替代方案，参数 schema 里每个字段都标着「当前不支持，调用一律失败」。
它也**不在任何 agent 的 `allowed_tools` 里**（`config/builtin_agents.yaml` 与
`agent_service.go` 的注册 switch 都没有它），所以模型连它的 schema 都看不到 ——
一条"不要调它"的 prompt 提醒反而会白占上下文。实现文件留在仓库里，真回测落地后
加回白名单一行即可。

替代路径：

| 想做的事 | 用哪个 |
|---|---|
| 从全市场按战法挑票 | `zettaranc.screener` |
| 单只票的趋势 / 量价 / 形态 / 支撑阻力 | `hithink.finance.analysis.trend` / `.volume` / `.pattern` / `.levels` |
| 取历史 OHLCV 自行核算收益 | `hithink.finance.market.price.historical` |

### zettaranc.screener

使用 Z哥交易体系进行全市场智能选股。一条 SQL 取回全市场指标 + 价量后在本地判定，
不逐只扫描。

**参数：**

- `strategy`: 选股策略名，共 **20 个**，按方向分三类：看涨（超卖共振、买点类）、
  看跌规避（超买 / 死叉 / 空头排列，比选新票更常用）、方向无关（波动率异动）。
  **本文档不维护这份清单**：唯一真相源是 python-service 的 `STRATEGY_RULES`，
  Go 侧 `zettaranc.ScreenerStrategies()`（`internal/agent/tools/zettaranc/strategies.go`）
  是给模型的 enum 提示，`strategies_test.go` 会在两边漂移时报错 —— 抄第三份到文档里
  只会多一处会漂的地方。
- `limit`: 返回数量（默认 20，最大 100）
- 可选筛选（第二道关）：`sector` 板块限定；`max_debt_ratio` / `min_current_ratio` /
  `max_receivable_ratio` 财务风险代理（出处 `financials.v_balance_sheet`）；
  `require_profit` 最新期归母净利润为正；`exclude_st` 排除 ST/*ST。
  **商誉、股权质押、减持、审计意见、监管处罚本地无数据源**，所以没有对应参数 ——
  回答"有没有暴雷风险"时必须说明这一层没覆盖。

**返回：**
- 按评分排序的候选股票列表
- 每只股票的匹配理由
- 技术指标快照
- 可信度信息：`scanned` / `scanned_from_universe` / `truncated` / `risk_filter` / `warnings`

**示例：**
- 超卖组合选股：`strategy="oversold_combo", limit=20`
- 半导体板块内排除 ST：`strategy="vol_breakout", sector="半导体", exclude_st=true`

## 配置

### DuckDB 路径

默认路径：`~/.hithink-finance/`

可用环境变量 `HITHINK_DB_DIR` 覆盖；也可在 `hithink_finance.Config` 中修改。

> 该目录存放 hithink-finance 的本地 DuckDB 行情/财报库，**不随本仓库分发**。
> 需自行准备数据，或不启用 finance profile（见 [README](../README.md)）。

### Python CLI 路径

默认路径：
- Python: `python3`（PATH 中的解释器）
- CLI 目录: 由 `zettaranc.Config` 指定

可在 `zettaranc.Config` 中修改。

### 时间窗口隔离

每日 ETL 重算期间同步进程持有 DuckDB 写锁，窗口内放行查询会撞 `database is locked`，
因此这些时段直接拒绝查询。窗口由 `HITHINK_SYNC_WINDOWS` 指定：

- 格式 `HH:MM-HH:MM[,HH:MM-HH:MM...]`，例如 `17:25-17:35,02:55-03:05`
- 不设置 = 用默认值 `17:25-17:35,02:55-03:05`
- 设为**空字符串** = 不做窗口拦截（自建 ETL 时刻表与默认值不同时用这个）
- `start > end` 视为跨零点窗口，例如 `23:50-00:10`
- 无法识别的片段被跳过，不影响其余窗口

## 错误处理

所有 tools 都提供可执行的错误建议：

- **数据源不可用**：检查 DuckDB 文件是否存在，数据是否已同步
- **股票不存在**：检查股票代码格式是否正确（如 `600519.SH`）
- **数据同步中**：等待 5-10 分钟后重试
- **CLI 执行超时**：尝试减少查询天数或简化查询条件

## 开发指南

### 添加新的公共 Tool

1. 在对应的子目录（market/financial/indicator 等）创建新的 Go 文件
2. 实现 `types.Tool` 接口
3. 在 `internal/application/service/agent_service.go` 的注册 switch 里加分支，
   并把工具名写进 `config/builtin_agents.yaml` 的 `allowed_tools` —— 两处都要：
   白名单决定注册遍历范围，不在名单里的工具既进不了模型的 tool schema，调用也只会
   得到 "tool not found"。

### 添加新的 Zettaranc Tool

1. 在 `zettaranc/` 目录创建新的 Go 文件
2. 实现 `types.Tool` 接口
3. 通过 `HTTPClient`（`http_client.go`）调用 python-service 的对应端点
4. 在 `internal/application/service/agent_service.go` 的注册 switch 里加分支，
   并把工具名写进 `config/builtin_agents.yaml` 的 `allowed_tools` —— 两处都要：
   白名单决定注册遍历范围，不在名单里的工具既进不了模型的 tool schema，调用也只会
   得到 "tool not found"。`internal/agent/tools/zettaranc/whitelist_test.go` 会
   在两边不一致时变红。

### 测试

```bash
# 编译
go build ./internal/agent/tools/hithink_finance/...
go build ./internal/agent/tools/zettaranc/...

# 运行 WeKnora
make build
./server
```

## 智能体配置

在 `config/builtin_agents.yaml` 中已添加 `builtin-zettaranc` 智能体，配置了所有 hithink.finance 和 zettaranc tools。

## 后续扩展

### 公共 Tools

可以按树形结构继续扩展：

```
hithink.finance.
├── market.
│   ├── price.snapshot ✓
│   ├── price.historical ✓
│   ├── calendar
│   └── symbol
├── financial.
│   ├── statement.income
│   ├── statement.balance
│   ├── statement.cashflow
│   ├── indicator.profitability
│   └── valuation.snapshot ✓
├── indicator.
│   ├── trend.ma ✓
│   ├── momentum.kdj
│   ├── momentum.macd
│   └── volatility.atr
├── special.
│   ├── limit.limit_up_pool
│   ├── dragon_tiger.list
│   └── hot_stock.skyrocket
├── index.
├── fund.
└── futures.
```

### Zettaranc Tools

可以添加更多专属 tools：
- `zettaranc.diagnosis`: 持仓诊断
- `zettaranc.workflow`: 每日五步工作流
- `zettaranc.indicator.custom`: 自定义指标（沙漏评分、麒麟会）

## 参考

- [K 线数据集与板块/指数支持](./kline-datasets.md)：数据集路由（market vs index）、板块能画什么、指标公式归谁算、SQL 目录的生成方式
- zettaranc-skill: https://github.com/lululu811/zettaranc-skill
- WeKnora: https://github.com/Tencent/WeKnora
- hithink-finance: `~/.hithink-finance/`（本地数据，不随仓库分发）
