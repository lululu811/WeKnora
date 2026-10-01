-- Reverse of 000116_stock_watch_pool_state.
--
-- Order matters: the event table references nothing, but dropping the columns
-- before the table would leave a window where events exist describing a pool
-- that no longer has the state they talk about.
DO $$ BEGIN RAISE NOTICE '[Migration 000116] Dropping stock_watch_events and pool columns'; END $$;

DROP INDEX IF EXISTS idx_stock_watch_events_tenant_id;
DROP INDEX IF EXISTS idx_stock_watch_events_user_tenant_created;
DROP TABLE IF EXISTS stock_watch_events;

ALTER TABLE stock_watches DROP COLUMN IF EXISTS note;
ALTER TABLE stock_watches DROP COLUMN IF EXISTS state;

DO $$ BEGIN RAISE NOTICE '[Migration 000116] stock_watch_pool_state reverted'; END $$;
