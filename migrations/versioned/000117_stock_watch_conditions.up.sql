-- Migration: 000117_stock_watch_conditions
-- User-authored numeric conditions on tracked symbols, plus an audit trail of
-- the notifications those conditions produce.
--
-- Why this exists at all: the B1 buy-point judgement was back-tested and had
-- no positive edge (see the feat/watchlist-pool history), so this feature is
-- deliberately NOT an algorithm. The user draws simple numeric lines; the
-- daily job watches them and reports crossings honestly. There is no scoring
-- and no recommendation in any table here.
--
-- stock_watch_conditions: one condition = (field, op, value) on one symbol.
--
--   last_satisfied BOOLEAN NULL  — the last decided state. NULL means "never
--     evaluated", which is load-bearing: a condition that is already true when
--     it is added must NOT fire (the user already knew), so the first
--     evaluation only records the state.
--   last_eval_date DATE NULL     — the trading-day watermark. On a holiday or
--     during an ETL outage the reading's date does not advance, so every
--     condition is skipped and the run stays silent instead of inventing a
--     crossing out of stale data.
--
-- The unique index makes a double-click idempotent: the same threshold on the
-- same symbol is one row, not two that would both fire.
DO $$ BEGIN RAISE NOTICE '[Migration 000117] Creating table: stock_watch_conditions'; END $$;

CREATE TABLE IF NOT EXISTS stock_watch_conditions (
    id              VARCHAR(36)      PRIMARY KEY,
    user_id         VARCHAR(36)      NOT NULL,
    tenant_id       BIGINT           NOT NULL,
    thscode         VARCHAR(16)      NOT NULL,
    field           VARCHAR(24)      NOT NULL,
    op              VARCHAR(8)       NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    last_satisfied  BOOLEAN,
    last_eval_date  DATE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Idempotency key: add-twice = one condition.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_watch_conditions_unique
    ON stock_watch_conditions (user_id, tenant_id, thscode, field, op, value);

-- Primary read path: the per-symbol list on the conditions page.
CREATE INDEX IF NOT EXISTS idx_stock_watch_conditions_user_tenant_symbol
    ON stock_watch_conditions (user_id, tenant_id, thscode);

-- Daily-job path: "every (user, tenant) that has conditions", and the
-- tenant-scoped cleanup when a workspace is deleted.
CREATE INDEX IF NOT EXISTS idx_stock_watch_conditions_tenant_user
    ON stock_watch_conditions (tenant_id, user_id);

DO $$ BEGIN RAISE NOTICE '[Migration 000117] stock_watch_conditions table ready'; END $$;

-- stock_watch_notifications: what the system tried to tell the user.
--
-- The event row proves a crossing happened; this row proves whether anyone was
-- told, how many times, and why it failed. It is written even when the webhook
-- is unset (ok=false, error explains it) so "系统说会推送，结果没收到" is
-- answerable after the fact instead of invisible.
DO $$ BEGIN RAISE NOTICE '[Migration 000117] Creating table: stock_watch_notifications'; END $$;

CREATE TABLE IF NOT EXISTS stock_watch_notifications (
    id         BIGSERIAL    PRIMARY KEY,
    user_id    VARCHAR(36)  NOT NULL,
    tenant_id  BIGINT       NOT NULL,
    kind       VARCHAR(32)  NOT NULL,
    payload    TEXT         NOT NULL DEFAULT '',
    ok         BOOLEAN      NOT NULL DEFAULT FALSE,
    error      TEXT         NOT NULL DEFAULT '',
    attempts   INTEGER      NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stock_watch_notifications_user_tenant_created
    ON stock_watch_notifications (user_id, tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_watch_notifications_tenant_id
    ON stock_watch_notifications (tenant_id);

DO $$ BEGIN RAISE NOTICE '[Migration 000117] stock_watch_notifications table ready'; END $$;
