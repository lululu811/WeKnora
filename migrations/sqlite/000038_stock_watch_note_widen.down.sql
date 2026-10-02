-- Reverse of 000038_stock_watch_note_widen: restore varchar(200) on both note
-- columns.
--
-- The rebuild is the mirror image of the up migration, and the truncation is
-- the same deliberate choice made in 000064 and 000066: notes longer than 200
-- characters are cut to their first 200 rather than aborting the rollback.
-- SQLite would otherwise happily keep the extra characters (it never enforced
-- the width in the first place), so the truncation here is about making the
-- declared schema honest again, not about preventing a storage error.

CREATE TABLE IF NOT EXISTS stock_watches__note_narrow (
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    exchange   VARCHAR(8)  NOT NULL DEFAULT '',
    sort_order INTEGER     NOT NULL DEFAULT 0,
    state      VARCHAR(16) NOT NULL DEFAULT 'observing',
    note       VARCHAR(200) NOT NULL DEFAULT '',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id, thscode)
);

INSERT INTO stock_watches__note_narrow
    (user_id, tenant_id, thscode, name, exchange, sort_order, state, note, created_at, updated_at)
SELECT
    user_id, tenant_id, thscode, name, exchange, sort_order, state,
    substr(note, 1, 200), created_at, updated_at
FROM stock_watches;

DROP TABLE stock_watches;
ALTER TABLE stock_watches__note_narrow RENAME TO stock_watches;

CREATE INDEX IF NOT EXISTS idx_stock_watches_user_tenant_order
    ON stock_watches (user_id, tenant_id, sort_order, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watches_tenant_id
    ON stock_watches (tenant_id);

CREATE TABLE IF NOT EXISTS stock_watch_events__note_narrow (
    id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    from_state VARCHAR(16) NOT NULL DEFAULT '',
    to_state   VARCHAR(16) NOT NULL DEFAULT '',
    note       VARCHAR(200) NOT NULL DEFAULT '',
    eval_date  DATE,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO stock_watch_events__note_narrow
    (id, user_id, tenant_id, kind, thscode, from_state, to_state, note, eval_date, created_at)
SELECT
    id, user_id, tenant_id, kind, thscode, from_state, to_state,
    substr(note, 1, 200), eval_date, created_at
FROM stock_watch_events
ORDER BY id;

DROP TABLE stock_watch_events;
ALTER TABLE stock_watch_events__note_narrow RENAME TO stock_watch_events;

CREATE INDEX IF NOT EXISTS idx_stock_watch_events_user_tenant_created
    ON stock_watch_events (user_id, tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watch_events_tenant_id
    ON stock_watch_events (tenant_id);
