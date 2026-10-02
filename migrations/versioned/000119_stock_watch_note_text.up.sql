-- Migration: 000119_stock_watch_note_text
-- Widen the tracking reason from varchar(200) to TEXT on both tables that
-- hold it.
--
-- Why TEXT and not varchar(500): the reason is free text whose useful length
-- is a moving target, and every width we declare is a number we will have to
-- migrate again the first time a good reason outgrows it. In PostgreSQL TEXT
-- and varchar(n) have identical storage (both TOASTable); the only thing
-- varchar(n) buys is a constraint we do not actually want at the storage
-- layer. The real guard is the application: service.Update / service.Add
-- reject a note longer than types.MaxStockWatchNoteLen with
-- ErrStockWatchNoteTooLong, before this column would ever see it. Dropping
-- the database-side ceiling does not drop the ceiling, it moves it to the
-- layer that can report a useful error.
--
-- Both tables are changed, not just stock_watches. stock_watch_events.note
-- is the snapshot of the reason AT THE MOMENT of a change ("why did this row
-- become what it is"); if only the main table widened, a 500-character reason
-- would land in the pool row and be silently refused by the events table,
-- which is precisely the case where the reason matters most — being unable to
-- trace why a symbol entered the pool at all.
--
-- Widening a column to TEXT is metadata-only in PostgreSQL: no table rewrite,
-- no reindex. The paired SQLite migration is the expensive one, because
-- SQLite cannot alter a column's declared width at all.
DO $$ BEGIN RAISE NOTICE '[Migration 000119] Widening stock_watch notes to TEXT'; END $$;

ALTER TABLE stock_watches
    ALTER COLUMN note TYPE TEXT;

ALTER TABLE stock_watch_events
    ALTER COLUMN note TYPE TEXT;

-- The 'observing' DEFAULT and the '' DEFAULT survive ALTER COLUMN TYPE: the
-- defaults are stored as expressions on the column and are re-checked against
-- the new type, not dropped. Asserting it here would be redundant with the
-- repository tests that read these columns; the notice records the intent.
DO $$ BEGIN RAISE NOTICE '[Migration 000119] stock_watch notes are now TEXT'; END $$;
