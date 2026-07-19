-- =============================================================================
-- 0003_storage_buckets.sql
-- Private Supabase Storage buckets for the file artifacts referenced by columns
-- in the certified schema:
--   import-uploads       -> import_jobs.upload_path        (single-use signed upload)
--   import-error-reports -> import_jobs.error_report_path  (NDJSON reject reports)
--   report-artifacts     -> reports.artifact_path          (rendered report docs)
-- All PRIVATE (public=false): every object is served via short-lived signed URLs
-- minted by the API; nothing is world-readable.
--
-- Guarded so the plain-Postgres 16 fallback (no `storage` schema) skips silently;
-- in that mode the API's storage layer targets a local/filesystem stub (README).
-- =============================================================================
DO $buckets$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'storage' AND table_name = 'buckets'
  ) THEN
    INSERT INTO storage.buckets (id, name, public)
    VALUES
      ('import-uploads',       'import-uploads',       false),
      ('import-error-reports', 'import-error-reports', false),
      ('report-artifacts',     'report-artifacts',     false)
    ON CONFLICT (id) DO NOTHING;
  END IF;
END $buckets$;
