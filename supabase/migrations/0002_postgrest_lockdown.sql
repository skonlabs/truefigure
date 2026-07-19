-- =============================================================================
-- 0002_postgrest_lockdown.sql
-- PostgREST / multi-tenant seal. Supabase auto-exposes every public table over
-- PostgREST to the `anon` and `authenticated` roles. The TrueFigure API is the
-- ONLY sanctioned reader of this data, so we deny those roles entirely:
--   * enable Row-Level Security on all 31 tables with NO policies  => deny-by-default
--   * revoke every table/sequence privilege from anon/authenticated => defence in depth
-- The application connects as the `postgres`/owner (or service) role, which is
-- NOT subject to RLS, so the server keeps full access. We do NOT FORCE RLS,
-- precisely so the owner-role server is unaffected.
--
-- This migration does NOT touch the certified schema (0001). It is idempotent
-- and runs on both Supabase and a plain Postgres 16 fallback (role/schema guards).
-- =============================================================================

-- Enable RLS on every base/partitioned table in public (covers all 31, and any
-- future events_YYYY_MM partitions automatically at provisioning time).
DO $lock$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')            -- ordinary + partitioned tables
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', r.relname);
  END LOOP;
END $lock$;

-- Revoke PostgREST-facing privileges. Guarded so the plain-Postgres fallback
-- (which has no anon/authenticated roles) still applies cleanly.
DO $revoke$
DECLARE
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I;', role_name);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I;', role_name);
      EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I;', role_name);
      -- Deny privileges on objects created later, too.
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I;', role_name);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I;', role_name);
      -- Keep USAGE on the schema off as well (belt and braces).
      EXECUTE format('REVOKE USAGE ON SCHEMA public FROM %I;', role_name);
    END IF;
  END LOOP;
END $revoke$;
