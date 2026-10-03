-- Drop plain unique index and recreate with WHERE deleted_at IS NULL to allow
-- re-inviting previously removed members. Matches versioned/000043_tenant_rbac.
DROP INDEX IF EXISTS idx_tenant_members_user_tenant_unique;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_members_user_tenant_unique
    ON tenant_members(user_id, tenant_id)
    WHERE deleted_at IS NULL;
