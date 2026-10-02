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

这些 tools 调用 Python CLI 进行复杂计算，延迟较高（500ms-2s）。

### zettaranc.analyze

使用 Z哥交易体系对单只股票进行全面分析。

**参数：**
- `thscode`: 同花顺股票代码
- `days`: 分析天数（默认 120）

**返回：**
- 技术指标：KDJ、MACD、RSI、BBI、白线黄线等
- 波浪分析：三波理论阶段判断
- 麒麟会：阶段和置信度
- 战法信号：30+ 种战法（超卖组合/B2、少妇战法、四块砖等）
- 综合诊断：买卖点判断

**示例：**
- 分析茅台：`thscode="600519.SH"`
- 分析宁德时代（120 天）：`thscode="300750.SZ", days=120`

### zettaranc.backtest

使用 Z哥交易体系进行策略回测。

**参数：**
- `strategy`: 策略名称（shaofu, multi, b1, b2, sb1）
- `thscode`: 同花顺股票代码
- `days`: 回测天数（默认 250）

**返回：**
- 收益率、夏普比率、最大回撤
- 胜率、盈亏比
- 交易记录
- 资金曲线

**示例：**
- 少妇战法回测茅台：`strategy="shaofu", thscode="600519.SH", days=250`

### zettaranc.screener

使用 Z哥交易体系进行智能选股。

**参数：**
- `strategy`: 选股策略（oversold_combo, B2, SB1, shaofu, limit_up, anomaly）
- `limit`: 返回数量（默认 20，最大 100）

**返回：**
- 按评分排序的候选股票列表
- 每只股票的匹配理由
- 技术指标快照

**示例：**
- 超卖组合选股：`strategy="oversold_combo", limit=20`

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
3. 在 `finanserv/register.go` 中注册

### 添加新的 Zettaranc Tool

1. 在 `zettaranc/` 目录创建新的 Go 文件
2. 实现 `types.Tool` 接口
3. 使用 `CLIClient` 调用 Python CLI
4. 在 `zettarancserv/register.go` 中注册

### 测试

```bash
# 编译
go build ./internal/agent/tools/hithink_finance/...
go build ./internal/agent/tools/zettaranc/...
go build ./internal/agent/tools/finanserv/...
go build ./internal/agent/tools/zettarancserv/...

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

- zettaranc-skill: https://github.com/lululu811/zettaranc-skill
- WeKnora: https://github.com/Tencent/WeKnora
- hithink-finance: `~/.hithink-finance/`（本地数据，不随仓库分发）
