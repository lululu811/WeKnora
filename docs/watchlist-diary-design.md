# 个股追踪增强设计：入池理由 / K线 / 每日观察日记

> **状态：已实现（2026-10-02）。** 本文档记录 2026-10-02 设计讨论的结论。
> 落地位置：迁移 `000119`/`000120`（PG）、`000038`/`000039`（SQLite）；
> 类型 `internal/types/stock_watch_diary.go`；服务
> `internal/application/service/stock_watch_diary*.go`；前端
> `frontend/src/components/watchlist/`。
> 实现与本文档若有出入，以代码为准——但**先查本文档的「不可让渡的立场」**，
> 那四条是被刻意守住的。

---

## 0. 为什么有这份文档

「个股追踪」已从一份自选清单演化成四张表的追踪池（见
[`docs/DATABASE.md`](DATABASE.md) 第 2.2 节）。本次要加三件事：

1. **入池时带理由** —— 现在 `AddStockWatchRequest` 没有 `note` 字段，理由只能事后补
2. **右侧内嵌 K 线** —— 列表页看不到图，判断买卖只能切走
3. **每日观察日记** —— 由 LLM 生成买卖/持有/移除建议，人工二次确认

三件事共用一个立场约束（见 [第 1 节](#1-不可让渡的立场)），所以放在一起设计。

---

## 1. 不可让渡的立场

这一节是全文的约束来源。以下四条**不允许在实现中被削弱**：

| 立场 | 含义 | 为什么 |
|---|---|---|
| **LLM 永不自动改 `state`** | AI 只写 `pending_verdict`；`state` 只能由人点下拉菜单改变 | `stockWatchStateTransitions`（`internal/types/stock_watch.go:104-109`）的注释明写 "Nothing but the user moves it"。同一天「AI 建议买 / 人已 drop」时 `state` 该是什么，机器无法回答 |
| **`trade_date` 是行情日，不是墙钟** | 停牌票拿到的是落后几天的 `Reading.Date`，用墙钟会写出一篇标着「今天」、内容其实是上周读数的日记 | 页面上已有 `is-stale` 灰显处理（`Watchlist.vue`），日记要沿用同一事实 |
| **不做成本 / 盈亏 / 止损** | `holding` 行的日记只回答「你当初的关注逻辑还在不在」 | `types.StockWatch:20-24` 已论证：拆股过的持仓用 nullable float 算盈亏会静默出错。真要成本是独立子项目 |
| **Python/Go 算的数，LLM 不算** | 日记只读现成读数，不让 LLM 做任何算术 | AGENTS.md：LLM arithmetic is a bug source |

---

## 2. 入池理由（Q1 / Q1b / Q14 / Q15）

### 2.1 从 chat 正文抽取

`MentionedStocksBar`（`frontend/src/components/chat/MentionedStocksBar.vue`）的「进池」按钮
现在只拿得到 `thscode / name / exchange`。要带上理由，规则是：

- **以该票「最后一次」出现的位置为中心**，向前找到最近的段落边界（`\n\n`），向后也到段落边界，取整段
- 去掉 markdown 标记，硬截 500 字
- 找不到前边界（该票在正文第一段）就退化为从 0 开始

**为什么取最后一次而不是第一次**：`extractStockMentions` 按 thscode 去重、只保留首次位置
（`frontend/src/utils/stockMentions.ts:197-199`），而 LLM 写股票分析的结构几乎总是
「铺垫 → 展开 → 结论」——结论段才是理由，第一次提及往往在「我们来看看 601127.SH 的情况」
这种引出句里。

**实现位置**：这个逻辑必须是 `utils/stockMentions.ts` 里的**纯函数** + 单测
（该文件已有 `pickPrimaryMention` 这类纯函数先例），不能塞进组件 click handler——
它是全站唯一真相源，截错了用户无从得知。

### 2.2 原子写入

`AddStockWatchRequest`（`internal/handler/stock_watch.go:114-118`）加 `note` 字段，
service 层在**同一个事务**里写行 + `added` 事件。

**为什么必须原子**：分两次请求时，中间崩了就留下一行 `note=''` 的记录，
而 `added` 事件的 `note` 快照是空的——事后无法追溯「它当初为什么进池」。
`UpdateStockWatchRequest` 已经有 `note`（`:187`），service 层复用现成的
`MaxStockWatchNoteLen` 校验即可。

### 2.3 note 宽度：PG 用 TEXT，SQLite 保持 VARCHAR(500)

⚠️ **两处一起改，不只 `stock_watches.note`**：

| 列 | 现状 | PG (`versioned`) | SQLite |
|---|---|---|---|
| `stock_watches.note` | `VARCHAR(200)` | `TEXT` | `VARCHAR(500)` |
| `stock_watch_events.note` | `VARCHAR(200)` | `TEXT` | `VARCHAR(500)` |

**为什么 PG 用 `TEXT` 而不是 `VARCHAR(500)`**：
`TEXT` 与 `VARCHAR(n)` 在 PG 里**存储完全等价**（同样的 TOAST 机制），
但不再需要在**每次理由长度上限变更时改一次 DDL**。理由文本的长度上限由
应用层 `MaxStockWatchNoteLen` 统一守卫，而不是由数据库声明——这与仓库里
`type:text` 的既有用法一致（`stock_watch_condition.go:187-189` 的
`payload`/`error` 就是 `type:text`）。理由是可变的自然语言，声明一个数字上限
本身就是在给一个会变的约束钉一个假的具体数字。

**为什么 SQLite 仍然是 `VARCHAR(500)`**：
SQLite **不 enforcement VARCHAR 长度**（实测：`VARCHAR(200)` 列成功存入 500 字符，
`pragma_table_info` 仍报 `VARCHAR(200)`）。所以 SQLite 侧**行为上不需要改**。
但仍然写成 `VARCHAR(500)`，是为了让**两套 schema 在文字上对齐**，
避免将来有人读 `migrations/sqlite/` 时以为宽度还是 200 而重新推导一遍。
真正的迁移代价在下面。

**SQLite 的迁移代价**：`ALTER TABLE` **不能修改列宽**。200→500 在 Lite 上只能
`ADD COLUMN note_new` → 拷贝 → `DROP` → `RENAME`，这与新增日记表是两件
不同性质的事，必须分属两条迁移。

**GORM tag 只有一个写法，不用分方言**：生产 schema 由 `golang-migrate` 拥有
（`internal/database/migration.go`），全仓库对这两张表**没有任何生产 AutoMigrate**
（`AutoMigrate` 只出现在测试文件里）。所以 `gorm:"type:text"` 一个 tag 服务两个方言，
**迁移文件才是权威**——这与 `internal/types/stock_watch.go:26-31` 那条
「`thscode` 必须写 `column:thscode`，否则 GORM 折成 `ths_code`」的注释是同一类提醒：
tag 与迁移不一致时，**信迁移**。

### 2.4 「让 AI 补一段理由」

行内按钮 → 同步调一次单票 LLM → **结果先填进输入框让人确认** → 再落库。

- 同步而非异步：这是**人正在操作**的场景，等 2 秒可以接受；异步会引入
  「点了按钮不知道结果」的中间态
- 先确认再写：符合第 1 节立场——LLM 产出永远进人工确认这道门，**包括理由**

---

## 3. K线内嵌（Q6）

**不复用 `KLineWorkspace.vue`**，新写一个轻量组件。

**为什么不复用**：该组件约 2000 行，复杂度**几乎全在「与 chat 的联动」**上——
候选池条、锚点高亮、追问条、形态气泡。这些在列表页一个都不需要。
硬复用要为了去掉 70% 的无关功能付出 100% 的耦合代价。

**复用**：`internal/models` 侧不动；`/api/kline`（python-service，
`day|week|month`、前复权、默认 5000 根）现成；前端复用
`components/workspace/kline/core-chart.ts` 与 `theme.ts` 的 chart 初始化与配色。

**形态**：行选中 → 右侧滑出面板 → 轻量 K 线 + 下方日记时间线。
默认日线 120 根，保留周/月切换。

---

## 4. 每日观察日记（Q2 / Q4 / Q5 / Q8 / Q9 / Q10 / Q11 / Q12 / Q13）

### 4.1 新表 `stock_watch_diaries`

```sql
-- PG (migrations/versioned/0001xx_stock_watch_diaries.up.sql)
CREATE TABLE IF NOT EXISTS stock_watch_diaries (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     VARCHAR(36) NOT NULL,
    tenant_id   BIGINT      NOT NULL,
    thscode     VARCHAR(16) NOT NULL,
    trade_date  DATE        NOT NULL,     -- Reading.Date，不是写入日
    verdict     VARCHAR(16) NOT NULL,     -- buy|hold|sell|none|keep|tighten|exit
    confidence  SMALLINT    NOT NULL DEFAULT 0,
    reasons     TEXT        NOT NULL DEFAULT '',
    body        TEXT        NOT NULL DEFAULT '',
    model_id    VARCHAR(64) NOT NULL DEFAULT '',
    readings    TEXT        NOT NULL DEFAULT '',   -- 五个读数的 JSON 快照
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

⚠️ **注意 `verdict` 是两个集合的并集**：
`observing` 行用 `buy|hold|sell|none`，`holding` 行用 `keep|tighten|exit|none`。
单列存两套取值是刻意的——它们回答的是同一个问题（「我该做什么」），
分成两列会让「今天有结论吗」这个判断也要写两遍。
`none` 在两套里都存在，含义相同：**数据不足，无结论**。
没有 `none`，LLM 在读数缺失时会被迫编一个买卖出来。

唯一索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_watch_diaries_unique
    ON stock_watch_diaries (user_id, tenant_id, thscode, trade_date);
```

这个索引就是**幂等键**：同一天重复跑 cron 是 upsert 覆盖，不会产生第二篇。

**为什么不复用 `stock_watch_events`**：事件是「状态变了一次」，append-only，
`kind` 通用；日记是「每个交易日一条、几百字、带上下文快照」。塞进 events 会让
一个标的一年产生 250 行噪音事件，淹没事件流本来的用途（回答「这行为什么变成这样」）。
events 继续只记状态迁移 + 人工采纳/忽略。

### 4.2 调度

**挂在现有 `StockWatchConditionJob` 的 08:30 批次尾部**，不新增 cron
（`stock_watch_condition_job.go:25` 的 `0 30 8 * * *` Asia/Shanghai）。

- 为什么 08:30：ETL 约 19:00 落地前一日 K 线，08:30 时数据已新鲜且人已看到消息
- 为什么不加盘中批次：本地 K 线是 19:00 ETL 落的，**盘中无新数据可读**，
  LLM 只能对着昨天写今天——假装有增量信息

### 4.3 输入

**只喂**（全部现成，零新增 HTTP 客户端）：

- `watchcond.Reading` 的五个字段：`Close / ChangePct / VolumeRatio / MA20 / Date`
  （`internal/watchcond/evaluator.go:29-44`；`MA20` 与 `VolumeRatio` 是
  commit `7fe975661` 专门为条件判定补进 `/api/quotes` 的）
- 入池理由（`stock_watches.note`）
- 近 5 天历史 verdict

`quoteclient.Client.Fetch` 一次批量拿全（≤200 只/次，内部自动分块）。

**为什么不加 K 线序列或 `zettaranc.analyze`**：日记要回答的是「今天变了没有」，
五个读数够答；`MA20 + VolumeRatio` 恰好是**最可能被人为设条件的那两个字段**——
设了阈值，LLM 就该知道它今天满足了没有。
「为什么变」是等日记跑满一周、确认确实答不出时才要的东西。

### 4.4 模型与 prompt 契约

**模型**：抄 `memory/extract.go:903-921` 的 `workspaceChatModelID`——
取第一个 active 的 `KnowledgeQA` 模型，**并把「这是猜的」写进日志**
（日志措辞照抄 memory：`no diary model configured, using workspace model %s`）。
选中的 model id 写进日记行的 `model_id` 字段。

⚠️ **前置陷阱：`ListModels` 第一行是 `types.MustTenantIDFromContext(ctx)`
（`internal/application/service/model.go:210`），缺 tenant 会 panic。**
cron 跑在 `context.Background()` 里，因此日记作业**必须逐 scope 重建 ctx**：
`context.WithValue(ctx, types.TenantIDContextKey, scope.TenantID)`
（仓库里 `memory/extract.go:282`、`knowledge_process.go:1242` 等十几处同写法）。
现有条件作业没事是因为它压根不碰模型服务。

**prompt 契约**：全套抄 memory 范式（`consolidate.go:461-466` + `extract.go:1088-1112` + `extract.go:1030-1040`）：

- `Temperature: 0`
- `Thinking: false` —— reasoning 模型会把预算全花在自言自语上然后返回空串
- `Format` 传 object schema —— **但不能只靠它**：
  `openaicompletions/request.go:214` 只在 `SupportsResponseFormat` 为真时才发出去，
  per-vendor compat 可能为假
- **必须**配 tolerant parser：剥 ```json 围栏 → 剥散文 → 取首个 `{` 到末个 `}`
- `MaxCompletionTokens` 预算 + `FinishReason == "length"` 时加大预算重试一次

### 4.5 范围与失败隔离

- **范围**：`state IN ('observing', 'holding')`。`dropped` 已放弃，不问
- **一次批量调用**整批；**单票解析失败只丢那一票**
  （抄 `memory/extract.go` 「one bad entry must not discard the rest of the run」）
- **整次调用失败**：当天不写任何行，但在 `stock_watch_notifications` 记一条
  （表已存在，`migrations/sqlite/000036`）——否则「那天没日记」和「那天没跑」永远分不开

### 4.6 `holding` 行的特殊语义

`holding` 行的 verdict 取值不是买卖，而是**跟踪逻辑是否还成立**：

- `keep` —— 关注逻辑未变
- `tighten` —— 理由变弱但未失效（收紧观察）
- `exit` —— 理由已失效

**不需要成本价、不算浮盈、不给止损建议。** 止损是纪律问题，该由人事先定好价格线执行，
不是让 LLM 每天重新决定一次。

### 4.7 不推送飞书

日记**不推送**。只落 `stock_watch_diaries` + events，UI 是唯一出口。

- 为什么：日记是**每票一篇、每篇几百字**，30 只票推一条消息是垃圾
- 飞书只能推纯文本，**verdict 的结构化标签和颜色在飞书里全丢**
- 与 `internal/alertnotify/feishu.go:3-7` 的既有立场一致：「the watchlist page is
  the primary surface, and the push is the bonus」。日记连 bonus 都不占

---

## 5. UI（Q7）

行内折叠的日记抽屉，点开看当天全文 + verdict + 依据，抽屉顶部三个按钮：

| 按钮 | 行为 | 落库 |
|---|---|---|
| ✅ 采纳 | 改 `state` | events：`kind='verdict_accepted'`，`from_state`/`to_state` 记实 |
| 🚫 忽略 | 不改 `state` | events：`kind='verdict_ignored'` |
| ✏️ 重写 | 同步重跑单票 LLM，结果先进输入框待确认 | 同 2.4 |

**「忽略」必须落 events**——这是唯一能回答「AI 是不是一直看错」的证据，
也是未来调 prompt 的唯一依据。

**`state` 只有「采纳买入」和「采纳移除」会改**，其余一律不动。

---

## 6. Q16–Q18 的最终决定

三题已拍板，均采纳原建议。

### 6.1 Q16 —— 日记的 scope 从 `stock_watches` 扫，不用 `ListScopes`

```sql
SELECT DISTINCT user_id, tenant_id FROM stock_watches
 WHERE state IN ('observing', 'holding')
```

**为什么不用 `ListScopes`**：它是
`Model(&types.StockWatchCondition{}).Distinct("user_id","tenant_id")`
（`internal/application/repository/stock_watch_condition.go:110-117`），
**只有设过条件的人才在结果里**。而日记范围是「池子里所有 `observing + holding`」——
没设任何条件的票根本不在 scopes 里，会静默拿不到日记。

**为什么不塞哨兵条件**：为让人出现在名单里而伪造一条用户从没设过的条件行，
是在用户的数据里写他没要求的东西。

直接 `SELECT DISTINCT` 一张已有的表，不碰 repository 接口。

### 6.2 Q17 —— 日记不推送飞书

只落 `stock_watch_diaries` + events，UI 是唯一出口。理由见 4.7。

### 6.3 Q18 —— 迁移八条都写，PG 的 note 用 `TEXT`

⚠️ **两套并行体系，每个特性成对**：

| 方言 | 路径 | 现状 |
|---|---|---|
| PostgreSQL | `migrations/versioned/` | 000115–000118 |
| SQLite | `migrations/sqlite/` | 000034–000037 |

`note` 宽度变更与日记建表**分属两条迁移**（理由见 2.3），
每条在两套体系下都有 up/down，共八条文件：

| 迁移 | 内容 |
|---|---|
| `versioned/0001xx_stock_watch_note_text.up.sql` | `stock_watches.note` / `stock_watch_events.note` → `TEXT` |
| `versioned/0001xx_stock_watch_diaries.up.sql` | 建 `stock_watch_diaries` |
| `sqlite/0000xx_stock_watch_note_widen.up.sql` | 两列 add/copy/drop/rename → `VARCHAR(500)` |
| `sqlite/0000xx_stock_watch_diaries.up.sql` | 建 `stock_watch_diaries`（`INTEGER PRIMARY KEY AUTOINCREMENT`） |

PG 侧 `ALTER COLUMN note TYPE TEXT` 的先例见 `000058_expand_knowledge_source`、
`000066_expand_knowledge_span_name`。

互指注释是既定惯例，照抄 `migrations/sqlite/000035` 那份
「SQLite twin of versioned migration 000116」的写法。

`watchlistTestDDL` 用 `Glob("*stock_watch*.up.sql")` 扫 sqlite 目录
（`internal/application/repository/stock_watch_sqlite_test.go:31`），
**新迁移自动被测试吃到**——这是好事，不要改成硬编码列表。

---

## 7. 与既有代码的关系

| 关注点 | 位置 | 处置 |
|---|---|---|
| `stockWatchStateTransitions` | `internal/types/stock_watch.go:104-109` | **一行不改** |
| 「只有用户能改 state」的测试 | 多个 `*_pool_sqlite_test.go` | **不改** |
| `KLineWorkspace.vue` | `frontend/src/components/workspace/kline/` | **不动**，新组件独立 |
| `StockWatchConditionJob` | `internal/application/service/` | 追加一步，不改现有四步 |
| `watchcond.Evaluate` | `internal/watchcond/evaluator.go` | **不动** |
| `ReadOnly` events repo | `repository/stock_watch_events.go` | 仍只读 |

---

## 8. 决策索引

完整决策表（含每题原文与理由）见 [Q1–Q18 逐题记录](watchlist-diary-decisions.md)。
本文档只保留实现所需的结论。
