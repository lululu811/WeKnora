-- Migration 000036: user-authored numeric conditions + notification audit
--
-- SQLite twin of versioned migration 000117; see it for the full rationale
-- (why this is not an algorithm, why last_satisfied NULL is distinct from
-- false, why last_eval_date is the trading-day watermark).
--
-- Kept as its own numbered migration rather than folded into 000034/000035:
-- existing Lite databases already ran those, and rewriting them would leave
-- them with tables the migration file claims are different.

CREATE TABLE IF NOT EXISTS stock_watch_conditions (
    id              VARCHAR(36) PRIMARY KEY,
    user_id         VARCHAR(36) NOT NULL,
    tenant_id       BIGINT      NOT NULL,
    thscode         VARCHAR(16) NOT NULL,
    field           VARCHAR(24) NOT NULL,
    op              VARCHAR(8)  NOT NULL,
    value           REAL        NOT NULL,
    last_satisfied  BOOLEAN,
    last_eval_date  DATE,
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_watch_conditions_unique
    ON stock_watch_conditions (user_id, tenant_id, thscode, field, op, value);
CREATE INDEX IF NOT EXISTS idx_stock_watch_conditions_user_tenant_symbol
    ON stock_watch_conditions (user_id, tenant_id, thscode);
CREATE INDEX IF NOT EXISTS idx_stock_watch_conditions_tenant_user
    ON stock_watch_conditions (tenant_id, user_id);

CREATE TABLE IF NOT EXISTS stock_watch_notifications (
    id         INTEGER     PRIMARY KEY AUTOINCREMENT,
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    kind       VARCHAR(32) NOT NULL,
    payload    TEXT        NOT NULL DEFAULT '',
    ok         BOOLEAN     NOT NULL DEFAULT 0,
    error      TEXT        NOT NULL DEFAULT '',
    attempts   INTEGER     NOT NULL DEFAULT 0,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stock_watch_notifications_user_tenant_created
    ON stock_watch_notifications (user_id, tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watch_notifications_tenant_id
    ON stock_watch_notifications (tenant_id);
