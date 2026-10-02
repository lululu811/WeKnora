-- Migration: 000120_stock_watch_diaries
-- One row per (symbol, trading day): the daily LLM observation diary.
--
-- Why a new table rather than more rows in stock_watch_events. An event is
-- "the pool changed once" — added, state_changed, note_changed,
-- condition_triggered, and its whole design is append-only + generic `kind`
-- so that reading the log answers "why did this row become what it is". A
-- diary is a different shape of fact: one per trading day, a few hundred
-- characters, carrying a snapshot of the readings the model was shown. Filing
-- those as events would put ~250 rows per symbol per year into the stream
-- that exists to be read, and the stream's usefulness dies with its volume.
--
-- trade_date, not written_at. The job runs at 08:30 on D+1 and reports D's
-- close. This is the same distinction stock_watch_events.eval_date was added
-- for (000118) after the "今日触发" badge could never light up because it
-- compared created_at. A diary keyed on insert time would relabel a
-- suspended symbol's week-old reading as this morning's, which is a fabricated
-- observation, not a stale one.
--
-- verdict is one column holding two value sets, not two columns: buy|hold|sell
-- for an observing symbol, keep|tighten|exit for a held one, and none shared
-- by both meaning "not enough data to decide". They answer the same question
-- in the two states a symbol can be in, and `none` is the reason they have to
-- share a column — a model handed an unreadable set of numbers will invent a
-- verdict unless the schema offers it an honest way to decline.
--
-- The model writes these rows. It never writes stock_watches.state: the state
-- machine is the human's position and nothing else may move it. Adoption is a
-- separate, explicit act.
DO $$ BEGIN RAISE NOTICE '[Migration 000120] Creating table: stock_watch_diaries'; END $$;

CREATE TABLE IF NOT EXISTS stock_watch_diaries (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    trade_date DATE        NOT NULL,
    verdict    VARCHAR(16) NOT NULL,
    confidence SMALLINT    NOT NULL DEFAULT 0,
    reasons    TEXT        NOT NULL DEFAULT '',
    body       TEXT        NOT NULL DEFAULT '',
    model_id   VARCHAR(64) NOT NULL DEFAULT '',
    readings   TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The idempotency key, and the reason a rerun of the same trading day
-- overwrites instead of appending: one symbol, one trading day, one diary.
-- Without it a re-run, a retry after a partial failure, or a manual
-- "generate again" would leave two rows claiming to be the same observation,
-- and the UI would have to decide which one to show — which is exactly the
-- de-duplication that always misses one case.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_watch_diaries_unique
    ON stock_watch_diaries (user_id, tenant_id, thscode, trade_date);

-- Read path for the drawer: "this symbol's diaries, newest first".
-- The unique index above cannot serve it — its leading columns are all
-- equality-only, and the read adds a range on trade_date.
CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_symbol_date
    ON stock_watch_diaries (user_id, tenant_id, thscode, trade_date DESC);

-- Cleanup path when a workspace is deleted: bulk DELETE WHERE tenant_id = ?.
-- Without it, deleting a workspace scans the whole table, and a diary table
-- grows by one row per tracked symbol per day.
CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_tenant_id
    ON stock_watch_diaries (tenant_id);

DO $$ BEGIN RAISE NOTICE '[Migration 000120] stock_watch_diaries table ready'; END $$;
