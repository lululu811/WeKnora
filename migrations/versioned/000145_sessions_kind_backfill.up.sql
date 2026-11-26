-- 补齐 000121 没落地的变更。
--
-- 有些库的 schema_migrations 版本被快进到了 121，但 121 的实际变更没执行：
-- sessions.kind 列不在表里。会话列表接口查 s.kind → SQLSTATE 42703 → 历史列表
-- 整个 500，前端表现为「对话全没了」（数据其实都在）。
--
-- 121 不会再跑（migrate 认为它已 applied），所以这里用幂等方式补一遍。
-- 对正确执行过 121 的库，这三条都是 no-op。
--
-- down 只撤索引、不撤列：列是 121 建的，逆序回退时 121 的 down 会撤它。
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS kind VARCHAR(32) NOT NULL DEFAULT '';

UPDATE sessions SET kind = 'assistant'
WHERE kind = '' AND agent_config->>'agent_id' = 'builtin-weknora-assistant';

CREATE INDEX IF NOT EXISTS idx_sessions_tenant_kind_user_updated
    ON sessions (tenant_id, kind, user_id, updated_at DESC)
    WHERE deleted_at IS NULL;
