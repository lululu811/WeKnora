-- Reverse of 000119_stock_watch_note_text: put the varchar(200) ceiling back.
--
-- DATA LOSS, DELIBERATE AND DOCUMENTED: a note longer than 200 characters is
-- truncated to its first 200 runes by the LEFT() below, exactly as
-- 000064_principal_model and 000066_expand_knowledge_span_name do when they
-- reverse their own widenings. The alternative — omitting USING and letting
-- PostgreSQL raise "value too long for type character varying(200)" — turns a
-- rollback into a hard failure that leaves the migration dirty and the
-- deployment stuck, which is a worse outcome than losing the tail of a note
-- that a human wrote as free text.
--
-- If a rollback must not lose anything, take a pg_dump first. That is true of
-- every down migration in this directory; this one is merely the one where it
-- is easy to forget, because the up migration cannot lose anything.
DO $$ BEGIN RAISE NOTICE '[Migration 000119] Restoring varchar(200) on stock_watch notes'; END $$;

ALTER TABLE stock_watches
    ALTER COLUMN note TYPE VARCHAR(200) USING LEFT(note, 200);

ALTER TABLE stock_watch_events
    ALTER COLUMN note TYPE VARCHAR(200) USING LEFT(note, 200);

DO $$ BEGIN RAISE NOTICE '[Migration 000119] stock_watch notes reverted to varchar(200)'; END $$;
