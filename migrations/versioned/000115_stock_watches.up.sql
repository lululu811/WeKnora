-- Migration: 000115_stock_watches
-- Per-(user, tenant) watchlist ("个股追踪 / 持有股观察").
--
-- Why (user_id, tenant_id, thscode) is the whole primary key:
--   * (user_id, tenant_id) — same reasoning as 000047_user_resource_favorites.
--     A user in two workspaces keeps two independent lists; scoping by tenant
--     also makes "tenant deleted -> its rows dropped" a single indexed DELETE.
--   * thscode, not a surrogate id — the only thing a caller can usefully
--     address is "my row for this symbol", so a synthetic id would add a
--     second identity for the same fact. It also makes "add" idempotent: a
--     double-click is an upsert on the PK, not a duplicate row.
--
-- No FK anywhere: a watchlist entry is not a child of any WeKnora aggregate
-- (the symbol space lives in the Python service's DuckDB, a different store
-- that this schema cannot reference). Hydration is done by the caller: the
-- stored thscode is looked up against /api/quotes at read time, and a symbol
-- with no local data renders as an explicit "无数据" row rather than being
-- silently dropped — that distinction is the whole point of keeping the row.
--
-- Deliberately absent: cost basis / lots. See types.StockWatch for why a
-- nullable float column would be a correctness trap rather than a shortcut.
DO $$ BEGIN RAISE NOTICE '[Migration 000115] Creating table: stock_watches'; END $$;

CREATE TABLE IF NOT EXISTS stock_watches (
    user_id    VARCHAR(36) NOT NULL,
    tenant_id  BIGINT      NOT NULL,
    thscode    VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    exchange   VARCHAR(8)  NOT NULL DEFAULT '',
    sort_order INTEGER     NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id, thscode)
);

-- Primary read path: "list this user's symbols in this workspace, in display
-- order". Mirrors the repository's ORDER BY sort_order ASC, created_at ASC so
-- the sort is served by the index rather than a scan + sort.
CREATE INDEX IF NOT EXISTS idx_stock_watches_user_tenant_order
    ON stock_watches (user_id, tenant_id, sort_order, created_at);

-- Cleanup path when a workspace is deleted: bulk DELETE WHERE tenant_id = ?
CREATE INDEX IF NOT EXISTS idx_stock_watches_tenant_id
    ON stock_watches (tenant_id);

DO $$ BEGIN RAISE NOTICE '[Migration 000115] stock_watches table ready'; END $$;
