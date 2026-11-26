ALTER TABLE stock_watch_diaries ADD COLUMN final_score NUMERIC(5,2);
ALTER TABLE stock_watch_diaries ADD COLUMN rank INTEGER;
ALTER TABLE stock_watch_diaries ADD COLUMN scores TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_scored
    ON stock_watch_diaries (trade_date DESC, final_score DESC)
    WHERE final_score IS NOT NULL;
