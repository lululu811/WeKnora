-- 只撤索引。列是 000121 建的，逆序回退时 121 的 down 会撤它。
DROP INDEX IF EXISTS idx_sessions_tenant_kind_user_updated;
