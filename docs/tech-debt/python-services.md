# 技术债审计 — python-service / docreader

## 结论
1. **挖到一个可从宿主机直接利用的文件读取漏洞**：`/query/` 的「只读白名单」只禁关键字，不禁 DuckDB 的 `read_csv()` 表函数，而该端点在 compose 里默认无鉴权且端口 50052 已发布到宿主机 —— 未授权方可读容器内任意 CSV 文本文件（已在本地 DuckDB 上实测通过）。
2. **AGENTS.md 声明的「单位声明缺失 = 跳过该页」不变式，在规则抽取通道被反向实现**：`normalize_to_yuan(None)` 按 1:1 当元处理，且有一条测试把这个相反行为锁死；同一份代码里的分部表通道却正确地 skip 了 —— 两条通道对同一条不变式给出相反答案。
3. **`halo.fetch_external` 是整个 halo 唯一没有 offload 的 async 函数**：12 个外部数据桶全是同步 `urllib` + `time.sleep` 退避，直接在事件循环上串行执行，10+ 分钟量级，期间整个 FastAPI 服务的其它请求全部停摆。
4. **`zettaranc/filters.py` 复刻了 AGENTS.md 明文警告的 `period` 陷阱**：`ORDER BY period DESC` 让「最新一期」在全 A 5,571 只上固定选中 Q2，且不看 `fiscal_period` —— 已在本地库实测：`600062.SH` 的负债率取到的是 **2022 Q4** 而非 2026 Q2。
5. **docreader 的解析层质量明显高于 python-service**：SSRF 防护、PDF 下载体积上限、进程池、临时文件、gRPC 消息上限都到位，解析失败在 RPC 边界被拦截不会静默入库；主要债点在 PDF 并行渲染的 N 倍内存放大，以及 `main.py` 那个 507 行的 `zettaranc_screen`。
6. **测试覆盖与代码量的错配集中在 HTTP 边界**：23 个路由里 9 个（含全部 4 个 halo 路由和全部 K 线/图表路由）零 HTTP 测试，API key 鉴权逻辑零测试 —— 恰好是刚才那个 S1 漏洞所在的那一层。

---

## 发现

### [S1] `/query/` 只读白名单可绕过：任意文件读取 + 默认无鉴权 + 端口对外发布

- **位置**：`python-service/main.py:47-58`（正则定义）、`python-service/main.py:268-289`（`_read_only_sql`）、`python-service/main.py:605-655`（路由）、`docker-compose.yml:1069`（`ports: "50052:50052"`）、`docker-compose.yml:1080`（`WEKNORA_PY_SERVICE_API_KEY=${...:-}`）
- **证据**：白名单只做两件事 —— 正则 `_READONLY_STATEMENT` 确认以 `SELECT` 开头，`_FORBIDDEN_SQL` 禁掉 18 个写/pragma 关键字：
  ```python
  _FORBIDDEN_SQL = re.compile(
      r"\b(?:ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|CREATE|ALTER|DROP|"
      r"TRUNCATE|DELETE|INSERT|UPDATE|PRAGMA|SET\b|VACUUM|CALL)\b", re.IGNORECASE)
  ```
  DuckDB 的文件读取是**表函数**而非关键字，`read_csv` / `read_parquet` / `read_text` / `glob` / `sniff` 全部不在禁用名单里，而 `read_only=True` **不阻止**读文件（它只阻止写库）。实测（生产同款连接配置）：
  ```
  ALLOWED   SELECT * FROM read_csv('/etc/hosts') -> 8 rows
  ```
  且 `docker-compose.yml:1080` 默认 `WEKNORA_PY_SERVICE_API_KEY` 为空 → `main.py:127-128` 的 `if not API_KEY: return` 直接放行全部请求，同时 `1069` 把 50052 发布到宿主机。
- **影响**：宿主机上任何能连到 `localhost:50052` 的人（浏览器里的恶意网页走 CSRF/内网扫描、同宿主机的其它容器、误配的端口转发）无需任何凭据，即可读取容器内任意可解析为 CSV 的文本文件，并可借 `glob()` 枚举文件系统。项目根目录、`~/.hithink-finance/` 下的 sqlite/duckdb 旁挂文件、以及任何被 `COPY` 无关但可 `read_csv` 的文件都在射程内。因为返回体是 `{"success":true,"data":[...]}`，失败时还带 `failed to parse` 提示，攻击者可以逐文件探测。更严重的是该端点同时接受任意 `SELECT`，配合 `range()` 等函数可做资源耗尽式拒绝服务。
- **修复**：三步，都必须做。
  1. **关掉这个洞**：在 `_read_only_sql` 里加表函数黑名单 —— `read_csv|read_parquet|read_json|read_text|read_blob|glob| sniff|iceberg_|delta_|postgres_|sqlite_|mysql_scan` 一律拒绝；同时把 `FORBIDDEN` 从关键字黑名单换成**表函数白名单**（`SET allow_external_access=false` / `disabled_filesystems` 才是根本手段，见第 3 步）。
  2. **收敛爆炸半径**：给 `duckdb.connect` 传 `config={"allow_external_access": "false", ...}`（DuckDB 原生开关，比正则可靠得多，能一次性关掉所有 `read_*`/`*_scan`）。
  3. **默认开启鉴权**：把 `WEKNORA_PY_SERVICE_API_KEY` 的 compose 默认值从空改成 fail-fast（未设置时启动即退出，参照 `docreader/auth.py:39` 的 `TLSConfigError` 做法），或至少把 50052 从 `ports:` 改为 `expose:`（docreader 就是这么做的，`docker-compose.yml:445`）。
- **工作量**：S（半天，正则 + 一个 config 项 + 改 compose 两行）

---

### [S1] `fetch_external` 在事件循环上同步执行全部外网请求，最长可阻塞服务 10 分钟以上

- **位置**：`python-service/halo/analyze.py:1071-1158`（`async def` 但循环体内全是同步调用）、`python-service/halo/external.py:153-166`（`_Throttle.wait` 里 `time.sleep`）、`python-service/halo/external.py:242-303`（重试循环里 `time.sleep(backoff)`）
- **证据**：`fetch_external` 声明为 `async def`，但 12 个数据桶（`external.py` 全部 `extdata.*` 函数经 grep 确认无一个是 `async def`）直接 `out[name] = fn()` 内联调用：
  ```python
  for name, tier, fn in buckets:
      try:
          out[name] = fn()          # analyze.py:1144 — 同步 urllib，直接在事件循环上跑
  ```
  限速器 `_Throttle.wait`（`external.py:160-166`）持锁 `time.sleep`，退避也在 `external.py:268/293/302` 同步睡。halo 里**其它** async 函数都正确 offload 了（`analyze.py:1179` 的 `asyncio.to_thread`、`pipeline.py:419`），唯独这个漏了。
- **影响**：`/halo/score?include_external=true` 一次调用要串行打 12 个子域、约 20+ 次 HTTP。按各档限速（datacenter 0.5s、push2his 1.0s、ths 2.0s、nbs 3.0s、hkex 2.0s）× 3 次重试 × 20s timeout 估算，最坏 **10 分钟以上**全在事件循环上。期间该 worker 的**所有**请求（含 `/health` 探活、其它股票的分析）全部挂起；FastAPI 默认单进程，`/health` 超时会让 Docker HEALTHCHECK 判失败并**重启容器**（`docker-compose.yml:1092`），把一次慢外网调用放大成服务重启循环。附带问题：`_Throttle` 是按子域全局共享的，一个阻塞调用同时占住事件循环和该子域的限速锁，并发调用会在这里排队堆积。
- **修复**：把 `for name, tier, fn in buckets` 里的 `out[name] = fn()` 换成 `await asyncio.to_thread(fn)`（或整段 `buckets` 循环包进 `asyncio.to_thread`）。这一处改动即可，语义完全不变。顺带建议给 `analyze()` 整体加一个 `asyncio.wait_for` 超时上限，避免外网挂死拖垮端点。
- **工作量**：S（一行改动 + 一个超时）

---

### [S1] 「单位声明缺失 = 跳过该页」不变式在规则通道被反向实现，并被测试锁死

- **位置**：`python-service/halo/extractor.py:250-270`（`normalize_to_yuan`）、`python-service/halo/extractor.py:501-526`（`_scan_page` 传 `current_unit`，可为 `None`）、`python-service/tests/unit/test_halo_extractor.py:83-85`、对比 `python-service/halo/facts_finance.py:230-238`
- **证据**：AGENTS.md:117-119 写的是「a missing `单位：` declaration means *skip the page*, not a 1e6 error」。分部表通道（`facts_finance.py:232-235`）**照做了**：
  ```python
  unit_m = _UNIT_RE.search(page.text)
  if not unit_m:
      logger.info("p%d 主营构成表未找到单位声明，跳过该页分部抽取", page.page)
      continue                                  # 正确：skip
  ```
  但规则通道（资产/负债表走的正是 `fixed_assets` / `inventory` / `total_assets` 这些 HALO 六维输入）**没有**：`current_unit` 初值就是 `None`（`extractor.py:563`），`_scan_page` 在 `extractor.py:525-526` 把它原样交给 `normalize_to_yuan`：
  ```python
  resolved_unit = current_unit          # 可能是 None
  out_value = normalize_to_yuan(value, resolved_unit)
  ```
  而 `normalize_to_yuan`（`extractor.py:263-264`）对 `None` **按 1:1 处理**：
  ```python
  if unit is None or unit == UNIT_CNY:
      return val                          # 百万元页被当成元，差 1e6
  ```
  注释承认了理由（「猜错量纲比默认元危险得多」），但这恰好是与 AGENTS.md 相悖的赌注：`test_halo_extractor.py:502-505` 甚至有一条测试断言这个行为是对的。
- **影响**：`fixed_assets` / `construction_in_progress` / `inventory` / `intangible_assets` / `goodwill` / `total_assets` 六个字段是 HALO 六维里五维的输入。当一份年报的资产负债表标题页恰好没有「单位：」声明（PDF 抽取把「金 额 单 位」打散、声明落到续页等情况，`pipeline.py:223` 的注释自己就记录了「合并表续页 p57 不含『单位：元』」），抽出的值会**小 1e4~1e8 倍**。这个数不进对账（`reconcile` 只有 `total_assets` 一条口径，`reconcile.py:23-26`），错 1e6 倍时对账直接把它标成 `disputed` 反而是好的；**真正致命的是 `fixed_assets` / `inventory` 等没有对账口径的字段** —— 它们会被 `promote_by_pipeline`（`reconcile.py:638-644`）当作「同 scope 管线可信」升级成 `verified`，带着 1e6 倍的量纲错误进入 `score_halo` 的 `fixed_intensity` / `capex_intensity`。报告里这个数看起来完全正常（有 raw、有百分比、有评级），没有任何缺失标记。
- **修复**：`normalize_to_yuan` 在 `unit is None` 且值形似金额时**返回 `None` 而不是 `val`**（让该字段以「缺失」进入 `MissingInput`，由 `score_halo:222-227` 抛错并如实标注），并把 `test_halo_extractor.py:83-85` 的 `test_unknown_unit_defaults_to_1to1` 反过来断言。人数（`UNIT_PERSON`）路径不受影响，可保留 1:1。若要降低误伤面，可只在「值 > 1e12 且无单位声明」时判缺失（元级金额的上限约 1 万亿）。
- **工作量**：S（半天：改一处分支 + 改一条现有测试的断言方向 + 补一条回归）

---

### [S2] `zettaranc` 风险筛选复刻 `period` 陷阱，实测取到 4 年前的资产负债表

- **位置**：`python-service/zettaranc/filters.py:124-143`（`build_risk_filter_sql`）、`python-service/zettaranc/filters.py:146-160`（`build_profit_filter_sql`）；正确写法见 `python-service/halo/reconcile.py:203-233`
- **证据**：两条 SQL 都用同一个窗口排序：
  ```python
  ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY period DESC) AS rn
  ```
  而 `v_balance_sheet.period` 的取值是 `('annual', 48804)` 与 `('quarterly', 177750)` —— 字典序下 `'quarterly' > 'annual'`，所以 `period DESC` **永远选到 quarterly 行，且完全不看 `fiscal_period`**。实测结果：
  ```
  Rows the filters.py ranking selects as "latest period":
     ('quarterly', 'Q2', 5569)   ('quarterly', 'Q3', 1)   ('quarterly', 'Q4', 1)
  ```
  与正确排序（`ORDER BY period_end_ms DESC, fiscal_year DESC, fiscal_period DESC`）逐票比对，**`600062.SH` 选到了 `2022 Q4`，正确答案是 `2026 Q2`** —— 相差 4 个会计年度。AGENTS.md:79-84 已经把这个坑写成「Two traps that have already cost real debugging」，`reconcile.py:121` 也有回归测试 `test_sql_uses_full_join_key_not_period_end_ms` 专门锁住完整键。
- **影响**：`/zettaranc/screen` 的 `max_debt_ratio` / `min_current_ratio` / `max_receivable_ratio` / `require_profit` 四个筛选项直接用这批数据，用户设「资产负债率 < 50%」却可能用 4 年前的负债率做判断。当前只有 1 只票数据异常，所以是潜伏而非正在发火；但**下一次财务数据全量重算后，一旦某只票的 quarterly 数据缺失回落到更早年份（退市/停牌/新上市常见），排序会静默选中那条陈旧记录**，且不产生任何日志或错误。`require_profit` 同理，会用陈旧的 `parent_holder_net_profit` 判断是否盈利 —— 亏损股可能被判为「最新期盈利」而通过筛选。
- **修复**：两条 SQL 的 `ORDER BY` 改为 `ORDER BY period_end_ms DESC, fiscal_year DESC, fiscal_period DESC`（照抄 `reconcile.py:221-223` 已验证的键）。同时在 `python-service/tests/unit/` 下补一条结构测试，断言这两条 SQL 的 `ORDER BY` 里不含裸 `period DESC` —— 与 `test_halo_reconcile.py:121` 同款，让下一次改动无法再退化。
- **工作量**：S（两行 SQL + 一条结构测试）

---

### [S2] 9/23 路由零 HTTP 测试，API key 鉴权逻辑零测试

- **位置**：`python-service/tests/e2e/test_http_api.py`（43 个测试，覆盖 10 个路由）、`python-service/main.py:1595/1738/1894/1954/2091/2224/2374`（K 线/行情/标注/形态/画像/搜索/指标）、`python-service/main.py:1366/1429/1475/1553`（4 个 halo 路由）、`python-service/main.py:125-137`（`require_api_key`）
- **证据**：全仓 grep 无任何测试引用 `/api/kline`、`/api/quotes`、`/api/annotate`、`/api/chart-pattern`、`/api/stock-profile`、`/api/symbols/*`、`/api/indicators`、`/halo/*`；`grep -rn "API_KEY\|require_api_key\|Authorization" python-service/tests/` 返回**空**。e2e 里覆盖的只有 `/health`、`/query/`、`/query/databases`、`/cache/*`、`/zettaranc/{analyze,scan,screen,four-bricks,health}`。
- **影响**：这正是本次 S1 漏洞所在的层 —— `/query/` 的白名单逻辑有测试（`test_query_rejects_writes_and_multiple_stmts`），但**表函数那条绕过路径没有**，所以 `read_csv` 一直没被发现。同一层缺测试的还有：鉴权（设了 key 时 401/403 分支从未被执行过，改坏不会红）、halo 四个路由的请求模型校验（`report_type` 白名单、`scope` 校验、`only_verified` 默认值）、以及 `/api/*` 系列的双路由别名（`/api/kline` 与 `/kline` 注册了两次，任何一处改漏不会被发现）。这批端点是**前端 KLineChart Pro 直连**的（`docker-compose.yml:29-32` 注释确认 nginx 反代），线上出问题只能靠用户报。
- **修复**：在 `test_http_api.py` 里补三类最小测试：(1) `/query/` 的表函数绕过用例（`read_csv`、`glob`、`read_parquet` 各一条，断言 4xx）—— 这条同时是 S1 修复的防回归；(2) `require_api_key` 的 401/403/放行三态（用 monkeypatch 改 `main.API_KEY`）；(3) 9 个未覆盖路由各一条 smoke（发最小合法请求，断言非 5xx）。
- **工作量**：M（1-3 天，主要是 (3) 的参数构造）

---

### [S2] `zettaranc_screen` 单函数 507 行，六类关注点混在一起

- **位置**：`python-service/main.py:675-1181`（`zettaranc_screen`，507 行）
- **证据**：AST 统计 `>100` 行的函数：`main.py` 有 5 个，最大就是这个。它同时承担：策略解析与校验、全市场清单取数、涨停池取数、板块筛选（精确/模糊两条分支）、指标与价量取数、超采样、`asyncio.to_thread` 调用纯函数、财务风险三段独立筛选、ST 名单、结果整形。全仓库没有任何针对该函数的单函数测试 —— e2e 只有一条 `test_zettaranc_screen_returns_ranked_stocks` 断言分数是降序的。
- **影响**：板块筛选那段（`main.py:763-790`）有一个已知的自耦合陷阱 —— 板块限定必须发生在取数**之前**，一旦有人把过滤挪到内存里做，「半导体 + oversold_combo」会从 188 只退化到先扫全市场 5,571 只再过滤（36~51 秒，注释里记着这个数）。507 行 + 无单测意味着这类「顺序敏感」的性能与正确性约束完全靠注释维持。板块匹配还分精确/模糊两条 SQL 分支（`filters.py:47` / `filters.py:67`），两者的行为差异没有测试锁定。
- **修复**：不重写，只做两件事 —— (1) 把「板块候选集解析」（板块名 → 股票代码列表，约 60 行）抽成独立函数并补测试；(2) 把「财务风险筛选」（约 90 行）抽成独立函数，它已经有清晰的入参/出参契约（`codes` 进、`kept` + `risk_rejects` 出）。抽完主函数降到约 350 行，两个新函数可单测。保持串行顺序不动。
- **工作量**：M（1-3 天）

---

### [S2] PDF 并行渲染每个 worker 进程把整个 PDF 读进内存，N 倍内存放大

- **位置**：`docreader/parser/pdf_parser.py:1128-1133`（`_render_pool_init`）、`docreader/parser/pdf_parser.py:1192-1199`（`ProcessPoolExecutor`）、`docreader/config.py:109-111`（`pdf_render_parallelism` 默认 `min(4, cpu_count)`）
- **证据**：
  ```python
  def _render_pool_init(pdf_path: str) -> None:
      global _WORKER_RENDER_DOC
      import pypdfium2 as pdfium
      with open(pdf_path, "rb") as f:
          _WORKER_RENDER_DOC = pdfium.PdfDocument(f.read())   # 每 worker 一份完整副本
  ```
  默认 4 个 worker（`config.py:109-111`）。注意临时文件（`pdf_parser.py:1183-1187`）确实做对了 —— 走文件而非 pickle 传字节，避免了 IPC 序列化，这是好设计；但 pool initializer 又把文件**整份读回内存**给每个 worker，等于抵消了一半收益。
- **影响**：`grpc_max_file_size_mb` 默认 50MB（`config.py:82-86`），一次 50MB 的扫描版 PDF 会让常驻进程持有一份（`parse_file` 收到的 `content`）**加上** 4 个 worker 各一份完整的 `bytes` + `PdfDocument` 解析结构。`pypdfium2` 的 PdfDocument 内部还会展开页面对象，实际占用常是源文件的 2-3 倍。峰值可达 `50MB × 5 × ~2.5 ≈ 600MB`，而 docreader 与 python-service 共享一台机器（`docker-compose.yml` 同一 network，app 侧还带 3g mem_limit 的 python-service）。多份扫描版 PDF 并发进来时，OOM 杀掉 docreader 进程 → 所有在途解析丢失。
- **修复**：`_render_pool_init` 里把 `f.read()` 换成 mmap 或路径直读（`pdfium.PdfDocument(pdf_path)` 若该版本支持路径，否则 `open(path,'rb').read()` 换成把内容交给进程继承的 `mp_context` 共享内存）。最小改动是给进程池加一个 `max_workers` 与文档体积的联动：单文件超过 `grpc_max_file_size_mb / 2` 时直接跳过并行、回落串行（`_render_pages_parallel:1172-1173` 已经有 `workers <= 1` 的回落分支，加一条体积判断即可）。
- **工作量**：S（半天）

---

### [S3] 19 处把原始异常对象拼进 HTTP 错误体，DuckDB 错误会回显完整 SQL

- **位置**：`python-service/main.py:645`（最严重）、`python-service/main.py:700/781/826/955/966/979/1206/1287/1311/1379/1393/1445/1497/1562/1702/1832/1931/2009`
- **证据**：
  ```python
  except Exception as exc:
      raise fail(400, f"查询失败：{exc}") from exc      # main.py:645
  ```
  DuckDB 的异常文本会回显整条语句（实测：`Catalog Error: Table with name t does not exist! ... LINE 1: SELECT nonexistent_col FROM t`），所以这条会把**用户提交的完整 SQL** 原样送回响应体。其余 18 处是 503，泄露的是内部路径与表名（`main.py:1379` 的「事实库不可用：{exc}」会带出 `~/.hithink-finance/halo.sqlite`）。
- **影响**：不构成直接漏洞（用户提交什么就回显什么），但 503 那批把内部文件路径、数据库文件名、列名暴露给了**任何能访问端点的人** —— 这在没有鉴权的 kline/quotes 路由上尤其宽泛，攻击者可据此绘制内部存储布局。`main.py:645` 那条还让调试变难：真正的错误（表不存在）和用户笔误混在一起，缺少一个稳定的错误码。
- **修复**：给 `fail()` 加一个 `code` 字段（如 `sql_syntax` / `datasource_unavailable` / `store_unavailable`），错误体只回 code + 一句人话；完整异常只进 `logger`。19 处机械替换，一轮即可完成。
- **工作量**：S（半天）

---

### [S3] `/halo/verify` 接受任意数值的维度分数，无 0–10 边界校验

- **位置**：`python-service/main.py:1465-1470`（`HaloVerifyRequest.scores: Dict[str, float]`）、`python-service/main.py:1514-1515`（`scores[k] = float(v)`）、`python-service/halo/scoring.py:423-432`（`risk_contrib = (10.0 - risk) * RISK_WEIGHT`）
- **证据**：请求模型只校验**维度名的集合**（`main.py:1487-1492`：多维报 422、少维报 422），对**分值本身零校验**。`scoring.py:423-432` 直接拿它算风险贡献：
  ```python
  risk = float(scores["risk"])
  risk_contrib = (10.0 - risk) * RISK_WEIGHT
  total += risk_contrib
  ```
  传 `risk = -1e9` 会让 `risk_contrib` 变成 `+1e8`，`total` 直接爆掉。`recalc_comprehensive` 的 `ok` 判定（`scoring.py:396-402`）只检查维度是否齐全，不检查取值域。
- **影响**：这个端点的设计意图是「Python 复算校验 LLM 的算术」，但校验器对 LLM 给的分值本身不设防。一个幻觉出 `risk: 100` 的模型会让该维度贡献 `-9.0`，`total` 掉到负数，`rating_10` 返回「弱」，而 `verify_comprehensive` 会把 LLM 的声明值与这个离谱的复算值对比后判 `ok` —— 校验器反而**确认**了一个明显错误的分数。模型传 `risk: 0`（最安全）则拿到满分贡献 `(10-0)×0.10 = 1.0`，等于白送 1 分。`test_halo_scoring.py` 有 27 个测试覆盖了容差、档位、权重和边界，但**没有一条测非法分值**。
- **修复**：在 `HaloVerifyRequest.scores` 上加 pydantic 约束（`Dict[constr(pattern=...), float]` 配 `Field(ge=0, le=10)` 的 validator），或在 `main.py:1514` 循环里加一次 `0 <= v <= 10` 校验并返回 422。同时在 `recalc_comprehensive` 里对超域值也做一次防御（返回 `ok: False` 而非静默接受），因为 `scoring` 是可被非 HTTP 调用方直接使用的。
- **工作量**：S（半天）

---

### [S3] docx/pdf 解析的 `Image.open` 资源未显式关闭，依赖 GC 回收

- **位置**：`docreader/parser/docx_parser.py:372`、`docreader/parser/docx_parser.py:1518`、`docreader/parser/pptx_media.py:70`
- **证据**：`Image.open(BytesIO(image_blob)).convert("RGBA")` —— `.convert()` 返回新对象，原 `Image` 上的文件指针依赖引用计数回收；`pptx_media.py:70` 则是 `img = Image.open(io.BytesIO(data))` 后直接返回 `img`，把关闭责任交给了调用方。仓库里 `docreader/utils/tempfile.py` 提供了规范的 `TempFileContext` 且有 `test_tempfile_context.py`，说明这个项目知道怎么做资源管理，只是图片路径上没统一。
- **影响**：单次不严重（内存对象，靠 GC 释放）。但在 `docx_parser.py:147-150` 的 `max_workers = min(4, cpu_count)` 线程池下，几十页图片同时转换时，未关闭的 PIL 对象会让文件描述符/内存峰值高于预期。属于「知道正确做法但没贯彻」的一致性问题。
- **修复**：三处改成 `with Image.open(...) as im:` 或显式 `.close()`，Pillow 的 `Image` 上下文管理器是标准做法，改动 3 行。
- **工作量**：S（< 半天）

---

## 量化

| 指标 | python-service | docreader |
|---|---|---|
| 生产代码行（不含测试） | 16,571 | 11,235（不含 proto） |
| 测试代码行 | 9,265 | 4,602 |
| 测试文件数 | 32 | 27 |
| 测试/生产行比 | 0.56 | 0.41 |

**坏味道命中计数**

- `>100` 行的函数：`main.py` 5 个（最大 `zettaranc_screen` 507 行）、`analyze.py` 1 个（231 行）、`pdf_parser.py` 1 个、`docx_parser.py` 5 个
- `except Exception: pass` / 等价空吞：5 处（`cache.py:160/169/197`、`duckdb_source.py:127/247`），全部在「关连接失败」这类无害路径，可接受
- 裸 `except:`：0 处
- `eval` / `exec` / `pickle` / 不安全 `yaml.load`：0 处 —— 外部响应没有任何一条喂给动态执行（cninfo 与 eastmoney 全部走 `json.loads`）
- `subprocess(..., shell=True)`：0 处
- 无 `with` 的 `urlopen`：0 处（`_urlopen` 的 3 个调用点 `cninfo_source.py:426/452/792` 全部正确关闭）
- 原始异常拼进 HTTP 错误体：19 处
- `asyncio` 函数中未 offload 的同步阻塞调用：1 处（`analyze.py:1144`），同模块另有 1 处正确 offload（`analyze.py:1179`）

**halo 不变式逐条核实结果**

| AGENTS.md 声明的不变式 | 代码核实 | 回归测试 |
|---|---|---|
| Python owns every number | ✅ 成立。`build_ai_slots` 七个维度 `score` 恒为 `None`（`analyze.py:260`），Markdown 留 `{{xxx_score}}` 槽位（`analyze.py:481`），综合分由 `recalc_comprehensive` 重算 | ✅ `test_halo_scoring.py` 27 例 + `test_halo_report_shape.py` |
| 风险项符号 `(10 − risk) × 0.10` | ✅ 成立。`scoring.py:424` 与 `AGENTS.md:112` 注释完全一致 | ✅ `test_risk_enters_as_inverted_positive_contribution` |
| Missing ≠ zero | ✅ 成立。`score_halo:222-227` 缺任一输入即抛 `MissingInput`，不返回部分分；`score_growth:370-372` 按实际权重归一 | ✅ `test_halo_refuses_when_any_input_missing`（参数化）+ `test_growth_renormalizes_by_used_weight` |
| analyze() 恒返回同一 shape | ✅ 成立。早返回路径（`analyze.py:1232-1265`）逐字段补齐，含 `announcements`/`markdown` | ✅ `test_no_period_anywhere_returns_full_shape_not_exception` 逐 key 断言 |
| 对账 disputed 不被提升 | ✅ 成立。`promote_by_pipeline:638-644` 的升级条件显式含 `status == STATUS_PENDING`，disputed 不在其中；且升级要求该 scope 全部对账口径通过 | ✅ `test_verified_is_demoted_when_reconcile_fails` |
| 单位声明缺失 = skip 该页 | ❌ **规则通道违反**（`extractor.py:263-264` 按 1:1 处理），分部通道正确（`facts_finance.py:232-235`） | ❌ 有一条测试锁死了**相反**行为（`test_halo_extractor.py:83-85`） |
| cninfo 限速 + 指数退避 | ✅ 成立。限速器按间隔共享（`cninfo_source.py:149-161`，防实例数乘算绕过），锁在 sleep 期间不释放；退避 0.8→1.6→3.2 且 4xx 不重试 | ✅ `test_halo_cninfo.py` 覆盖 |
| 外部响应不喂 eval/pickle | ✅ 成立。全模块 0 处 `eval`/`exec`/`pickle` | — |
| Eastmoney 子域 WAF 独立 | ✅ 成立。`external.py:82-140` 12 个子域各占一档，403 与连续 `RemoteDisconnected` 都判定为 IP 级封禁并走降级（`external.py:255-260`、`271-288`） | ✅ `test_halo_*` 各文件 |

**结论：9 条声明不变式中 8 条成立且有回归测试守护，1 条（单位声明）在规则通道被违反且被测试反向锁死。**

**docreader 防护层核实（均已到位，不构成债项）**

- SSRF：`utils/ssrf.py` 私有网/保留 IP/CIDR/端口/白名单四层校验，有 `test_ssrf.py`、`test_ssrf_proxy.py` 共 2 组测试
- 上传体积：gRPC `max_receive_message_length` 默认 50MB（`main.py:303`、`config.py:82-86`）
- PDF 下载体积：`cninfo_source.py:795-808` 双保险（Content-Length 预检 + chunked 累计），超限删半截文件
- 解析失败：空内容在 gRPC 边界被拦（`main.py:198-201`、`247-251`），不会静默入库
- 网络暴露：docreader 用 `expose:` 而非 `ports:`（`docker-compose.yml:445`），不发布到宿主机；TLS 配置缺失时 fail-fast（`auth.py:39`）
- 临时文件：`utils/tempfile.py` 统一封装，`__exit__` 清理，`test_tempfile_context.py` 覆盖
- 子进程超时：`doc_parser.py:94` 对 `soffice` 设 60s timeout，`test_sandbox_executor.py` 有 5 个超时路径测试

**风险排序（按「线上确定性故障 × 修复成本」）**

1. S1 `/query/` 文件读取 —— 已可利用，改动最小，**建议优先**
2. S1 `fetch_external` 阻塞事件循环 —— 单行改动，可触发容器重启循环
3. S1 单位不变式违反 —— 影响财务数据正确性，但触发条件依赖具体 PDF
4. S2 `period` 陷阱 —— 当前 1 只票异常，随数据重算可能扩大
5. S2 测试覆盖缺口 —— 是前 4 条都逃过 CI 的共同原因
