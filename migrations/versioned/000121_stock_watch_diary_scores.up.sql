-- Add scoring and ranking columns to the daily observation diary.
--
-- final_score: the grading job's 0-100 aggregate across Q1-Q18. NULL until the
-- grading job has run for this trading day; a diary written by the observation
-- job alone carries no score.
--
-- rank: position within the same trade_date across all (user, tenant) scopes.
-- 1 is the best score. NULL until ranked.
--
-- scores: JSON of the 18 individual question scores and per-question reasons,
-- kept as text so the schema does not need 18 new columns. The grading job
-- writes it; the page reads it for the detail drawer.
ALTER TABLE stock_watch_diaries ADD COLUMN IF NOT EXISTS final_score NUMERIC(5,2);
ALTER TABLE stock_watch_diaries ADD COLUMN IF NOT EXISTS rank INTEGER;
ALTER TABLE stock_watch_diaries ADD COLUMN IF NOT EXISTS scores TEXT NOT NULL DEFAULT '';

-- Partial index for the ranking query: only rows that have been scored.
CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_scored
    ON stock_watch_diaries (trade_date DESC, final_score DESC)
    WHERE final_score IS NOT NULL;
