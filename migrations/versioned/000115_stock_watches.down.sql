-- Reverse of 000115_stock_watches.
DO $$ BEGIN RAISE NOTICE '[Migration 000115] Dropping table: stock_watches'; END $$;

DROP INDEX IF EXISTS idx_stock_watches_tenant_id;
DROP INDEX IF EXISTS idx_stock_watches_user_tenant_order;
DROP TABLE IF EXISTS stock_watches;

DO $$ BEGIN RAISE NOTICE '[Migration 000115] stock_watches table dropped'; END $$;
