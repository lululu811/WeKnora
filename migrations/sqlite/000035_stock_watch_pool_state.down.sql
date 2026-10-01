-- Reverse of 000035_stock_watch_pool_state.
DROP INDEX IF EXISTS idx_stock_watch_events_tenant_id;
DROP INDEX IF EXISTS idx_stock_watch_events_user_tenant_created;
DROP TABLE IF EXISTS stock_watch_events;

ALTER TABLE stock_watches DROP COLUMN note;
ALTER TABLE stock_watches DROP COLUMN state;
