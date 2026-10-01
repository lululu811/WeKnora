-- Migration 000035: tracking-pool state, note and event log
--
-- SQLite twin of versioned migration 000116; see it for the full rationale
-- (the state machine, why `kind` is generic, why events are append-only and
-- why no cost/P&L column exists).
--
-- Kept as its own numbered migration rather than folded into 000034 or the
-- 000000 baseline: existing Lite databases already ran 000034, and rewriting
-- it would leave them with a table the migration file claims is different.

-- SQLite allows ADD COLUMN ... NOT NULL only with a non-null constant default,
-- which is exactly what we want: existing rows get the only true value for a
-- freshly tracked symbol.
ALTER TABLE stock_watches ADD COLUMN state VARCHAR(16) NOT NULL DEFAULT 'observing';
ALTER TABLE stock_watches ADD COLUMN note  VARCHAR(200) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS stock_watch_events (
    id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    from_state VARCHAR(16) NOT NULL DEFAULT '',
    to_state   VARCHAR(16) NOT NULL DEFAULT '',
    note       VARCHAR(200) NOT NULL DEFAULT '',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stock_watch_events_user_tenant_created
    ON stock_watch_events (user_id, tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watch_events_tenant_id
    ON stock_watch_events (tenant_id);
