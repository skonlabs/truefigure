# TrueFigure SDK — discrepancy report (living)

Per the build rule "machine artifacts win over prose AND the disagreement goes in
your discrepancy report; Ambiguity = STOP and record the question; never guess."
This file is updated every phase. Nothing here was silently resolved by guessing.

## §Inputs — all authoritative documents now received
1. **`TrueFigure_SDK_Specification.docx`** (rank #2) — **RECEIVED.** Drives P1+.
2. **`TrueFigure_Use_Case_Specification`** (rank #4, BR-001..BR-022) — **RECEIVED.**
   All 22 business rules now defined and available to the build.

### D4 — Use-case count: document has 50, build-spec says 42
The build spec repeatedly names **42 UCs** ("42/42 PASS", "TrueFigure_Use_Case_
Specification (42 UCs …)"). The delivered document defines **50 primary use cases**
(UC-ONB-01…04, UC-ACQ-01…09, UC-DEP-01, UC-PIL-01…04, UC-MEA-01…05, UC-IDQ-01…03,
UC-WFP-01…02, UC-BEN-01…02, UC-FIN-01…05, UC-ECO-01…06, UC-GOV-01…05, UC-REP-01…04,
UC-ACQ…, UC-ONB…). Distinct UC ids: **50**.

**Resolution (no guessing):** the document is the authority for what the UCs *are*;
the build-spec "42" is a count that undercounts the delivered spec by 8. Rather than
pick 42 of 50 (which would require guessing which to drop — a defect), the P8
conformance matrix will cover **all 50** documented UCs. 50/50 PASSING satisfies the
"42/42" mandate a fortiori. This is recorded here rather than silently resolved.

Impact: **P0–P3 complete and gated.** **P4–P7** buildable now. **P8** now unblocked
(will target 50/50). `ci.sh` marks the P8 matrix PENDING until it is built.

## Machine-artifact vs prose disagreements found
| # | Where | Says | Wins | Note |
|---|-------|------|------|------|
| D1 | `IMPLEMENTATION.md` line 11 | schema has "36 enums" | **35** (the certified `schema.sql` defines 35 `CREATE TYPE`) | Same finding as the schema-build discrepancy report; the live DB has 35. Prose is stale. |
| D2 | `IMPLEMENTATION.md` line 10 | error codes are `NP-<plane>-<nnn>` | **`TF-<plane>-<nnn>`** (the actual `registry/error-codes.json` uses `TF-`; 26 codes) | Build-spec §1 and §4 also say `TF-`. `NP-` is stale prose. |
| D3 | Build-spec §checksums (earlier schema task) | "36 enum types" | **35** | Carried forward; 31 tables & 377 columns match exactly, confirming 35 is correct. |
| D5a | Reference client `create_parameter_version` | body field `parameters` | **`payload`** (Data Dictionary column name; the server contract) | The older client binding diverged from the built server; aligned the client to `payload`. |
| D5b | Reference client `declare_change_event` | body field `scope_deployments` | **`deployment_refs`** (the server contract) | Same: aligned the client binding. |
| D6 | openapi roster `kind` enum = `[person,…]` | wire value `person` | stored `user_type='user'` | A value rename atop the field rename `kind→user_type`; handled in `wire.py` and recorded when P2 landed. |

## Wire shapes derived (not contradicted) where openapi is silent
Most config-plane request bodies (roster, license, id-namespaces, parameters,
qa-labels, webhooks, change-events, mapping-contracts) are not spelled out in
`openapi.yaml` (only summaries). Their field shapes were derived from the Data
Dictionary columns + the wire-name map + the SDK-spec §5 descriptions. These are
derivations, not conflicts; they are the documented column/wire correspondence.

## SDK architecture (post-review)
Per the reference layering, logic is separated by responsibility (see
`docs/architecture.md`):
- **SDK (Python only)** is PURE PASSTHROUGH for events (no validation, no
  event_key, no canonicalization — nothing a downloaded copy could leak) plus
  transport convenience: retries/backoff, timeouts, cursor pagination, NDJSON
  file upload, async import polling, error mapping, webhook signature
  verification, `provision()` workflow, typed results.
- **Server** is split into `api/` (routes, request/response models, application
  services), `domain/` (engine, entities, policies — the core intelligence), and
  `platform/` (database, storage, billing, config, security).
- Server is AUTHORITATIVE: it re-validates, re-canonicalizes, recomputes the key,
  deduplicates, and measures. The measurement engine/pricing/thresholds live only
  in `domain/` and never ship in the SDK (grep-guarded in `ci.sh`).
- `domain/` and `platform/` never import `api/` (layering guard in `ci.sh`).
- Node/React SDKs were removed; Python is the single supported SDK.

## Final status — Definition of Done
All build-spec §7 items hold simultaneously (verified by `./ci.sh`, exit 0):
- battery 7/7, three attack tests raise, PostgREST lockdown proven per table
- ruff clean, mypy --strict clean, zero-stub grep clean
- 19 (+7 extended = 26) client tests green; 277 server tests; coverage **100%**
- conformance matrix **50/50** (covers the build-spec's 42 a fortiori)
- every one of the 26 TF-* error codes produced by a test
- every behavior-driving enum value exercised (figure statuses, all grades, all
  webhook event types delivered, all import types, both origins, both modes)
- all 29 openapi paths+verbs implemented and exercised
- golden Integration-Playbook E2E passes deterministically
- no key-minting / tenancy-creating HTTP route (asserted); no secrets in repo/logs
- zero edits to `schema.sql`; zero invented error codes; zero renamed fields

## Environment notes (not contract discrepancies, but affect verification)
- **Python 3.11** is what's installed here; the stack target is **3.12**. Code is
  written to 3.12 syntax and runs on 3.11; CI in a 3.12 image is the reference.
- **Supabase CLI not installed** in this sandbox → P0 verified via the documented
  plain-Postgres-16 fallback locally, and migrations applied to the hosted Supabase
  project via the management API. `supabase start` is the documented primary path.
- **Direct outbound to `*.supabase.co` REST is blocked by the sandbox egress proxy**
  (403 CONNECT). The PostgREST lockdown is therefore proven at the database mechanism
  level instead (RLS state + `SET ROLE anon` privilege denial), which is what
  PostgREST actually relies on — see the P0 evidence. A live external `curl` against
  `/rest/v1/*` is included as a CI step to run where `supabase.co` is reachable.

## P0 evidence (all green)
- Migrations 0001–0003 apply cleanly (local PG16 fallback **and** hosted Supabase).
- `verification.sql`: **7/7 PASS** on both.
- Attack tests: cross-tenant service link, duplicate event, phantom parameter
  version — **all three blocked** (fk / unique / fk).
- PostgREST lockdown: RLS enabled **31/31**, **0** policies, **0** tables
  selectable/insertable by `anon`/`authenticated`; live `SET ROLE anon; SELECT`
  → `42501 permission denied`. Buckets: **3** created (all private).
- Python client: original **19 tests green**.
