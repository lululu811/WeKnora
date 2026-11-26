DROP INDEX IF EXISTS idx_stock_watch_diaries_scored;
ALTER TABLE stock_watch_diaries DROP COLUMN scores;
ALTER TABLE stock_watch_diaries DROP COLUMN rank;
ALTER TABLE stock_watch_diaries DROP COLUMN final_score;
