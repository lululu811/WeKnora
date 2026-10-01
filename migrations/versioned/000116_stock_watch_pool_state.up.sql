-- Migration: 000116_stock_watch_pool_state
-- Turns the watchlist (000115) into a tracking pool: a per-symbol state, a
-- user note, and an append-only event log.
--
-- state: the user's own stance on a symbol, moved only by the user.
--   observing -> holding | dropped
--   triggered -> holding | dropped | observing
--   holding   -> observing | dropped
--   dropped   -> observing
-- Nothing in this migration or the API promotes a row to `triggered`: that is
-- reserved for a buy-point trigger, which does not exist yet. Keeping the
-- column generic now means that future writer needs no schema change.
-- NOT NULL DEFAULT 'observing' so every row that already exists (and any
-- insert that forgets the column) lands in the only state that is true for a
-- freshly tracked symbol.
--
-- note: the user's reason for tracking the symbol. Free text, deliberately
-- short. Same NOT NULL DEFAULT '' reasoning: "no note" is the empty string,
-- not NULL, so the read path never has to decide what NULL means.
--
-- Deliberately still absent: cost basis / lots / P&L. `holding` is a stance,
-- not a position size — see types.StockWatch for why a nullable float column
-- would emit wrong P&L for any split-adjusted holding.
DO $$ BEGIN RAISE NOTICE '[Migration 000116] Adding pool state/note to stock_watches'; END $$;

ALTER TABLE stock_watches ADD COLUMN IF NOT EXISTS state VARCHAR(16) NOT NULL DEFAULT 'observing';
ALTER TABLE stock_watches ADD COLUMN IF NOT EXISTS note  VARCHAR(200) NOT NULL DEFAULT '';

-- stock_watch_events: the pool's memory.
--
-- Append-only by construction: nothing in the application updates or deletes a
-- row here. Each row is a fact ("this symbol entered the pool", "the user moved
-- it to holding", "the user rewrote the reason"), never a current-state mirror
-- — so reading the log answers "why is this row the way it is", which the
-- stock_watches row itself cannot answer once the moment has passed.
--
-- `kind` is a generic VARCHAR on purpose. Today: added | state_changed |
-- note_changed. A later "buy point triggered" writer must not need a migration,
-- so the schema refuses to bake the current three into a type.
--
-- `note` on the event is a snapshot at the moment of the change, not a
-- reference to the live note — a note the user later rewrites must not
-- retroactively change what the history says the reason was at that time.
--
-- from_state/to_state are filled only when the state transition *is* the event;
-- for `added` from_state is '' and to_state is the initial state, for
-- note_changed both are ''. Empty string, not NULL, for the same reason as above.
--
-- `id` is a surrogate key here, unlike stock_watches: events are not addressed
-- by the caller, they are a stream, and two events written in the same
-- millisecond (a state + note change in one request) must stay ordered.
--
-- User-scoped exactly like stock_watches: the same human in two workspaces has
-- two pools and therefore two independent histories.
DO $$ BEGIN RAISE NOTICE '[Migration 000116] Creating table: stock_watch_events'; END $$;

CREATE TABLE IF NOT EXISTS stock_watch_events (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    from_state VARCHAR(16) NOT NULL DEFAULT '',
    to_state   VARCHAR(16) NOT NULL DEFAULT '',
    note       VARCHAR(200) NOT NULL DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Primary read path: the pool-wide feed, newest first — mirrors the
-- repository's WHERE (user_id, tenant_id) ORDER BY created_at DESC. A
-- per-symbol read (`?thscode=`) also rides this index: the pair is the prefix,
-- so it narrows to one user's events and the thscode filter is a cheap recheck.
CREATE INDEX IF NOT EXISTS idx_stock_watch_events_user_tenant_created
    ON stock_watch_events (user_id, tenant_id, created_at);

-- Cleanup path when a workspace is deleted: bulk DELETE WHERE tenant_id = ?
CREATE INDEX IF NOT EXISTS idx_stock_watch_events_tenant_id
    ON stock_watch_events (tenant_id);

DO $$ BEGIN RAISE NOTICE '[Migration 000116] stock_watch_events table ready'; END $$;
