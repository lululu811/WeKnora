-- Reverse of 000034_stock_watches.

DROP INDEX IF EXISTS idx_stock_watches_tenant_id;
DROP INDEX IF EXISTS idx_stock_watches_user_tenant_order;
DROP TABLE IF EXISTS stock_watches;
