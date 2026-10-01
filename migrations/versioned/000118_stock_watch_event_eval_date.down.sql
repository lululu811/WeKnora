-- Reverse of 000118_stock_watch_event_eval_date.
DO $$ BEGIN RAISE NOTICE '[Migration 000118] Dropping stock_watch_events.eval_date'; END $$;

ALTER TABLE stock_watch_events DROP COLUMN IF EXISTS eval_date;

DO $$ BEGIN RAISE NOTICE '[Migration 000118] stock_watch_events.eval_date dropped'; END $$;
