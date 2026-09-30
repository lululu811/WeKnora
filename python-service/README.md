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
