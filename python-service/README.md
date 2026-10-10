# python-service

金融数据查询与技术分析服务。数据来自本地 DuckDB 库（`~/.hithink-finance/*.duckdb`），
对外提供只读 SQL 查询和 Zettaranc 技术分析。

```bash
# 本地跑
DB_DIR=~/.hithink-finance python3 main.py     # 默认 :50052

# 容器
docker compose up -d --build python-service
pytest tests -q                              # 30 unit + 59 e2e
```

---

## HTTP 契约

**成功 2xx，失败 4xx/5xx。** 错误体同时给出 `success: false` 和 `error`：

```json
{"success": false, "error": "数据库 'foo' 不存在", "detail": "..."}
```

业务错误（参数错、SQL 非法、标的格式错）= 4xx；依赖不可用 = 5xx。
> 2.0 版本所有错误都返回 `200 + {"success": false}`，对 ingress、负载均衡、
> 监控全都不可见。现在是破坏性变更，调用方请改判 HTTP 状态码。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 数据源健康。全部 healthy → 200；否则 `degraded` → 200 / `unhealthy` → 503 |
| `/` | GET | 服务信息与端点清单 |
| `/query/databases` | GET | 已注册的 DuckDB 数据源 |
| `/query/` | POST | 只读 SQL 查询（**需鉴权**） |
| `/cache/stats` | GET | 内存缓存条目数与命中统计 |
| `/cache/clear` | POST | 清空内存 + Redis 两级缓存 |
| `/zettaranc/screen` | POST | 全市场选股 |
| `/zettaranc/analyze` | POST | 趋势 + 量价 + 形态 + 支撑阻力 |
| `/zettaranc/scan` | POST | 技术信号扫描 |
| `/zettaranc/health` | GET | 真去查一次 indicators，不是硬编码 healthy |

> **`/zettaranc/analyze` 不再由 agent 工具调用。** 2026-10-05 起主服务器的
> `zettaranc.analyze` 复合工具已删除：单票分析改由四个原子工具
> `hithink.finance.analysis.trend` / `.volume` / `.pattern` / `.levels` 各回答一段，
> 由模型自行组合，因此本端点在仓库内已无调用方。它作为**服务 API 保留** ——
> 外部调用方与前端不受影响。若要把综合分析重新暴露给 agent，请连同
> `internal/config/zettaranc_prompt_verify_test.go` 的守卫与
> `config/builtin_agents.yaml` 的白名单一起改，只加回工具会让两边漂移。

### `/query/`

```jsonc
POST /query/
{
  "db": "market",                                  // 必须在白名单内
  "sql": "SELECT thscode FROM dim_symbol WHERE thscode = ?",
  "params": ["600519.SH"],                         // 绑定参数，按顺序消费 ?
  "limit": 1000                                    // 1..MAX_QUERY_ROWS
}
```

保护措施（每一条都对应一个曾经能被绕过的缺陷）：

- **只允许单条 SELECT**。`ATTACH`/`COPY`/`PRAGMA`/`SET` 等关键字一律拒绝。
- **SQL 注释先剥掉**。旧实现判断 `"LIMIT" not in sql.upper()`，一个 `-- limit`
  注释就能让限制整个失效（实测 `limit=2` 返回了 5571 行）。
- **无条件外套一层 LIMIT**：`SELECT * FROM (<你的 SQL>) LIMIT n`。子查询、
  CTE、已有 LIMIT 全部被夹紧。
- **行数硬上限** `max_rows`（默认 100000）。`indicators.duckdb` 有 12GB，
  一条 `SELECT *` 就能把进程撑爆。
- **`WEKNORA_PY_SERVICE_API_KEY`** 设置后需 `Authorization: Bearer <key>`。
- **读到写到一半的库文件 = 503，不是 400**。宿主机 ETL（`indicators_sync.py` /
  `daily_sync`）写库时，容器里的长驻连接可能在某次 checkpoint 中间被换掉，读到
  写到一半的元数据块（`Serialization Error: Failed to deserialize: field id
  mismatch, expected: N, got: M`）。`datasources/duckdb_source.py` 会**换一条连接
  重试**（最多 `CORRUPTION_RETRIES`，只对这类错误，笔误不动连接）；仍失败才把
  503 交出去，并在消息里说明是 ETL 写盘中间态 —— 重试即可，不要去改 SQL。

### `zettaranc` 三兄弟

三者的**行序契约**（`zettaranc/utils.py` 顶部有完整说明）：
`fetch_*` 用 `ORDER BY date DESC`，所以 `rows[0]` 是最新一根 K 线；
`find_swings` 返回的下标是升序，因此**列表开头是最近的摆动点**。
这两个方向叠加，历史上被读反过，导致趋势方向整体反转。

数据不足时不再静默：

```jsonc
{
  "thscode": "603448.SH",
  "requested_days": 120,     // 你要的
  "days": 14,                // 实际拿到的
  "trend": null,             // 段缺失时字段仍然在，只是 null
  "insufficient_data": ["trend: 需要 20 根 K 线，仅 14 根"],
  "complete": false
}
```

指标缺失一律保留成 `None`，**不用 0 冒充**。SQL 里的 `COALESCE(col, 0)`
会把"没算出来"变成 `RSI6 = 0 → RSI6超卖`，于是完全没有指标数据的标的
被报成"偏多"。

---

### 大盘侧：`/api/market/snapshot` 的 `market_state` 块

`zettaranc/market_state.py` 算五维打分（趋势 / 宽度 / 量能 / 波动 / 短期热度），
结果挂在 `/api/market/snapshot` 的 `market_state` 字段上。

**⚠️ 返回值里 `tradable` 恒为 `false`，不要把它当买/卖方向信号。**

回测证据（`scripts/backtest_market_state.py`，基准沪深300，1103 个交易日）：

| 策略 | 总收益 | 最大回撤 |
|---|---|---|
| 基准满仓 | +1.13% | -29.73% |
| 高分满仓（既定方向） | -7.77% | -20.79% |

`composite` 的**逐年 IC 反号 4 次**（2022 -0.52 / 2023 +0.07 / 2024 -0.22 /
2025 +0.33 / 2026 -0.15），是随机游走而非"某几年特殊"，因此没有可用的
regime 过滤条件。方向不可预测这件事，后端在返回值里明说，前端能直接看到。

`exposure_hint` 是**仓位暴露参考**（0 / 0.5 / 1.0），不是方向建议：

| | 基准满仓 | 方向自适应 |
|---|---|---|
| 总收益 | +1.13% | +9.47% |
| 最大回撤 | -29.73% | -16.96% |

但逐年看它在 2024 年把 +12.82% 的涨幅压到 +0.01% —— **收益来自降暴露，
不是方向判得准**。`exposure_hint.note` 字段里写死了这个代价。

`short_term_signal` 是唯一跨窗口方向稳定的维度（20/40/60/120 日 IC 全为正），
但 60 日 IC 仅 +0.123，`confidence` 恒为 `weak`。

**注意 `heat_score` 是反向分**：高 = 冷、低 = 热，与 `raw_heat` 方向相反。
（曾把 score 直接当热度解读，指数跌 5.5% 的冷市被报成"偏热、回落风险大"。）

宽度指标来自本地缓存 `data/market_breadth.csv`（2320 个交易日，2017-03 起），
由 `scripts/build_breadth_cache.py` 生成。

**⚠️ 这个缓存没有任何定时任务会重建它。** 数据同步后宽度指标不会自动跟新，
而 composite 里宽度+量能占 0.25 权重。所以响应里带 `breadth_freshness`：

```jsonc
"breadth_freshness": {
  "last_date": "2026-09-30",
  "lag_trading_days": 0,     // 缓存落后指数数据的天数
  "days_behind_today": 6,    // 缓存最后一天距今的天数
  "stale": false,
  "note": "宽度缓存到 2026-09-30（落后 0 天，正常）；缓存最后一天距今 6 天（阈值 12 天）"
}
```

两层检查各有必要：只看"缓存 vs 指数"时，**两者同步滞后查不出来**（实测
2026-10-06 时 index 库和缓存都停在 09-30，lag=0 判为新鲜，实际已落后 6 天）。
阈值 12 个自然日覆盖 A 股长假（春节/国庆最长 9 天不开盘）。

过期时前端会显示黄色警示条。修复：

```bash
uv run --with duckdb python scripts/build_breadth_cache.py   # 约 60s，2000 万行窗口函数
```

回测与诊断脚本（都不写库，只读）：

```bash
uv run --with duckdb python scripts/backtest_market_state.py  # 主回测 + 分维度IC归因
uv run --with duckdb python scripts/backtest_adaptive.py      # 方向自适应
uv run --with duckdb python scripts/diagnose_regime_shift.py  # 逐年IC稳定性诊断
```

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_DIR` | `~/.hithink-finance` | DuckDB 文件目录 |
| `PORT` | `50052` | 监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `REDIS_HOST` | 空 | 空 = 不启用 Redis（注意不是 `REDIS_ADDR`） |
| `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD` | 6379 / 0 / 空 | |
| `WEKNORA_PY_SERVICE_API_KEY` | 空 | 空 = `/query/` 不鉴权 |
| `DUCKDB_MAX_CONCURRENT` | `4` | 每个 DuckDB 源的准入并发 |
| `CACHE_MEMORY_SIZE` | `1000` | 内存层条目上限 |
| `CACHE_DEFAULT_TTL` | `300` | 两级缓存共用 TTL（秒） |
| `WEKNORA_MAX_QUERY_ROWS` | `100000` | 单次查询行数上限 |

完整清单见仓库根目录 `.env.example`。

---

### `/zettaranc/screen`

```jsonc
POST /zettaranc/screen {"strategy": "oversold_combo", "limit": 20}
// => {"success":true, "strategy":"oversold_combo", "universe":5571, "scanned":5571,
//     "incomplete":38, "matched":880, "unsupported_signals":[], "stocks":[...]}
```

**全市场覆盖。** 标的池是 `dim_symbol.asset_type = 'a-share'` 的全量、按
thscode 排序。早期版本硬编码 `LIMIT 100` 且没有 `ORDER BY`，5571 只 A 股
只扫了 100 只，而且全是 `600xxx.SH` —— 创业板、科创板、深市一只都没扫到。

**集合式，不是 N+1。** 逐只调 `scan_patterns` 是 5571 次往返，实测 36~51 秒。
现在两条查询（清单 / 指标快照，最近 10 天与 `/zettaranc/scan` 同口径），
Python 侧复用 `detect_signals` 判定，**全市场约 0.4~0.7 秒**。
`tests/unit/test_screener.py` 里有对账：选股池里任意一只的判定结果，
必须和单独调 `/zettaranc/scan` 完全一致。

`unsupported_signals` 回报规则里引用了扫描器算不出来的信号名。
`v_indicators_daily` 只有指标、没有点位（`close`/`vol` 在 market 库的
`v_daily_qfq` 里，而两个库是独立的 read-only DuckDB 文件、不能 ATTACH），
所以 `放量突破`、`Donchian上轨突破` 这类依赖价格的信号在选股池里不可用 ——
接口会明确说出来，而不是像从前那样静默地永远匹配不到。

`incomplete` 是核心指标为 NULL 被剔除的标的数。空数据标的**不进选股池**。

---

## 测试

```bash
pytest tests/unit -q   # 48 个：行序契约、信号门控、缓存 TTL、集合式选股
pytest tests/e2e  -q   # 29 个：活体 HTTP，需要服务在跑
WEKNORA_PY_SERVICE_URL=http://host:50052 pytest tests/e2e -q
```

`Test*Regression` 类逐条钉住已修的缺陷，每个类名写明对应的 BUG 编号；
回归会直接红，不会静默产出反向的交易信号。

---

## 与 Go 侧的关系

Go 侧 `internal/agent/tools/hithink_finance/` 是**本服务的客户端**
（`QueryDuckDB` 通过 HTTP 调 `/query/`），不是独立的第二实现。
两边的分析逻辑曾经是逐行翻译的关系，包括同样的错误；现在两边都有
对应的回归测试（Go 侧见 `analysis/parity_test.go`），改一处要同步另一处。
