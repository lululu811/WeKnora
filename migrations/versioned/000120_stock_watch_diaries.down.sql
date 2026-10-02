-- Reverse of 000120_stock_watch_diaries.
--
-- Dropping the table is the whole reversal. Nothing else references it: no
-- foreign key exists (a watchlist symbol is not a child of any WeKnora
-- aggregate, and the thscode space lives in the Python service's DuckDB), and
-- the events written when a verdict is adopted reference a symbol, not a diary
-- row. Those events are history that stays true after the diary it responded
-- to is gone, the same way a state_changed event outlives the page that
-- recorded it.
DO $$ BEGIN RAISE NOTICE '[Migration 000120] Dropping stock_watch_diaries'; END $$;

DROP INDEX IF EXISTS idx_stock_watch_diaries_tenant_id;
DROP INDEX IF EXISTS idx_stock_watch_diaries_symbol_date;
DROP INDEX IF EXISTS idx_stock_watch_diaries_unique;
DROP TABLE IF EXISTS stock_watch_diaries;

DO $$ BEGIN RAISE NOTICE '[Migration 000120] stock_watch_diaries reverted'; END $$;
