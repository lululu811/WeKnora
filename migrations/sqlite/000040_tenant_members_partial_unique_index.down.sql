DROP INDEX IF EXISTS idx_tenant_members_user_tenant_unique;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_members_user_tenant_unique
    ON tenant_members(user_id, tenant_id);
