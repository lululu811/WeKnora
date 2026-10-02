-- Migration 000039: the daily LLM observation diary table
--
-- SQLite twin of versioned migration 000120; see it for the full rationale
-- (why a table of its own rather than more events, why trade_date is the
-- trading day rather than the insert time, and why one verdict column holds
-- two value sets with `none` shared between them).

CREATE TABLE IF NOT EXISTS stock_watch_diaries (
    id         INTEGER     PRIMARY KEY AUTOINCREMENT,
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
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The idempotency key: one symbol, one trading day, one diary. A re-run
-- overwrites instead of appending, so a retry or a manual regeneration cannot
-- leave two rows both claiming to be the same observation.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_watch_diaries_unique
    ON stock_watch_diaries (user_id, tenant_id, thscode, trade_date);

-- The drawer's read path ("this symbol's diaries, newest first"). The unique
-- index cannot serve it: its leading columns are equality-only and the read
-- adds a range on trade_date.
CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_symbol_date
    ON stock_watch_diaries (user_id, tenant_id, thscode, trade_date DESC);

-- Workspace cleanup: bulk DELETE WHERE tenant_id = ?.
CREATE INDEX IF NOT EXISTS idx_stock_watch_diaries_tenant_id
    ON stock_watch_diaries (tenant_id);
