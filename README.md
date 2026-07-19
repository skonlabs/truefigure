# TrueFigure SDK

Implementation of the frozen, certified TrueFigure contract: API server, ingestion
pipeline, workers, reference measurement engine, ops CLI, and Python client. **All
data lives in Supabase** (Postgres for the 31 certified tables; Storage for file
artifacts). The application layer is a conventional FastAPI service hosted
separately; Supabase is the data platform, not the runtime.

> Build status: **Phase 0 (Bootstrap) complete and gated.** Phases P1–P9 are in
> progress. Two authoritative inputs are required to proceed past P0 and are not
> yet in the workspace — see `docs/sdk_discrepancies.md` §Blocking.

## Repository layout
```
openapi/openapi.yaml          # 29 HTTP paths (authoritative surface)
schemas/events.schema.json    # wire event contract, Draft 2020-12 (runtime-loaded)
registry/error-codes.json     # closed TF-<PLANE>-<NNN> registry (26 codes)
supabase/
  migrations/
    0001_certified_schema.sql  # db/schema.sql VERBATIM — never edited
    0002_postgrest_lockdown.sql# RLS deny-all + privilege revokes, all 31 tables
    0003_storage_buckets.sql   # private buckets: import-uploads, import-error-reports, report-artifacts
  verification.sql             # the 7/7 certification battery
tests/sql/attack_tests.sql     # 3 integrity attacks that must be blocked
tests/sql/lockdown_test.sql    # proves anon is denied on every table
python/                        # reference client (19 tests) — extended per phase
src/                           # FastAPI server + workers + engine (P1+)
ops/                           # Console-substitute CLI (P1+)
ci.sh                          # the gate: db reset + battery + attacks + lockdown + lint + types + tests + conformance
```

## Bring-up

### Primary path — Supabase local stack
```bash
supabase start                 # local Postgres + Storage + PostgREST
supabase db reset              # applies supabase/migrations/* in order
psql "$DATABASE_URL" -f supabase/verification.sql   # every row must PASS (7/7)
```
`supabase db reset` runs 0001 (certified schema, which self-seeds the platform
system user id=1), then the lockdown and bucket migrations.

### Fallback path — plain PostgreSQL 16 (no Supabase CLI)
The schema is standard PostgreSQL, so a bare PG16 works for everything except
Storage (stubbed to the filesystem) and the `anon`/`authenticated` PostgREST
roles (created by `ci.sh` so the lockdown gate stays meaningful):
```bash
createdb truefigure
psql -d truefigure -f supabase/migrations/0001_certified_schema.sql
psql -d truefigure -f supabase/migrations/0002_postgrest_lockdown.sql
psql -d truefigure -f supabase/migrations/0003_storage_buckets.sql   # buckets skipped (no storage schema)
psql -d truefigure -f supabase/verification.sql
```

### Hosted Supabase (this project)
Migrations 0001–0003 are already applied to the hosted **TrueFigure** project.
Point the server at it with a **direct/session** connection string (DDL, workers,
partition creation need it; the transaction pooler drops prepared statements).
Connection strings and the service key live in env/secret config only — never in
the repo, fixtures, or logs.

## The gate
```bash
./ci.sh          # fallback path (manages a local tf_ci database)
DATABASE_URL=postgres://... ./ci.sh   # against Supabase or any PG16
```
`ci.sh` fails on any red. Gates for phases not yet built print `PENDING (Pn)` and
do not fake success; the Definition of Done (§7 of the build spec) requires every
section green, coverage ≥ 90%, and the conformance matrix at 42/42.

## Partitions (ops)
Monthly `events` partitions are provisioned ahead of time by the ops CLI over the
direct connection (`ops partitions ensure --months N`); `events_default` catches
gaps. In production this is a scheduled invocation (or Supabase `pg_cron`).

## Auth
Authorization is TrueFigure API keys (`tf_live_`/`tf_test_`, hash-stored, secret
shown once at issuance) — **not** Supabase Auth (reserved for the future Console).
Tenancy and key issuance are Console/ops operations, never HTTP routes.
