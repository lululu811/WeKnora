-- Reverse of 000117_stock_watch_conditions.
DO $$ BEGIN RAISE NOTICE '[Migration 000117] Dropping stock_watch_notifications and stock_watch_conditions'; END $$;

DROP INDEX IF EXISTS idx_stock_watch_notifications_tenant_id;
DROP INDEX IF EXISTS idx_stock_watch_notifications_user_tenant_created;
DROP TABLE IF EXISTS stock_watch_notifications;

DROP INDEX IF EXISTS idx_stock_watch_conditions_tenant_user;
DROP INDEX IF EXISTS idx_stock_watch_conditions_user_tenant_symbol;
DROP INDEX IF EXISTS idx_stock_watch_conditions_unique;
DROP TABLE IF EXISTS stock_watch_conditions;

DO $$ BEGIN RAISE NOTICE '[Migration 000117] stock_watch_conditions reverted'; END $$;
