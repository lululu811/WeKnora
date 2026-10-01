-- Migration 000034: stock_watches, the per-(user, tenant) watchlist
--
-- SQLite twin of versioned migration 000115; see it for the full rationale
-- (composite PK on (user_id, tenant_id, thscode), no FK, no cost columns).
--
-- Kept as its own numbered migration rather than folded into the 000000
-- baseline: the baseline is a snapshot of the schema at Lite's v1, and adding
-- new tables there would mean two sources of truth for the same DDL.

CREATE TABLE IF NOT EXISTS stock_watches (
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    exchange   VARCHAR(8)  NOT NULL DEFAULT '',
    sort_order INTEGER     NOT NULL DEFAULT 0,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id, thscode)
);

CREATE INDEX IF NOT EXISTS idx_stock_watches_user_tenant_order
    ON stock_watches (user_id, tenant_id, sort_order, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watches_tenant_id
    ON stock_watches (tenant_id);
