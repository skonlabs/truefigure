-- =============================================================================
-- lockdown_test.sql — proves the PostgREST seal at the exact mechanism PostgREST
-- relies on: it authenticates as the `anon` (or `authenticated`) role, so we
-- BECOME that role and confirm every public table denies access.
--
-- Deny-by-default here means the role has NO table privilege (revoked in 0002)
-- AND RLS is enabled with no policy — either alone would return no rows; we
-- assert the stronger privilege denial. Raises (non-zero exit) on any breach.
--
-- Requires an `anon` role to exist (Supabase has it; the plain-Postgres fallback
-- path in ci.sh creates a NOLOGIN `anon` role before applying migrations).
-- =============================================================================
DO $seal$
DECLARE
  r RECORD;
  breach text[] := '{}';
BEGIN
  FOR r IN
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
    ORDER BY c.relname
  LOOP
    BEGIN
      SET LOCAL ROLE anon;
      EXECUTE format('SELECT 1 FROM public.%I LIMIT 1', r.relname);
      -- Reached here => anon was permitted to read the table: a breach.
      RESET ROLE;
      breach := array_append(breach, r.relname);
    EXCEPTION
      WHEN insufficient_privilege THEN
        RESET ROLE;  -- expected: permission denied for table
    END;
  END LOOP;

  IF array_length(breach, 1) > 0 THEN
    RAISE EXCEPTION 'POSTGREST LOCKDOWN BREACH: anon can read %', breach;
  END IF;
  RAISE NOTICE 'PostgREST lockdown verified: anon denied on all % public tables',
    (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind IN ('r','p'));
END $seal$;
