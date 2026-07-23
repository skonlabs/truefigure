# TrueFigure SDK

A complete implementation of the frozen, certified TrueFigure contract: FastAPI
API server, ingestion pipeline, workers, reference measurement engine, ops CLI,
and an extended Python client. **All data lives in Supabase** (Postgres for the
31 certified tables; Storage for file artifacts). The application layer is a
conventional service you host separately; Supabase is the data platform.

> **Status: complete.** `./ci.sh` exits 0 — schema battery 7/7, three attack
> tests blocked, PostgREST lockdown, ruff, mypy --strict, 19 client + 255 server
> tests, **≥97% coverage**, and the **use-case conformance matrix at 50/50**.

## What's implemented
- **29/29 OpenAPI endpoints** (auth, config plane, ingestion, imports, webhooks,
  read plane) — every path+verb exercised by a contract test.
- **Ingestion**: schema-validated against `contract/events.schema.json` at runtime;
  no-content guarantee, SHA-256 dedup (spec 3.8, byte-identical to the client),
  partial-batch acceptance, rejected-events quarantine.
- **Pipeline workers** (`FOR UPDATE SKIP LOCKED`): identity resolution (BR-011
  shared/service/bot exclusion), work-item join, and the three daily aggregates.
- **Imports**: bulk NDJSON lane replaying the identical validators/dedup; signed
  Storage upload; per-line reject report.
- **Webhooks**: verification challenge, HMAC v1 signing, retry schedule, delivery log.
- **Engine**: priced cost, seat utilization/waste, containment (G6 corroboration),
  time-savings vs baseline; first-class `refused`/`awaiting_parameters`; grades;
  parameter version stamped for the period; change treatment; lineage; append-only
  figure versions; report issuance to Storage; alert monitors.
- **ops CLI** (`ops/opsctl.py`): the Console substitute for the §1.5 control plane.

## Repository layout
```
contract/                      # the API contract (single source of truth)
  ├── openapi.yaml             #   29 paths (authoritative HTTP surface)
  ├── events.schema.json       #   wire event contract (Draft 2020-12, runtime-loaded)
  └── error-codes.json         #   closed TF-<PLANE>-<NNN> registry (26 codes)
supabase/
  ├── migrations/              # 0001 certified schema VERBATIM · 0002 lockdown · 0003 buckets
  └── verification.sql         # the 7/7 certification battery
src/truefigure_server/            # SERVER — layered:
  ├── api/                     #   routes · request_models · response_models · application_services
  ├── domain/                  #   engine · entities · policies  (core intelligence)
  └── platform/                #   database · storage · billing · config · security
python/                        # SDK — the pure-passthrough Python client (100% tested)
ops/opsctl.py                  # Console-substitute control-plane CLI
tests/                         # server tests + tests/sql (attacks, lockdown)
  └── conformance/             #   50-UC matrix, golden E2E, error-registry, enum coverage
docs/                          # architecture, discrepancy reports, certification logs
ci.sh                          # the full gate
```

## Bring-up

### Primary — Supabase local stack
```bash
supabase start                 # local Postgres + Storage + PostgREST
supabase db reset              # applies migrations 0001-0003 in order (0001 self-seeds user id=1)
psql "$DATABASE_URL" -f supabase/verification.sql   # 7/7 PASS
```

### Fallback — plain PostgreSQL 16 (no Supabase CLI)
The schema is standard PostgreSQL; Storage is stubbed to the filesystem and the
`anon`/`authenticated` PostgREST roles are created so the lockdown gate stays real.
```bash
createdb truefigure
for m in supabase/migrations/*.sql; do psql -d truefigure -f "$m"; done
psql -d truefigure -f supabase/verification.sql
```

### Hosted Supabase
Migrations 0001-0003 are already applied to the hosted **TrueFigure** project.
Point the server at it with the **direct/session** connection string (DDL,
workers, and partition creation need it). Connection strings and the service key
live in env/secret config only — never in the repo, fixtures, or logs.

## Demo walkthrough (the golden playbook, by hand)
```bash
export DATABASE_URL=postgres://…              # direct/session connection

# 1. Provision the tenant + a finance key (secret printed once)
python ops/opsctl.py org create   --ref org_acme --legal-name "Acme Inc" --plan enterprise
python ops/opsctl.py workspace create --org-ref org_acme --ref ws_prod --name Prod
python ops/opsctl.py user create  --workspace-ref ws_prod --user-ref u_admin --role finance_params
python ops/opsctl.py key issue    --workspace-ref ws_prod --owner-user-ref u_admin --mode production --role finance_params
python ops/opsctl.py partitions ensure --months 3     # monthly events partitions

# 2. Run the API (uvicorn) and use the client / curl for:
#    POST /v1/deployments · PUT …/license · POST /v1/parameters · POST /v1/roster:batch
#    POST /v1/events:batch · POST /v1/imports (+ upload NDJSON)

# 3. Workers (scheduled in production; run once here)
python ops/opsctl.py pipeline run
python ops/opsctl.py engine run   --deployment-ref <dep> --period 2026-07
python ops/opsctl.py report issue --deployment-ref <dep> --period 2026-07
python ops/opsctl.py monitors run --workspace-ref ws_prod
python ops/opsctl.py webhooks deliver
```
`tests/conformance/test_golden_e2e.py` scripts this exact flow end-to-end and
asserts the final figures, grades, lineage references, and issued report
deterministically.

## The gate
```bash
./ci.sh                                  # fallback path (manages a local tf_ci database)
DATABASE_URL=postgres://… ./ci.sh        # against Supabase or any PG16
```
`ci.sh` fails on any red: db reset + migrations, battery 7/7, three attack tests,
PostgREST lockdown, zero-stub grep, ruff, mypy --strict, the 19 client tests,
the server suite with coverage ≥90%, and the 50/50 conformance matrix.

## Auth & tenancy
Authorization is TrueFigure API keys (`tf_live_`/`tf_test_`, SHA-256-hashed,
secret shown once). Tenancy and key issuance are ops/Console operations, never
HTTP routes (keys cannot mint keys). Read isolation is app-enforced workspace
scoping; the PostgREST surface is sealed (RLS deny-all + privilege revokes).

See `docs/sdk_discrepancies.md` for the (few) documented contract discrepancies
and their resolutions.
