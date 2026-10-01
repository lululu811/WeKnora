-- Migration 000037: stock_watch_events.eval_date
--
-- SQLite twin of versioned migration 000118; see it for the full rationale
-- (CreatedAt is insert time, not the trading day the event is about — the job
-- runs at 08:30 on D+1 and reports D's close, so anything comparing the two is
-- off by one trading day by construction).

ALTER TABLE stock_watch_events ADD COLUMN eval_date DATE;
