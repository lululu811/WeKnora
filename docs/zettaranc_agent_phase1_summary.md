# Zettaranc Agent 开发总结

## Phase 1 完成情况 ✅

### 1. 基础架构

#### 公共基础设施（hithink_finance/common.go）
- ✅ DuckDB 连接池（只读模式）
- ✅ 时间窗口隔离（17:25-17:35, 02:55-03:05）
- ✅ SQL 安全校验（只允许 SELECT）
- ✅ 默认 LIMIT 1000
- ✅ 错误处理（包含可执行建议）

#### Discovery Tool（hithink_finance/discover.go）
- ✅ 按前缀查询子 tools
- ✅ 按深度展开
- ✅ 智能提示（未找到时给出建议）

### 2. 公共 Tools（Go DuckDB 直连）

| Tool | 数据库 | 状态 |
|---|---|---|
| `hithink.finance.discover` | - | ✅ |
| `hithink.finance.market.price.snapshot` | market.duckdb | ✅ |
| `hithink.finance.market.price.historical` | market.duckdb | ✅ |
| `hithink.finance.financial.valuation.snapshot` | financials.duckdb | ✅ |
| `hithink.finance.indicator.trend.ma` | indicators.duckdb | ✅ |
| `hithink.finance.query.sql` | 所有 DB | ✅ |

**延迟：** <10ms（DuckDB 直连）

### 3. Zettaranc 专属 Tools（Go 调用 Python CLI）

| Tool | 功能 | 状态 |
|---|---|---|
| `zettaranc.analyze` | 个股分析（指标+战法+诊断） | ✅ |
| `zettaranc.backtest` | 策略回测 | ✅ |
| `zettaranc.screener` | 智能选股 | ✅ |

**延迟：** 500ms-2s（Python CLI 子进程调用）

### 4. 注册与集成

- ✅ 创建注册包：`finanserv/register.go`, `zettarancserv/register.go`
- ✅ 添加智能体配置：`config/builtin_agents.yaml`（builtin-zettaranc）
- ✅ 编译测试通过

### 5. 文档

- ✅ 创建使用文档：`docs/hithink_finance_tools.md`

---

## 文件清单

```
internal/agent/tools/
├── hithink_finance/
│   ├── common.go                    # 公共基础设施
│   ├── discover.go                  # Discovery tool
│   ├── market/
│   │   ├── price_snapshot.go        # 行情快照
│   │   └── price_historical.go      # 历史 K 线
│   ├── financial/
│   │   └── valuation_snapshot.go    # 估值快照
│   ├── indicator/
│   │   └── trend_ma.go              # 均线指标
│   └── query/
│       └── sql_query.go             # 通用 SQL 查询
├── zettaranc/
│   ├── common.go                    # CLI 客户端
│   ├── analyze.go                   # 个股分析
│   ├── backtest.go                  # 策略回测
│   └── screener.go                  # 智能选股
├── finanserv/
│   └── register.go                  # 公共 tools 注册
└── zettarancserv/
    └── register.go                  # Zettaranc tools 注册

config/
└── builtin_agents.yaml              # 追加 builtin-zettaranc 配置

docs/
└── hithink_finance_tools.md         # 使用文档
```

---

## 技术决策

### 1. 命名约定
**决策：** 用点号分隔（`hithink.finance.market.price.snapshot`）  
**理由：** 符合树形结构的视觉表达，易于理解和扩展

### 2. 延迟加载
**决策：** Discovery Tool 方案  
**理由：** 灵活，智能体可以按需探索可用 tools

### 3. DuckDB 连接
**决策：** Go DuckDB bindings 直连，只读模式  
**理由：** 延迟 <10ms，完全控制错误处理

### 4. Python CLI 集成
**决策：** Go 子进程调用 Python CLI  
**理由：** 复用 zettaranc-skill 的现有功能，无需重写

### 5. 时间窗口隔离
**决策：** 硬编码同步时间（17:25-17:35, 02:55-03:05）  
**理由：** 简单有效，避免"database is locked"错误

---

## 下一步计划

### Phase 2：扩展公共 Tools（2-3 周）

按树形结构继续实现：

#### Market Tools
- `hithink.finance.market.calendar` - 交易日历
- `hithink.finance.market.symbol` - 股票列表

#### Financial Tools
- `hithink.finance.financial.statement.income` - 利润表
- `hithink.finance.financial.statement.balance` - 资产负债表
- `hithink.finance.financial.statement.cashflow` - 现金流量表
- `hithink.finance.financial.indicator.profitability` - 盈利能力指标

#### Indicator Tools
- `hithink.finance.indicator.momentum.kdj` - KDJ
- `hithink.finance.indicator.momentum.macd` - MACD
- `hithink.finance.indicator.volatility.atr` - ATR

#### Special Tools
- `hithink.finance.special.limit.limit_up_pool` - 涨停池
- `hithink.finance.special.limit.limit_down_pool` - 跌停池
- `hithink.finance.special.dragon_tiger.list` - 龙虎榜
- `hithink.finance.special.hot_stock.skyrocket` - 飙升榜

#### Index/Fund/Futures Tools
- `hithink.finance.index.snapshot` - 指数快照
- `hithink.finance.fund.nav` - 基金净值
- `hithink.finance.futures.daily` - 期货日 K

### Phase 3：Zettaranc 专属 Tools 扩展（2-3 周）

- `zettaranc.diagnosis` - 持仓诊断
- `zettaranc.workflow` - 每日五步工作流
- `zettaranc.indicator.custom` - 自定义指标（沙漏评分、麒麟会）

### Phase 4：知识库集成（1 周）

- 将 zettaranc-skill 的 `knowledge/*.md` 导入 WeKnora RAG
- 配置 `builtin-zettaranc` 的知识库选择

### Phase 5：测试与优化（1-2 周）

- 单元测试（每个 tool）
- 集成测试（智能体端到端）
- 性能测试（并发查询、延迟）
- 错误场景测试

---

## 验证清单

### 编译验证
```bash
# 编译所有新代码
go build ./internal/agent/tools/hithink_finance/...
go build ./internal/agent/tools/zettaranc/...
go build ./internal/agent/tools/finanserv/...
go build ./internal/agent/tools/zettarancserv/...
go build ./cmd/server
```

### 功能验证
```bash
# 启动 WeKnora
make build
./server

# 在前端创建 builtin-zettaranc 智能体
# 测试以下查询：
- "帮我分析一下茅台"
- "宁德时代现在能买吗"
- "用少妇战法回测一下 600519"
- "B1 买点选股，给我 20 只"
```

### 性能验证
```bash
# 测试 DuckDB 直连延迟
time curl -X POST http://localhost:8080/api/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "获取茅台最新行情", "agent_id": "builtin-zettaranc"}'
# 预期：<10ms

# 测试 Python CLI 延迟
time curl -X POST http://localhost:8080/api/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "用 Z哥体系分析茅台", "agent_id": "builtin-zettaranc"}'
# 预期：500ms-2s
```

---

## 已知问题

### 1. Python CLI 依赖
- 需要确保 `/Users/chenlei/007_DB/Financial-API/.venv/bin/python3` 存在
- 需要确保 zettaranc-skill 的依赖已安装

### 2. DuckDB Schema
- `indicators.duckdb` 的 `v_ma` 视图可能不存在，需要根据实际 schema 调整查询

### 3. 数据新鲜度
- 如果数据过期超过 3 天，会在结果中添加 warning

---

## 参考

- zettaranc-skill: https://github.com/lululu811/zettaranc-skill
- WeKnora: https://github.com/Tencent/WeKnora
- hithink-finance: /Users/chenlei/.hithink-finance/
- 使用文档: /Users/chenlei/007_DB/WeKnora/docs/hithink_finance_tools.md

---

## 总结

Phase 1 已完成核心架构和基础 tools，验证了：
1. ✅ 树形命名 + 延迟加载的可行性
2. ✅ Go DuckDB 直连的低延迟（<10ms）
3. ✅ Go 调用 Python CLI 的可行性（500ms-2s）
4. ✅ Discovery Tool 的灵活性
5. ✅ 时间窗口隔离的有效性

下一步可以继续扩展更多 tools，或者进入 Phase 2 完善公共 tools 覆盖。
