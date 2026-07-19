# TrueFigure SDK — discrepancy report (living)

Per the build rule "machine artifacts win over prose AND the disagreement goes in
your discrepancy report; Ambiguity = STOP and record the question; never guess."
This file is updated every phase. Nothing here was silently resolved by guessing.

## §Blocking — two authoritative inputs are absent from the workspace
Neither is present in `truefigure-sdk-package.zip` nor the uploads folder (searched):

1. **`TrueFigure_SDK_Specification.docx`** (authority rank #2). Supplies endpoint
   semantics, the **§1.5 population map** (which defines the exact ops-CLI scope in
   build-spec §5), grade rules, refusal honesty, and the no-content guarantee prose.
2. **`TrueFigure_Use_Case_Specification`** (42 UCs + **BR-001..BR-022**), authority
   rank #4 and the entire target of the §0 "100% mandate" (42/42 conformance). The
   business rules referenced throughout build-spec §4 (BR-002, BR-005, BR-011,
   BR-016, BR-022, …) are defined only here.

Impact: **P0 is fully buildable without them** (done — see below). **P1–P7** are
partially specified by the machine artifacts + `docs/semantics.md` + `IMPLEMENTATION.md`
+ the Data Dictionary, but the §1.5 provisioning split and BR semantics need the SDK
Spec. **P8 is impossible** without the Use-Case Specification — the 42 UCs cannot be
invented (doing so is defined as a defect). Requested by name; build paused at the
P0/P1 boundary pending delivery.

## Machine-artifact vs prose disagreements found so far
| # | Where | Says | Wins | Note |
|---|-------|------|------|------|
| D1 | `IMPLEMENTATION.md` line 11 | schema has "36 enums" | **35** (the certified `schema.sql` defines 35 `CREATE TYPE`) | Same finding as the schema-build discrepancy report; the live DB has 35. Prose is stale. |
| D2 | `IMPLEMENTATION.md` line 10 | error codes are `NP-<plane>-<nnn>` | **`TF-<plane>-<nnn>`** (the actual `registry/error-codes.json` uses `TF-`; 26 codes) | Build-spec §1 and §4 also say `TF-`. `NP-` is stale prose. |
| D3 | Build-spec §checksums (earlier schema task) | "36 enum types" | **35** | Carried forward; 31 tables & 377 columns match exactly, confirming 35 is correct. |

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
