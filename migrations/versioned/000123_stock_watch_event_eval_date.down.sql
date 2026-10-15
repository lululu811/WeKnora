-- Reverse of 000123_stock_watch_event_eval_date.
DO $$ BEGIN RAISE NOTICE '[Migration 000123] Dropping stock_watch_events.eval_date'; END $$;

ALTER TABLE stock_watch_events DROP COLUMN IF EXISTS eval_date;

DO $$ BEGIN RAISE NOTICE '[Migration 000123] stock_watch_events.eval_date dropped'; END $$;
