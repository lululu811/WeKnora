-- Reverse of 000039_stock_watch_diaries.
--
-- Nothing else references this table (no foreign key: a watchlist symbol is
-- not a child of any WeKnora aggregate, and the thscode space lives in the
-- Python service's DuckDB). The verdict_accepted / verdict_ignored events
-- written to stock_watch_events when a verdict is adopted stay where they
-- are — they record what the human did, which remains true after the diary
-- that prompted it is gone.

DROP INDEX IF EXISTS idx_stock_watch_diaries_tenant_id;
DROP INDEX IF EXISTS idx_stock_watch_diaries_symbol_date;
DROP INDEX IF EXISTS idx_stock_watch_diaries_unique;
DROP TABLE IF EXISTS stock_watch_diaries;
