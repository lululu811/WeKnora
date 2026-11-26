-- 补齐 000119 与 000120 没落地的变更。
--
-- 这个库的 schema_migrations 版本被**手工快进**到 121，于是 119、120、121 三条
-- 迁移被 golang-migrate 永久跳过：版本号说"跑过了"，实际变更一条没做。
--
-- 后果是**新的代码查不存在的东西**：
--   - messages.run_state 不在 → 聊天发消息时 INSERT 报 column does not exist，
--     流式对话整个 500（"流式连接失败: HTTP 500"）；
--   - agent_prompts / agent_run_suspensions 不在 → 会话加载与"待办提示"路径报错；
--   - tenants.assistant_config 不在 → assistant 面板取不到配置。
-- 旧镜像的代码不查这些表/列，所以一直看不出来；换上含 10-14/10-15 提交的新镜像
-- 才炸。
--
-- 000121 的 sessions.kind 已由 000135 补齐（同一原因）。
--
-- 119 与 120 的语句本身全是 IF NOT EXISTS，天然幂等：这里原样重放一次，
-- 对正确执行过它们的库是 no-op。
--
-- down：这些表与列是 119/120 建的，逆序回退时由它们各自的 down 撤。

DO $$ BEGIN RAISE NOTICE '[Migration 000119] Creating agent_prompts and agent_run_suspensions...'; END $$;

-- One row per human-in-the-loop prompt raised by an agent run: a tool
-- approval, an in-conversation OAuth authorization, a question for the user.
-- The row, not process memory, is the source of truth for the answer so any
-- replica can record it. Kind-specific content lives in request / response.
CREATE TABLE IF NOT EXISTS agent_prompts (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    user_id VARCHAR(64) NOT NULL DEFAULT '',
    session_id VARCHAR(36) NOT NULL,
    assistant_message_id VARCHAR(36) NOT NULL DEFAULT '',
    request_id VARCHAR(128) NOT NULL DEFAULT '',
    kind VARCHAR(32) NOT NULL,
    tool_call_id VARCHAR(255) NOT NULL DEFAULT '',
    suspendable BOOLEAN NOT NULL DEFAULT false,
    request JSONB,
    response JSONB,
    status VARCHAR(32) NOT NULL,
    close_reason TEXT NOT NULL DEFAULT '',
    resolved_by VARCHAR(64) NOT NULL DEFAULT '',
    resolved_at TIMESTAMPTZ,
    owner_instance VARCHAR(64) NOT NULL DEFAULT '',
    lease_until TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_prompts_session_status
    ON agent_prompts (session_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_prompts_status_lease
    ON agent_prompts (status, lease_until);
CREATE INDEX IF NOT EXISTS idx_agent_prompts_status_expires
    ON agent_prompts (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_agent_prompts_message
    ON agent_prompts (assistant_message_id);

-- Run state persisted when an agent run blocks on a prompt, so the run can be
-- suspended and later resumed by any replica.
CREATE TABLE IF NOT EXISTS agent_run_suspensions (
    assistant_message_id VARCHAR(36) PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    prompt_id VARCHAR(36) NOT NULL,
    snapshot_version INTEGER NOT NULL,
    snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_run_suspensions_session
    ON agent_run_suspensions (session_id);

ALTER TABLE messages ADD COLUMN IF NOT EXISTS run_state VARCHAR(32) NOT NULL DEFAULT '';

DO $$ BEGIN RAISE NOTICE '[Migration 000119] agent_prompts ready'; END $$;

DO $$ BEGIN RAISE NOTICE '[Migration 000120] Adding tenants.assistant_config...'; END $$;

-- Workspace setting of the WeKnora assistant: display name and the highest
-- access level a member may pick.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS assistant_config JSONB;
