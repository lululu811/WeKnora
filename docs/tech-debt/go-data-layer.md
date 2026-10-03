# 技术债审计 — Go 数据层（models / repository / types / migrations）

## 结论

- **修** `tenant_members` 唯一索引：SQLite 建成了全表唯一，Postgres 是 `WHERE deleted_at IS NULL` 部分唯一；SQLite/Lite 上被移除的成员**永远无法重新加入**（被移除→重新邀请报 409 `ErrMembershipAlreadyExists`，而 Get 看不到已软删行，代码里没有任何恢复路径）。
- **补** `mcp_metadata` 表与 `mcp_services.usage_instructions` 列到 `migrations/sqlite/`：Postgres 侧有（000092/000092），SQLite 侧完全没有，但 Lite 构建跑的是**同一套 service**（无 edition 开关），MCP 目录接口在 Lite 上必然 `no such table`。
- **合并** `knowledge_tags` 的模型/表不一致：模型无 `gorm.DeletedAt` 而表有 `deleted_at` 列，导致 `tag.go:139` 实际是硬删，`knowledge_tag_relations` 残留孤儿行且永不被 `DeleteUnusedTags` 回收。
- **修** 3 处无方言判断的 Postgres 专有 SQL（`wiki_page.go:370` 的 `to_tsvector`、`organization.go:96` 的 `ILIKE` + `id::text`、`user.go:265` 的 `ILIKE`），Lite 上必 500；同文件 `wikiSearchMatchOp()` 已经做对了，说明是遗漏而非设计。
- **补** schema 一致性守卫：现有 parity 测试（`migration_sqlite_versioned_schema_test.go`）只断言一份**手写的 22 表 + 21 表列**白名单，Postgres 独有的 `mcp_metadata` 恰好不在其中，漂移无人拦截。
- **收敛** `internal/types` 与迁移的字段漂移：`stock_watch.note` 模型写 `varchar(200)`、PG 迁移是 `TEXT`、SQLite 迁移是 `varchar(500)`，三处互不相同（应用层 `MaxStockWatchNoteLen=500` 才是真实约束）。

## 发现

### [S1] `tenant_members` 唯一索引在 SQLite 上没有 `deleted_at IS NULL` 谓词，成员被移除后无法重新加入

- **位置**：`migrations/sqlite/000000_init.up.sql:357`、`migrations/versioned/000043_tenant_rbac.up.sql:40`、`internal/application/repository/tenant_member.go:170`、`internal/application/service/tenant_member.go:184`
- **证据**：两套迁移同名索引定义不一致——

  ```sql
  -- sqlite/000000_init.up.sql:357  （全表唯一）
  CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_members_user_tenant_unique
      ON tenant_members(user_id, tenant_id);

  -- versioned/000043_tenant_rbac.up.sql:40  （部分唯一）
  CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_members_user_tenant_unique
      ON tenant_members(user_id, tenant_id)
      WHERE deleted_at IS NULL;
  ```

  模型 `internal/types/tenant_member.go:106` 是 `DeletedAt gorm.DeletedAt`，`SoftDelete`（`tenant_member.go:170`）走 GORM 软删、只写 `deleted_at` 不删行。
- **影响**：Lite/SQLite 上，Owner 移除成员 → 该行 `deleted_at` 被置位但**仍占着唯一索引槽位**。之后任何人再邀请同一 user，`AddMember`（`tenant_member.go:169`）先 `Get`（软删过滤后返回 nil，不报 `ErrMembershipAlreadyExists`）→ 走到 `repo.Create` 插入新行 → 触发全表唯一冲突 → `isDuplicateMembership`（`tenant_member.go:31`，匹配 `"unique constraint"`）把它**误判成"已经是成员"**，返回 `ErrMembershipAlreadyExists`。邀请流程 `tenant_invitation.go:248` 收到该哨兵后回退去 `GetMembership`，仍然拿到 nil，于是 `return nil, err`。净结果：邀请已被置为 accepted（`MarkStatusIfPending` 早于 `AddMember` 提交，见 `tenant_invitation.go:238`），但成员行不存在，且 `ErrInvitationNotPending` 会让**重试也失败**。用户永久无法重新加入该租户，只能新建租户。同一条错误路径对 Postgres 不存在——那里唯一索引带谓词，重建合法。
- **修复**：`migrations/sqlite/` 新增一个编号迁移 `DROP INDEX IF EXISTS idx_tenant_members_user_tenant_unique` 后按 Postgres 形态重建带 `WHERE deleted_at IS NULL` 的部分索引（SQLite 3.8+ 支持部分索引，`_foreign_keys=on` 的 DSN 下可直接用）。注意已有 Lite 库里若存在"软删行 + 活动行同 (user,tenant)"的历史脏数据，建索引前要先查并清理，否则建索引会失败；同时把 `TestSQLiteMigrationsCreateVersionedSchema` 的 `expectedSQLiteMigrationVersion` 一起 bump。
- **工作量**：S(<半天)

### [S1] `mcp_metadata` 表与 `mcp_services.usage_instructions` 在 SQLite 迁移中完全缺失，Lite 构建的 MCP 目录接口必然报错

- **位置**：`migrations/versioned/000092_mcp_metadata.up.sql:3`（PG 有）、`migrations/sqlite/`（无对应文件）、`internal/application/repository/mcp_metadata.go:16`、`internal/container/container.go:917`
- **证据**：Postgres 侧 000092 一次加了两样东西，SQLite 侧两样都没有：

  ```sql
  -- versioned/000092_mcp_metadata.up.sql:3
  ALTER TABLE mcp_services ADD COLUMN IF NOT EXISTS usage_instructions TEXT NOT NULL DEFAULT '';
  CREATE TABLE IF NOT EXISTS mcp_metadata (
      tenant_id BIGINT NOT NULL, service_id VARCHAR(36) NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
      ...
  ```

  `grep -rn "mcp_metadata" migrations/sqlite/` 无输出；`grep -rn "usage_instructions" migrations/sqlite/*.sql` 无输出。
  而仓储层**已经**为 SQLite 写好了方言分支（`mcp_metadata.go:31` 的 `metadataToolCountExpr` 对非 postgres 走 `json_array_length`），说明这份 schema 是**打算**在 Lite 上工作的，只是迁移没落地。
  `container.go:917` 的 `case "sqlite"` 只切 DSN，不裁剪 service；全仓无 `EDITION`/`lite` 条件装配（`grep -rn "EDITION" --include=*.go internal/` 无输出），MCP handler 照常注册（`internal/handler/mcp_metadata.go:30`）。
- **影响**：Lite 用户点开任一 MCP 服务的"目录/刷新"（`GET /mcp/:id/metadata`）→ `GetMetadata` 报 `no such table: mcp_metadata`；`MCPService` 的任何 `Save`/`First` 也会因 `usage_instructions` 列不存在而失败。更隐蔽的是**启动期不报错**：`container.go:987` 对迁移失败只 `logger.Warnf` 后继续启动，缺表这件事要到用户点进 MCP 才暴露，排查时会被误判成 MCP 连通性问题。
- **修复**：在 `migrations/sqlite/` 追加编号迁移，建 `mcp_metadata`（`tools` 列用 TEXT + `CHECK(json_valid(tools))`，对应 PG 的 `JSONB NOT NULL`）并 `ALTER TABLE mcp_services ADD COLUMN usage_instructions TEXT NOT NULL DEFAULT ''`；`mcp_metadata` 需要与 `mcp_services` 的 `ON DELETE CASCADE` 外键（SQLite 迁移已统一开 `_foreign_keys=on`）。同步把 `mcp_metadata` 补进 `internal/database/migration_sqlite_versioned_schema_test.go` 的 `versionedSQLiteTables`。
- **工作量**：S(<半天)

### [S1] `knowledge_tags` 删标签是硬删，但 `knowledge_tag_relations` 不随之清理，残留孤儿行

- **位置**：`internal/application/repository/tag.go:139`、`internal/types/tag.go:12`、`migrations/versioned/000001_agent.up.sql:255`
- **证据**：模型**没有** `gorm.DeletedAt`（`tag.go:12` 的 struct 到 `UpdatedAt` 结束），而表**有** `deleted_at` 列（`000001_agent.up.sql:251`）。因此 `tag.go:139` 的 `Delete(&types.KnowledgeTag{})` 走的是 GORM 的**物理删除**，`deleted_at` 永远是 NULL——`tag.go:230` 注释里写的 "excluding soft-deleted records" 对本表并不成立。而同表唯一索引：

  ```sql
  -- 000001_agent.up.sql:255 （两套迁移同样地不带谓词）
  CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_tags_kb_name
      ON knowledge_tags(tenant_id, knowledge_base_id, name);
  ```

  硬删之后，唯一索引槽位随之释放，所以"删了建不了同名标签"这个后果**不成立**；真正的问题是 `Delete` 只删 `knowledge_tags` 一行，**不动** `knowledge_tag_relations`。该表无外键（`000001_agent.up.sql` 里 `knowledge_tag_relations` 的建表语句没有 REFERENCES），所以孤儿行不会报错、也不会被级联清理。
- **影响**：`knowledge_tag_relations` 残留指向已删 tag 的行。两个后果：(1) `tag.go:146` 的 `CountReferences` 按 `ktr.tag_id` JOIN `knowledges` 统计，孤儿行不计入可见引用，看起来"无害"；(2) 但 `tag.go:229` 的 `DeleteUnusedTags` 用 `id NOT IN (SELECT DISTINCT ktr.tag_id …)` 判断"未被引用"，孤儿行会让该 tag **永远无法被判定为无用**，从而滞留。表随时间单调增长，且没有任何清理任务覆盖它。
- **修复**：二选一并让模型与表对齐——(a) 给 `KnowledgeTag` 补 `DeletedAt gorm.DeletedAt`，同时把唯一索引改为 `WHERE deleted_at IS NULL`（与 `tenant_skills`/`storage_backends` 已有写法一致），并保留关系行作为历史；(b) 保持硬删，但在 `tag.go:139` 的 `Delete` 里放进事务，同时删除对应的 `knowledge_tag_relations` 行。推荐 (b)——改动面小，且不必为一张语义简单的表引入软删。
- **工作量**：S(<半天)

### [S1] 三处无方言判断的 Postgres 专有 SQL，Lite 上直接 500

- **位置**：`internal/application/repository/wiki_page.go:370`、`internal/application/repository/organization.go:96`、`internal/application/repository/user.go:265`
- **证据**：同文件 `wiki_page.go:1358` 的 `wikiSearchMatchOp()` 明确做了 `if r.wikiDialect() == "sqlite"` 分支，`chunk.go:243` 也做了 `isPostgres` 分支——但以下三处漏了：

  ```go
  // wiki_page.go:370  — List() 的 query 分支，无任何 dialect 判断
  query = query.Where(
      "(to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, '')) @@ plainto_tsquery('simple', ?) OR aliases::text ILIKE ?)",
      req.Query, "%"+req.Query+"%")

  // organization.go:96 — ListSearchable
  q = q.Where("name ILIKE ? OR description ILIKE ? OR id::text ILIKE ?", pattern, pattern, pattern)

  // user.go:265 — SearchUsers
  Where("username ILIKE ? OR email ILIKE ?", searchPattern, searchPattern)
  ```

  触发路径无门槛：`internal/handler/wiki_page.go:123` 直接把 `c.Query("query")` 填进 `WikiPageListRequest.Query`，无需任何权限或开关。
- **影响**：Lite 上访问 wiki 列表带 `?query=` → `no such function: to_tsvector`；组织发现页和用户搜索同理。`wiki_page_test.go:183/220/233` 的 `List` 测试跑在 SQLite 上但**从不设置 `Query` 字段**（已核实三个 `List` 调用的参数均无 `Query`），所以这条分支在 CI 里完全无覆盖。
- **修复**：三处各自加 dialect 分支（SQLite 用 `LIKE` + `ESCAPE` + `json_extract(x,'$.k')`，与 `chunk.go:255` 已有的 `metadata->>'$.field'` 写法一致）；给 `wiki_page_test.go` 补一条设置 `Query` 的 SQLite 用例，否则修完仍无回归保护。
- **工作量**：S(<半天)

### [S2] SQLite schema 与 Postgres 的漂移没有任何自动化守卫，parity 测试靠手写白名单

- **位置**：`internal/database/migration_sqlite_versioned_schema_test.go:23`、`internal/database/migration_sqlite_versioned_schema_test.go:49`、`migrations/sqlite/000000_init.up.sql:1122`
- **证据**：两套迁移的形态完全不同——Postgres 是 121 个顺序增量，SQLite 是 `000000_init.up.sql`（1122 行，47 张表的压缩基线）+ 39 个增量。parity 靠两份**手写清单**：`versionedSQLiteTables`（22 项）与 `versionedSQLiteColumns`（21 张表），文件自己在 78-88 行承认这份常量已经漏过一次（"last bumped 33 -> 37 … three tests failed"）。实测两边表集合差：

  ```
  Postgres 有 / SQLite 无：embeddings, mcp_metadata, organization_members, wiki_log_entries
  ```

  `mcp_metadata` 确认**不在** `versionedSQLiteTables` 里（`grep -n "mcp_metadata"` 该文件无输出），所以第 2 条的缺表至今无声。`organization_members` 与 `wiki_log_entries` 是**已废弃**的表（`organization.go:111` 注释说明已迁到 `organization_tenant_members`；`000077_remove_wiki_log` 两边都删了）——但 PG 侧 `000012` 的 `CREATE TABLE IF NOT EXISTS organization_members` 仍在文件里，说明**废弃表没有被清理**。
- **影响**：新增一张 Postgres 表时，SQLite 侧漏建不会有任何测试变红（除非有人记得手动加白名单），问题推迟到运行时。同理 SQLite 迁移崩了启动也不拦（`container.go:987` 只 warn）。
- **修复**：把 parity 测试改成**从 `migrations/versioned/*.up.sql` 动态解析 `CREATE TABLE` 集合**，与 SQLite 实际建表结果求差，差集必须为空或落在显式的"有意省略"名单里（`organization_members` / `wiki_log_entries` / `embeddings` 三个废弃或引擎专属表）。这样新增 PG 表漏同步会立刻红。PG 侧另开一个迁移 `DROP TABLE IF EXISTS organization_members` 收尾。
- **工作量**：M(1-3天)

### [S2] `stock_watches.note` / `stock_watch_events.note` 三处类型声明互不相同

- **位置**：`internal/types/stock_watch.go:44`、`internal/types/stock_watch.go:200`、`migrations/versioned/000119_stock_watch_note_text.up.sql:27`、`migrations/sqlite/000038_stock_watch_note_widen.up.sql:32`
- **证据**：三份"真相"各说各话——

  ```
  Go 模型          stock_watch.go:44   Note string `gorm:"type:varchar(200);not null;default:''"`
  Postgres 迁移    000119 (up)         ALTER TABLE stock_watches ALTER COLUMN note TYPE TEXT;
  SQLite 迁移      000038 (up)         note VARCHAR(500) NOT NULL DEFAULT ''
  ```

  且两个迁移的**文件名也不同**（`stock_watch_note_text` vs `stock_watch_note_widen`），无法按 topic 名做配对校验。真实约束在应用层：`stock_watch.go:108` `const MaxStockWatchNoteLen = 500`。
- **影响**：模型 tag 是唯一一处会被自动化工具（GORM AutoMigrate、schema 文档生成）读到的声明，它说 200，而 PG 侧根本没有这个上限。任何依赖模型 tag 做校验或做 Lite→PG 迁移换算的下游都会取到错值；两边 down 迁移的回滚行为也不同（PG 用 `USING LEFT(note,200)` 截断，SQLite 用 `substr(note,1,200)` 截断 rune，语义已经分叉）。
- **修复**：模型 tag 改成与目标方言一致（PG 为 `text`，SQLite 为 `varchar(500)`）；由于同一模型要服务两个方言，更实际的做法是显式注释"宽度由 `MaxStockWatchNoteLen` 保证，DB 侧不设限"，避免下一个人再信 tag。同时把 SQLite 迁移文件名对齐成 `stock_watch_note_text` 同名系列。
- **工作量**：S(<半天)

### [S2] SQLite 检索引擎的 `BatchUpdateChunk*` 逐条 UPDATE 且完全吞掉错误

- **位置**：`internal/application/repository/retriever/sqlite/repository.go:355`、`internal/application/repository/retriever/sqlite/repository.go:362`
- **证据**：

  ```go
  func (r *sqliteRepository) BatchUpdateChunkEnabledStatus(ctx context.Context, chunkStatusMap map[string]bool) error {
  	for chunkID, enabled := range chunkStatusMap {
  		r.db.WithContext(ctx).Model(&sqliteEmbedding{}).Where("chunk_id = ?", chunkID).Update("is_enabled", enabled)
  	}
  	return nil
  }
  ```

  返回值直接丢弃。Postgres 同名方法（`retriever/postgres/repository.go:708`）按 tag 分组批量 UPDATE **并且**检查 `result.Error`——同一接口两套错误语义。
- **影响**：FAQ 批量启停 / 批量改标签（`knowledge_faq.go:828`、`knowledge_faq.go:889`，单次可达整批条目数）在 Lite 上退化为 N 次单行 UPDATE——SQLite 是单写者模型，N 次往返把批量操作变成串行瓶颈。更糟的是失败被静默吞掉：上层拿到 `nil` 认为同步成功，向量库与 `chunks` 表就此**永久不一致**，且没有任何日志线索可查。
- **修复**：按值分组（`map[bool][]string` / `map[string][]string`）后用 `WHERE chunk_id IN ?` 一次更新；循环内 `if err := ...; err != nil { return err }`，至少要做到与 Postgres 一致的错误传播。
- **工作量**：S(<半天)

### [S2] 大量高频过滤列在两张表上都只作为组合索引的**尾列**存在

- **位置**：`internal/application/repository/chunk.go:216`、`migrations/versioned/000026_chunks_query_indexes.up.sql:18`、`migrations/versioned/000100_chunks_index_diet.up.sql:18`
- **证据**：`ListPagedChunksByKnowledgeID`（`chunk.go:216`）的过滤是 `tenant_id + knowledge_id + chunk_type IN + status IN (+tag_id/is_enabled)`，排序走 `chunk_index` / `updated_at`。但现存索引只有：

  ```sql
  -- 000026
  idx_chunks_kb_tenant          ON chunks(knowledge_base_id, tenant_id)
  idx_chunks_knowledge_enabled  ON chunks(knowledge_id, is_enabled, deleted_at)
  ```

  `idx_chunks_knowledge_enabled` 以 `knowledge_id` 打头是**对的**，但它只覆盖 `is_enabled`/`deleted_at`，不含 `chunk_type`/`status`——这两个过滤条件只能回表过滤。而 `000100_chunks_index_diet` 刚刚以"chunk_type 基数低、总是和 knowledge_id 组合"为由**删掉了** `idx_chunks_chunk_type`（该迁移注释 9-11 行）。
  排序列 `chunk_index`（`chunk.go:294` 文档类型分支）在两套迁移中**都没有任何索引**。`updated_at` 同样没有。
- **影响**：文档型知识库的分页查询在 chunk 表上退化为"按 `knowledge_id` 定位后，对该知识库全部 chunk 做过滤+排序"。一个文档切出几千 chunk 时，`ORDER BY chunk_index` 需要显式排序且无法用索引满足；FAQ 列表按 `updated_at DESC`（`chunk.go:288`）同理。分页越深 OFFSET 越贵。这与 `000106` 给 `messages` 补 `(session_id, created_at DESC, id DESC)` 索引所修的是同一类问题——chunk 侧还没做。
- **修复**：给 `chunks` 加 `(knowledge_id, chunk_type, status, chunk_index)` 覆盖 FAQ 列表与文档排序两条主路径；`(knowledge_id, is_enabled, deleted_at, updated_at DESC)` 覆盖 recent 读。两套迁移各加一个编号。建前先用 `EXPLAIN` 确认不会让 `idx_chunks_knowledge_enabled` 变成纯冗余。
- **工作量**：M(1-3天)

### [S2] `knowledge_tags` 表有 `deleted_at` 列但模型无 `gorm.DeletedAt`，删除语义与注释直接矛盾

- **位置**：`internal/types/tag.go:12`、`migrations/versioned/000001_agent.up.sql:251`、`internal/application/repository/tag.go:139`
- **证据**：用花括号配对（而非正则）逐表比对"迁移里带 `deleted_at` 的表"与"Go 模型里声明 `gorm.DeletedAt` 的表"，33 张软删表中 **25 张有、2 张真没有**：

  - `knowledge_tags`（`tag.go:12`）——struct 到 `UpdatedAt` 结束，无 `DeletedAt`；表有列（`000001_agent.up.sql:251`）。后果见第 3 条。
  - `message_artifacts`（`message_artifact.go:34`）—— **刻意为之，不算债**：`DeletedAt *time.Time` 而非 `gorm.DeletedAt`，迁移 `000107` 注释说明了为何保留墓碑行（position 是下载地址，物理删除会让旧链接拿到错 blob），且仓储层 8 处查询全部手写 `deleted_at IS NULL`（`message_artifact.go:63/201/226/268/344/371/407/445`），语义自洽。列在此处仅为对照。

  （`tenant_sandbox_configs` 初判为缺失，复核后排除：真正落库的是 `TenantSandboxConfigEntity`，`tenant_sandbox_config_entity.go:56` 有 `DeletedAt gorm.DeletedAt`；`TenantSandboxConfig`（`tenant.go:635`）是内嵌的 JSON 配置载荷，不是实体。）
- **影响**：同一张表的删除语义无法从模型声明推断，只能读实现。更实际的问题是注释误导：`tag.go:230` 写着 "excluding soft-deleted records"，但硬删之下该条件对本表恒真；下一个人照注释去写软删相关逻辑会直接踩空。这类模型/表不一致没有编译期检查，每次新增表都可能重犯。
- **修复**：把 `knowledge_tags` 的删除语义二选一定死（连同第 3 条一起处理）；`message_artifacts` 保持现状，把"刻意不用 `gorm.DeletedAt`"的理由从迁移 `000107` 的注释同步到模型注释，让下一个读模型的人不必去翻迁移。
- **工作量**：S(<半天)

### [S3] `chunk_images` 触发器集合在两套迁移里不一致（PG 3 个 / SQLite 4 个）

- **位置**：`migrations/versioned/000113_chunk_images.up.sql:107`、`migrations/sqlite/000032_chunk_images.up.sql:55`
- **证据**：

  ```
  versioned/000113: trg_chunk_images_insert, trg_chunk_images_update, trg_chunk_images_delete
  sqlite/000032:    trg_chunk_images_insert, trg_chunk_images_patch, trg_chunk_images_update, trg_chunk_images_delete
  ```

  SQLite 多一个 `trg_chunk_images_patch`（`:55`），PG 侧没有对应物；同时 `trg_chunk_images_update` 两边语义也不同（PG 用 `IS DISTINCT FROM` 精确比较 10 个列，SQLite 侧实现不同）。
- **影响**：Lite 上 chunk 的 `image_info` 更新走的是 PG 不存在的一条额外路径。无法从代码判断这是"SQLite 补偿"还是"PG 漏了逻辑"——正是本次审计要消除的那类不确定性。若是前者，`image_info` 在两个方言下的同步行为就已经不同；若是后者，PG 上存在图片投影漏更新。
- **修复**：给 `trg_chunk_images_patch` 补一段说明它为何只存在于 SQLite（并在 PG 侧确认逻辑等价或承认差异），避免下一个读迁移的人重新查一遍。
- **工作量**：S(<半天)

### [S3] `migrations/sqlite/000000_init.down.sql` 只回滚 47 张表中的 34 张

- **证据**：脚本比对 CREATE TABLE 与 DROP TABLE 集合，13 张表建了但回滚时不删：

  ```
  chunk_revisions, data_sources, embed_channels, im_channel_sessions, im_channels,
  mcp_oauth_clients, mcp_oauth_tokens, message_suggestion_events,
  message_suggestion_sets, sync_logs, tenant_sandbox_configs, vector_stores, web_search_providers
  ```

- **影响**：`migrate down` 到 version 0 后残留 13 张表，再 `up` 时 `CREATE TABLE IF NOT EXISTS` 会跳过它们——如果上一轮有手工改动，数据/结构会带着脏状态进入新一轮。SQLite 侧还有唯一一处"DROP 多于 CREATE"的迁移（`000038` 建 3 张临时表、down 删 2 张），虽属正确的表重建写法，但同样是人工推理出来的。
- **修复**：补齐 13 条 `DROP TABLE IF EXISTS`（注意 FK 顺序，SQLite 迁移开了 `_foreign_keys=on`），并加一条测试断言"down 到 0 后 `sqlite_master` 中无残留表"。
- **工作量**：S(<半天)

### [S3] hithink 契约测试不校验 WHERE / ORDER BY / GROUP BY 的列名

- **位置**：`internal/agent/tools/hithink_finance/schema_contract_test.go:64`、`internal/agent/tools/hithink_finance/schema_contract_test.go:215`
- **证据**：主测试只对 `SELECT … FROM` 之间的显式列清单做快照校验：

  ```go
  selectListRe = regexp.MustCompile(`(?is)^\s*SELECT\s+(.*?)\s+FROM\b`)   // :64
  ...
  sel := selectListRe.FindStringSubmatch(sql)                          // :215
  ```

  `FROM` 之后的所有子句（`WHERE` / `ORDER BY` / `GROUP BY` / `HAVING`）**完全没有列名校验**。实测：工具代码里 30 处 `ORDER BY`、16 处 `thscode = ?` 过滤条件都不在校验范围内；`financial/period.go:21` 的 `periodOrderBy = "ORDER BY period_end_ms DESC, period ASC"` 是普通字符串常量（不是反引号字面量），`sqlLiteralRe`（`:60`）根本抓不到。
- **影响**：AGENTS.md 把这个测试描述为"扫描工具源码里所有反引号 SQL，抽出它们引用的表和显式列名，逐一对快照校验"——但"显式列名"实际只指 SELECT 列表。**过滤/排序列写错不会被它拦住**，而这类错误的表现是静默返回错误行集（而非报错），比 AGENTS.md 记录的"全部静默 400"更难发现。
- **修复**：给测试加 `whereRe` / `orderByRe` / `groupByRe`，对子句里的裸标识符做同样的快照校验（`financial/period.go:21` 那种常量需单独处理——可加一条断言检查 `periodOrderBy` 引用的列存在于 `v_balance_sheet`/`v_income_statement`/`v_cash_flow_statement`）。另：把 `financial/period.go` 的三段 SQL 与 `financial/period_test.go` 现有断言一起纳入覆盖。
- **工作量**：M(1-3天)

### [S3] 契约测试只扫描子目录，根包里的 `discover.go` SQL 不在保护范围内

- **位置**：`internal/agent/tools/hithink_finance/schema_contract_test.go:145`、`internal/agent/tools/hithink_finance/discover.go:292`
- **证据**：测试遍历 `os.ReadDir(".")` 后 `if !entry.IsDir() { continue }`（`:145`），只扫子包。根包的 `discover.go:292` 持有一条真实查询：

  ```go
  const indicatorColumnsSQL = `SELECT column_name, data_type, count(*) OVER () AS total_columns
  FROM information_schema.columns
  WHERE table_name = ? ORDER BY ordinal_position`
  ```

  它查的是 DuckDB 的 `information_schema`（`discover.go:285-291` 的注释解释了为何不用自维护清单），**不在** 7 个库的快照范围内。
- **影响**：这条 SQL 查错 `information_schema` 只会在线上表现为"取不到列信息"，测试不会响。范围小、影响有限，属可接受但未言明的盲区。
- **修复**：在测试里显式注释说明"根包的 `information_schema` 查询不适用本契约"（有意排除），或为它单独加一条断言锁定表名。
- **工作量**：S(<半天)

## 量化

**规模**

| 项 | 数值 |
|---|---|
| `internal/models/**` 非测试代码 | 19,622 行（131 文件）— 全部是模型供应商元数据/传输层，**不含持久化实体** |
| `internal/types/**` 非测试代码 | 25,281 行 — 真正的 GORM 实体 + DTO + 枚举都在这里 |
| `internal/application/repository/**` 非测试代码 | 29,940 行 / 53 文件（最大 `wiki_page.go` 1489、`chunk.go` 1409、`knowledge.go` 1192） |
| `migrations/versioned/**` | 121 个 `.up.sql`（`000000_init` 仅 205 行 / 7 表，其余全为 ALTER） |
| `migrations/sqlite/**` | 40 个 `.up.sql`（`000000_init` 1122 行 / 47 表，压缩基线） |
| 迁移 SQL 总行数 | 8,832 行 |
| `migrations/mysql/**` | 未接线（README 自述不被任何代码执行，仅 233 行一次性建表脚本） |
| `migrations/paradedb/**` | 未接线（`00-init-db.sql` 220 行 + `01-migrate-to-paradedb.sql` 69 行，内容为注释掉的示例数据与 `SELECT COUNT(*)` 验证语句） |

**Schema 漂移实测**

```
Postgres 独有表（SQLite 缺失）: embeddings, mcp_metadata, organization_members, wiki_log_entries
  └ mcp_metadata  = 活跃功能，缺表 → S1
  └ organization_members / wiki_log_entries = 已废弃，未清理
  └ embeddings     = 检索引擎专属，Postgres 后端专用，合理
索引数: versioned 240 / sqlite 215
带 deleted_at 的表: 33
  ├ 模型有 gorm.DeletedAt: 25
  ├ 模型无该字段:          2  （message_artifacts=刻意的墓碑设计, knowledge_tags=真债）
  └ 找不到对应实体模型:      5  （im_channels, im_channel_sessions, mcp_services, tenant_skills, tenant_skill_catalog — 实体定义在别处）
  └ 软删表上缺 deleted_at IS NULL 谓词的唯一索引: 5
      tenant_members(sqlite 独有缺陷，唯一会导致业务不可恢复的一个),
      message_artifacts, chunks(seq_id), knowledge_tags(seq_id), knowledge_tags(kb_name)
parity 守卫: 手写 22 表 + 21 表列白名单，mcp_metadata 未列入 → 第 1、2 条至今无人拦截
```

**查询层坏味道计数**（`internal/application/repository` 非测试代码）

- 显式事务块：36 处 `.Transaction(func(tx *gorm.DB)`，未发现绕过 repository 传播事务的 handler/service 直连（service 层有 5 个文件持 `*gorm.DB`，均为 DI 注入且用途受限：`agent_web_pages.go:34`、`knowledge_span_tracker.go:172`、`knowledge_housekeeping.go:76`、`storagebackend.go:31`、`wiki_ingest.go:563`）
- `Preload` 仅 20 处，集中在 `agent_share.go` / `kbshare.go` / `organization.go`；`knowledge.go`、`chunk.go` 全靠手写 JOIN
- 循环内 DB 调用：6 处（`chunk.go:464` Save、`message.go:591` Update、`retriever/sqlite/repository.go:315/356/363`、`stock_watch_diary.go:155` Create）
- 无方言判断的 Postgres 专有 SQL：3 处（`wiki_page.go:370`、`organization.go:96`、`user.go:265`），同文件另有 5 处已正确做方言分支
- 全表 `SELECT *` + `ILIKE '%…%'` 关键词搜索：`chunk.go:222`、`session.go:196`、`wiki_page.go:1394`、`organization.go:96`、`user.go:265`（除 `wiki_pages` 有 GIN `to_tsvector` 索引外，其余均无全文检索能力）
- 分页 count 与 data 查询共用同一 scope 闭包（`chunk.go:275`、`knowledge.go:233`、`session.go:253`），未发现 count/data 不一致
