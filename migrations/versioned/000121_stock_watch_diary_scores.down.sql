DROP INDEX IF EXISTS idx_stock_watch_diaries_scored;
ALTER TABLE stock_watch_diaries DROP COLUMN IF EXISTS scores;
ALTER TABLE stock_watch_diaries DROP COLUMN IF EXISTS rank;
ALTER TABLE stock_watch_diaries DROP COLUMN IF EXISTS final_score;
