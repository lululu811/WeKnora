# 个股追踪增强：Q1–Q18 逐题记录

设计讨论的完整存档，含每题的问题、选项与理由。
实现所需的结论已整理进 [`watchlist-diary-design.md`](watchlist-diary-design.md)；
本文档保留**决策过程**——「为什么不是另一个选项」在半年后维护时比结论本身更有用。

**状态**：Q1–Q18 全部拍板（均选 A）。

---

## 第 1 轮

### Q1 —— 「理由」到底是谁写的？

代码里那条 LLM 建议**根本不存在**：`MentionedStocksBar` 从 assistant 正文只抽了
`thscode/name/exchange`，LLM 的分析文字还留在 chat message 里，没有结构化落库。

- **A（已选）**：点进池时截取该票所在段落前 N 字作默认理由，note 上限 200→500，
  备注旁加「让 AI 补一段理由」入口
- B：进池弹框让 LLM 现在就写一句——理由一定切题，代价是多一次模型调用
- C：不做默认，理由一律后补
- D：不截取，理由留空

**选 A 并加了 B 的兜底**：A 的成本是「可能截到废话」，而「让 AI 补一段理由」
入口就是 B 的降级版。猜错的代价由人工重写吸收，不用一次做两个入口。

### Q1b —— 入池理由要不要原子写入？

分两次请求（先 POST 入池，再 PUT 写 note）时，中间崩了就留下空理由行，
且 `added` 事件快照为空，事后无法追溯。

- **A（已选）**：`AddStockWatchRequest` 加 `note`，行 + `added` 事件同事务
- B：保持两个请求，接受「偶尔一行理由为空」
- C：两个请求，理由为空的行在 UI 高亮

**选 A**：改动面极小（一个 struct 字段 + service 一个参数 + 复用现成
`MaxStockWatchNoteLen` 校验），换掉的是「理由可能永久缺失」这个不可追溯的洞。

### Q2 —— 机器的话存在哪？

- **A（已选）**：新表 `stock_watch_diaries`（thscode + trade_date 唯一 + 正文 +
  结构化 verdict/confidence/reasons + 读数快照）；events 只记状态迁移
- B：复用 events，`kind='diary'`
- C：复用 events，每天一条全池汇总
- D：不落库，只当场显示

**选 A**：事件是「状态变了一次」，日记是「每交易日一条、几百字、带上下文快照」。
塞进 events 会让一个标的一年产生 250 行噪音，淹没事件流本来的用途。

### Q3 —— `state` 还是不是 LLM 能动的东西？

`StockWatch.State` 的注释白纸黑字：「Nothing but the user moves it」。

- **A（已选）**：LLM 只写 `pending_verdict`，永不动 `state`；采纳才改
- B：允许 LLM 写 `triggered`（那个预留态就是给它准备的）
- C：加两个正交标记位 `ai_buy_flag` / `ai_sell_flag`

**选 A 而非 B**：B 会让「状态」和「建议」两件事混在一个字段里——
同一天机器说买、你说 drop，state 该是什么？A 让 state 永远是「你的立场」，
AI 的话永远是「AI 的立场」，两者不打架，采纳是一个显式动作。
B 还要额外处理「AI 建议买但你已 dropped」这种边角。

### Q4 —— 观察日记一天跑几次？

- **A（已选）**：只跑一次 08:30，挂现有 cron 批次
- B：08:30 + 14:50 两次
- C：只在打开页面时按需生成
- D：手动触发

**选 A**：B 的 14:50 意味着行情快照只能取到当时的部分数据（本地 K 线是 19:00 ETL 落的），
盘中无新数据，LLM 只能对着昨天写今天——**假装有增量信息**。
真要盘中日记，那是「实时行情源」问题，不是 cron 问题。

### Q5 —— LLM 读什么？一次喂多少？

- **A（已选，修订为 A′）**：批量一次调用，输入 = 五个 Reading + 入池理由 +
  近 N 天历史 verdict，严格 JSON 输出
- B：每票一次
- C：只喂纯技术面
- D：喂进 agent 引擎让它自己调 tools

**A 被修订为 A′**：查代码发现 `watchcond.Reading`（`evaluator.go:29-44`）已经带了
`Close/ChangePct/VolumeRatio/MA20/Date` 五个字段，`quoteclient.Client.Fetch`（`client.go:85`）
已批量封装。所以日记 LLM **不需要调任何工具**，拿到的就是一份 JSON 读数。
A′ 比原 A 便宜得多，也不需要会话管线。

**A 相对 C 的优势**：一定要喂入池理由。LLM 复述均线交叉没有价值；
LLM 要说的是「你当初因为 X 关注它，今天 X 变了没有」。

### Q6 —— K线图：嵌进列表页还是复用一个轻量组件？

`KLineWorkspace.vue` 约 2000 行，挂了候选池条、战法状态条、工具栏、锚点、砖型图、
追问条——**不是**一个能塞进表格右侧的组件。

- **A（已选）**：行选中 → 右侧滑出面板，内含**新写的**轻量 K 线组件
- B：点行跳到 chat 页打开 K 线工作台（`agentWorkspace.open('kline',...)` 现成）
- C：把 `KLineWorkspace` 抽成可复用组件

**选 A**：`KLineWorkspace` 的复杂度**全在「和 chat 的联动」**上，列表页一个都不需要。
硬复用要为了去掉 70% 的无关功能付出 100% 的耦合代价。
B 违背「右侧直接看到」的诉求；C 要么剥离 chat 专属依赖（工作量大），
要么复刻残缺版（以后两处必然分叉）。

### Q7 —— 「二次判断」在 UI 上长什么样？

- **A（已选）**：行内折叠日记抽屉，顶三个按钮：采纳 / 忽略 / 重写；
  **「忽略」也落 events**
- B：备注旁 AI 图标 + Popover
- C：独立的「待处理建议」页
- D：A + C 都要

**选 A**：「忽略」必须落 events——这是唯一能回答「AI 是不是一直看错」的证据，
也是未来调 prompt 的唯一依据。C 的汇总视图等池子超过 30 只后再加。

---

## 第 2 轮

### Q8 —— 「继续持股」需要成本吗？

`StockWatch` **刻意不建模成本/盈亏**，注释写得很硬：拆股过的持仓会静默算出错误盈亏，
「等需求真实存在时再单开表」（`types/stock_watch.go:20-24`）。

- **A（已选）**：`holding` 行只回答「关注逻辑还在不在」——verdict 取
  `keep / tighten / exit`
- B：加 `cost`/`shares`/`entry_date`，算浮盈止损
- C：让 LLM 从历史日记正文自己读成本
- D：`holding` 行不问 LLM

**选 A**：原需求是「LLM 帮我判断」，而**判断「逻辑还在不在」比判断「赚没赚」有用得多**——
后者本质是止损纪律，该由你事先定好的价格线执行。
**C 直接反对**：AGENTS.md 的 load-bearing 不变量就是「LLM arithmetic is a bug source」。

### Q9 —— 日记用哪个模型？

日记没有 KB（不像 auto-tag 有 `kb.SummaryModelID` 兜底），没有会话（不像 memory 有
`payload.ChatModelID`），也不该跟 agent 抢配置。

- **A（已选）**：抄 `memory/extract.go:903-921` 的 `workspaceChatModelID`——
  第一个 active 的 KnowledgeQA 模型，**逐 scope 注入 tenant context**，
  选中的 model id 记进日记行
- B：取 tenant 的 `is_default=true` 模型（要新写 repo 查询）
- C：在 `system_settings` registry 加 `watchlist.diary_model` 键
- D：复用 `builtin_agents.yaml` 里 `builtin-zettaranc` 的模型

**选 A**：C 是正确的终态但现在没人配它，YAGNI。等你发现「日记用了贵模型想换便宜」时再加，
那时加键是十分钟的事。

⚠️ **A 的前置陷阱**：`ListModels` 第一行是 `types.MustTenantIDFromContext(ctx)`
（`service/model.go:210`），缺 tenant 会 **panic**。cron 跑在 `context.Background()` 里。
现有条件作业没事是因为它压根不碰模型服务。详见设计文档 4.4。

### Q10 —— prompt 的 JSON 契约怎么钉死？

- **A（已选）**：全套抄 memory 范式——`Temperature 0` + `Thinking false` +
  `Format` 传 schema + 预算 + 截断重试 + **tolerant parser**；
  verdict 枚举 `buy|hold|sell|none`
- B：不传 `Format`，只靠 prompt + parser
- C：一票一调用，schema 极简

**选 A**。两个关键点：
1. **`none` 必须存在**，否则 LLM 在没数据时被迫选一个买卖
2. **tolerant parser 不是可选项，是必需的**——`openaicompletions/request.go:214`
   只在 `SupportsResponseFormat` 为真时才发 `response_format`，per-vendor compat 可能为假。
   `Format` 能发就发，发不出去靠 parser 兜。

### Q11 —— 日记到底读什么数据？

`watchcond.Reading` 已有五个字段，其中 `MA20`、`VolumeRatio` 是上一轮 commit `7fe975661`
专门为条件判定补进 `/api/quotes` 的。日线走势、形态、支撑阻力**都不在里面**。

- **A（已选）**：只喂五个 Reading + 入池理由 + 近 5 天历史 verdict。零新增 HTTP 客户端
- B：再加近 60 根日 K 序列
- C：加 `zettaranc.analyze` 的结构化分析
- D：A 为主，每 N 票抽一只做 B 式深度分析

**选 A**：日记要回答的是「今天变了没有」，五个读数够答。
且 `MA20 + VolumeRatio` 恰好是**最可能被人为设条件的那两个字段**——
设了阈值，LLM 就该知道它今天满足了没有。
B/C 回答「为什么变」，那是等日记跑满一周、确认确实答不出时才要的东西。

### Q12 —— 谁有日记？LLM 失败或返回垃圾怎么办？

- **A（已选）**：范围 = `observing + holding`；一次批量调用；单票失败只丢那一票；
  整次失败当天不写行但记 `stock_watch_notifications`
- B：范围含 `triggered` 和 `dropped`
- C：一票一调用
- D：范围可配

**选 A**。留痕是关键：`stock_watch_events` 已经因为 `eval_date` vs `created_at`
吃过一次「记录了但徽章永远不亮」的亏（`Watchlist.vue:268-272` 的注释记着），
同一个错误不能在日记上重犯。

### Q13 —— `trade_date` 取什么？重跑怎么办？

- **A（已选）**：`trade_date` = 该票 `Reading.Date`，**不是墙钟日期**；
  唯一索引 + 重跑 upsert 覆盖
- B：用墙钟日期，每天必有一行
- C：重跑追加不覆盖，历史更全

**选 A**：停牌票的行情日期会落后（页面上就有 `is-stale` 灰显），
用墙钟会给它写一篇标着「今天」、内容其实是上周读数的日记。
**反对 C**：「多版本」听着诱人，实际是你自己在 UI 里做去重——而 UI 去重一定会漏。

### Q14 —— 「截取 LLM 建议」的具体规则

事实：`extractStockMentions` 给每只票返回一个 `index`（语义：在输入文本中的起始下标），
按位置切片可行。但**同一只票出现 3 次时只保留第一次**，第一次往往只是被顺带提到。

- **A（已选）**：以该票**最后一次**出现的位置为中心，向前后找段落边界取整段，
  去 markdown，硬截 500 字；找不到前边界就退化从 0 开始
- B：只取该票之后 200 字
- C：弹框让用户粘贴/编辑
- D：不截取，理由留空

**选 A**。**关键是「最后一次」而不是「第一次」**：LLM 写股票分析的结构几乎总是
「铺垫 → 展开 → 结论」，结论段才是理由，第一次提及往往在「我们来看看 601127.SH 的情况」
这种引出句里。实现必须是 `utils/stockMentions.ts` 里的**纯函数** + 单测。

### Q15 —— note 上限，以及「让 AI 补理由」怎么触发？

⚠️ 耦合点：`stock_watch_events.note` **也是 VARCHAR(200)**，语义是「变化瞬间的快照」。
只改 `stock_watches.note` 会导致超 200 字的理由**写得进主表、写不进事件**。

- **A（已选）**：两张表的 note 列**一起** 200→500；「补理由 / 重写」是行内按钮，
  同步调一次单票 LLM，**结果先填进输入框让人确认**再落库
- B：涨到 1000
- C：保持 200
- D：改成 TEXT

**选 A**。同步单票调用是刻意的：这是**你人在操作**的场景，等 2 秒可以接受，
异步化会引入「点了按钮不知道结果」的中间态。

> 📌 **后续修订（Q18 讨论中）**：PG 侧最终采用 **D 的做法**——`note` 改为 `TEXT`。
> 理由：`TEXT` 与 `VARCHAR(n)` 在 PG 里存储完全等价，但不再需要在每次理由长度
> 上限变更时改一次 DDL；长度上限由应用层 `MaxStockWatchNoteLen` 统一守卫。
> **长度仍守 500**（A 的语义不变），只是不再由数据库声明这个数字。
> SQLite 侧保持 `VARCHAR(500)`——实测 SQLite 根本不 enforcement VARCHAR 长度，
> 写成 500 只是为了让两套 schema 文字对齐。详见设计文档 2.3。

---

## 第 3 轮

### Q16 —— 日记的 scope 从哪来？ ⚠️ 正确性

`ListScopes` 是 `Model(&types.StockWatchCondition{}).Distinct("user_id","tenant_id")`
（`repository/stock_watch_condition.go:110-117`）——**只有设过条件的人才在结果里**。
而日记范围是「池子里所有 `observing + holding`」，没设任何条件的票根本不在 scopes 里。

- **A（已选）**：不复用 `ListScopes`，从 `stock_watches` 自己扫
  `DISTINCT(user_id, tenant_id) WHERE state IN ('observing','holding')`
- B：复用 `ListScopes`，再合并池子表的 scope
- C：在条件表给没条件的人塞一条永不满足的哨兵条件

**选 A**。**反对 C**：为让人出现在名单里而伪造一条用户从没设过的条件行，
是在用户的数据里写他没要求的东西。

### Q17 —— 日记要不要推送飞书？

条件作业是全局一次取价、发**一条**汇总消息。日记改成逐 scope 调 LLM 后，
若沿用同一套推送会变成**每租户一条**。而日记是每票一篇、每篇几百字。

- **A（已选）**：**不推送**，只落库 + events，UI 是唯一出口
- B：每 scope 汇总一条
- C：只推 verdict 发生变化的票
- D：复用 condition 消息，AI 结论拼后面

**选 A 的理由**：30 只票推一条消息是垃圾；飞书只能推纯文本，
verdict 的结构化标签和颜色全丢。与 `alertnotify/feishu.go:3-7` 的既有立场一致——
「the watchlist page is the primary surface, and the push is the bonus」。

### Q18 —— 迁移如何落地？

`migrations/` 下有**两套并行体系**：`migrations/versioned/`（000115–000118，PG）和
`migrations/sqlite/`（000034–000037，Lite）。每个 stock_watch 特性都是**成对**的两条。

- **A（已选）**：四条都写（两套 × up/down），照抄现有互指注释风格；
  **PG 侧的 `note` 改用 `TEXT`**
- B：只改 sqlite
- C：note 宽度改动和日记表塞进同一条迁移

**选 A**。**反对 B**：`make build-lite` 是一等公民，只改一套会让 Postgres 部署运行时炸
`column too long`。
**反对 C**：SQLite 的 `ALTER TABLE` 不能改列宽，200→500 只能
add/copy/drop/rename；这与建新表是两件不同性质的事，混一条会让 down 难以正确回滚。

**PG 用 `TEXT`（追加决定）**：`TEXT` 与 `VARCHAR(n)` 在 PG 里存储完全等价，
但理由长度上限是**会变的自然语言约束**，把它钉成一个数字本身就是错的建模。
真正的守卫是应用层 `MaxStockWatchNoteLen`（服务端已在
`service/stock_watch.go:130` 用 `utf8.RuneCountInString` 做了这个检查）。
PG 侧先例：`000058_expand_knowledge_source`、`000066_expand_knowledge_span_name`。
