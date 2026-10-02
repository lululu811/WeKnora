-- Migration 000038: widen the tracking reason to varchar(500) on both tables
--
-- SQLite twin of versioned migration 000119; see it for the full rationale
-- (TEXT vs a declared width, and why stock_watch_events.note must change
-- together with stock_watches.note).
--
-- Why this file rebuilds tables when the PostgreSQL one only alters a column:
-- SQLite's ALTER TABLE has no "change the declared type of an existing
-- column". The three available shapes are add / rename / drop, so widening a
-- column means: add a new column, copy into it, drop the old one, rename.
--
-- A note on the declared width itself: SQLite does not enforce VARCHAR(n) at
-- all. A varchar(200) column accepts 500 characters without complaint, and
-- pragma_table_info still reports varchar(200) afterwards. So the rebuild below
-- is not what makes long reasons work on Lite — that already worked, by
-- accident. What the rebuild buys is that the two schemas finally SAY the same
-- thing. Without it, the next person to read migrations/sqlite/ reasonably
-- concludes the width is still 200 and has to rediscover the enforcement gap
-- from scratch. A schema that lies about a limit is worse than one that is
-- merely too strict.
--
-- Kept as its own numbered migration rather than folded into 000034/000035:
-- existing Lite databases already ran those, and rewriting them would leave
-- them with a table the migration file claims is different.

-- ---- stock_watches -------------------------------------------------------
--
-- Column order is preserved exactly, and the two indexes on this table are
-- recreated after the swap: a DROP TABLE takes its indexes with it, and
-- 000034's idx_stock_watches_user_tenant_order is what serves the pool's
-- primary read (list this user's symbols in display order).
CREATE TABLE IF NOT EXISTS stock_watches__note_widen (
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    exchange   VARCHAR(8)  NOT NULL DEFAULT '',
    sort_order INTEGER     NOT NULL DEFAULT 0,
    state      VARCHAR(16) NOT NULL DEFAULT 'observing',
    note       VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id, thscode)
);

INSERT INTO stock_watches__note_widen
    (user_id, tenant_id, thscode, name, exchange, sort_order, state, note, created_at, updated_at)
SELECT
    user_id, tenant_id, thscode, name, exchange, sort_order, state, note, created_at, updated_at
FROM stock_watches;

DROP TABLE stock_watches;
ALTER TABLE stock_watches__note_widen RENAME TO stock_watches;

CREATE INDEX IF NOT EXISTS idx_stock_watches_user_tenant_order
    ON stock_watches (user_id, tenant_id, sort_order, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watches_tenant_id
    ON stock_watches (tenant_id);

-- ---- stock_watch_events --------------------------------------------------
--
-- No primary key to preserve here beyond the rowid that CREATE TABLE WITHOUT
-- ROWID would change, so the copy below is a plain SELECT * ordered by id to
-- keep autoincrement monotonic rather than letting SQLite reassign rowids in
-- an order that may differ from insert order.
CREATE TABLE IF NOT EXISTS stock_watch_events__note_widen (
    id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    from_state VARCHAR(16) NOT NULL DEFAULT '',
    to_state   VARCHAR(16) NOT NULL DEFAULT '',
    note       VARCHAR(500) NOT NULL DEFAULT '',
    eval_date  DATE,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO stock_watch_events__note_widen
    (id, user_id, tenant_id, kind, thscode, from_state, to_state, note, eval_date, created_at)
SELECT
    id, user_id, tenant_id, kind, thscode, from_state, to_state, note, eval_date, created_at
FROM stock_watch_events
ORDER BY id;

DROP TABLE stock_watch_events;
ALTER TABLE stock_watch_events__note_widen RENAME TO stock_watch_events;

CREATE INDEX IF NOT EXISTS idx_stock_watch_events_user_tenant_created
    ON stock_watch_events (user_id, tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watch_events_tenant_id
    ON stock_watch_events (tenant_id);
