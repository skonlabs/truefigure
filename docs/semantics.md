# Measurement semantics (read this before integrating)

## Why there is no `value_usd` field
The platform's economics depend on one property: **numbers cannot be bought**.
Fees never depend on findings (BR-002), and the integrating party — customer or
vendor — never asserts value; it emits facts the engine measures. If your
integration "knows" the savings, the place for that knowledge is a conversation
with your finance team about parameters, not an event field.

## Grades: what the numbers are
- **estimate** — directional, assumptions listed. What projections and thin data honestly support.
- **measured** — computed from your records with disclosed methodology, confidence stated.
- **verified** — six machine-checked conditions (lineage, repeatability, governed method,
  deterministic core, immutable issuance, independent issuer). Only issued under the
  Enterprise plan's governed process.
Every figure carries its grade + method version + parameter version. Grades are
per-figure, not per-report.

## /live vs /figures: two freshness semantics, on purpose
- `/live/*` — direct reads and deterministic arithmetic (seats, usage spread, meter
  accumulation). Seconds-fresh. Safe because no statistics are involved.
- `/figures/*` — effect estimates and dollar verdicts. Served as the **latest finalized
  computation** with `computed_at` / `next_compute_at`. Never recomputed per call:
  numbers that jitter with every request are the dashboard disease this platform
  replaces. Subscribe to `figure.updated` webhooks instead of polling.

## refused is not an error
When the margin of error exceeds the effect, the API says so and forecasts when a
verdict is expected (BR-005). HTTP 200. Design your consuming code for three figure
states: `computed`, `refused`, `awaiting_parameters`.

## Identity is pseudonymous and resolved server-side
`user_ref` is your opaque key. Roster resolution, shared-account exclusion, and
per-person first-use cutoffs happen in the platform (with exclusions disclosed on
reports). No report artifact can identify an individual (k-floor on every render path).

## Why lifecycle events matter even though you asked for cost/quality/revenue
Time/labor effects are usually the **dominant** value line, and they are computable
only from lifecycle transitions. If your product sees the work item, emit its
lifecycle. It also future-proofs the integration where the customer's work system
can't be connected directly.

## Ordering, dedup scopes, backfill, corrections (v2 audit additions)
- Events may arrive in any order; the server orders by payload timestamp. Never buffer to preserve order.
- Two concepts: event_key (32-hex lookup handle, deployment-scoped) vs dedup scope.
  activity + cost_meter dedup within the deployment; lifecycle/quality/revenue dedup across
  the WORKSPACE — a work item is a fact about your workflow, not about any one AI. When two
  deployments share a queue, send each work-item fact once, under either deployment_id.
  Different origins (customer_system vs vendor_product) are never deduped against each other:
  they are the reconciliation inputs.
- Historical backfill: same endpoint, up to the backfill horizon (default 400 days). Batch 500,
  pace under rate limits, don't sort — idempotency makes restarts free.
- The store is append-only: no edit/delete API. Systematic bad data => fix the emitter, request
  a source reprocess (console/support in v1); issued figures correct by re-versioning only.

## Namespace resolution model (v2)
At most one active id-namespace declaration per (scope, field); deployment scope overrides the
workspace default. Undeclared fields match raw against roster/work-system identifiers (valid and
common). A declared format_regex makes bad values fail fast at ingestion (TF-EVT-004) instead of
becoming silent unresolved identities.

## Webhook delivery contract (v2)
Headers: X-TrueFigure-Timestamp (unix seconds), X-TrueFigure-Signature: v1=hex(hmac_sha256(secret,
timestamp + "." + raw_body)). Verify over the RAW body; reject if |now - timestamp| > 300s.
Respond 2xx within 10s. Retries: 1m, 5m, 30m, 2h, 6h, 24h; dedupe by delivery_id; 7 days of
total failure => suspended (re-verify to resume).

## Money and periods (v2)
ParameterSet carries currency (ISO-4217, default USD); all amounts in a set share it. Figure/cost
period grammar: YYYY-Qn or YYYY-MM. Claim types are an open additive set (see spec §6.1);
tolerate unknown values.

## Buildability fixes (v2.1 audit round 2)
- Workspace defined: tenancy root bound to the API key, never on the wire; all named
  scopes (deployments, users, parameters, schemas, work items) unique within it.
- event_key redefined: SHA-256 of the family DEDUP key. lifecycle/quality/revenue keys
  exclude deployment_id (workspace-scoped identity); duplicate returns the canonical key;
  /status resolves it regardless of which deployment carried the event. Client key
  computation updated to match (breaking change to _event_key values; pre-1.0).
- Roster API added (POST /v1/roster:batch): SDK-only integrations can now supply the
  identity-resolution and labor-rate-resolution target. Without it, person-level
  measurement cannot start.
- License API added (PUT /v1/deployments/{id}/license): the seat ledger /live/usage
  classifies and prices against. seats_active says used; this says paid-for.
- Test-mode echo fields defined per family (cost_meter has no person/work-item echo;
  non-activity families echo interpreted_as instead of action_class).

## Build-completeness fixes (v2.2 audit round 3)
- Scope boundary stated: the SDK (contract, ingestion, config, read serving, clients) is
  fully buildable with zero measurement logic; the engine interfaces by writing Figure
  records the read plane serves. /figures with TF-READ-002 is a legitimate pre-engine state.
- event_key canonicalization is now normative (spec 3.8): pipe-joined fields in table
  order, absent optionals = empty string, timestamps canonicalized to UTC RFC3339
  millisecond 'Z' form BEFORE digesting, sha256 -> first 32 lowercase hex. The Python
  client implements it; equal instants in different offset forms digest identically.
- Roster: kind person|shared|service|bot declares BR-011 exclusions in SDK-only mode;
  GET /v1/roster added; unresolved-event policy defined (re-resolvable forever; excluded
  per-computation only).
- Config retry idempotency defined: deployments via external_ref (TF-CFG-003 = already
  applied), parameters via TF-CFG-001-on-same-effective_from, webhooks dedupe by
  (url, events).
- Seat-classification default thresholds and drift-warning codes are now enumerated with
  default triggers (versioned, [A], tunable).
- Section 8: client library conformance requirements C1-C14 - the normative checklist
  for building the SDK in any language; the Python tests are its executable form.

## Completeness round (v2.3): eight new API surfaces
- /v1/imports: bulk NDJSON transport for the SAME envelope contract (backfill,
  publication bursts, migrations) - separate ingestion lane, per-line reject report.
- /v1/change-events: declare cutovers/seasonal/process/staffing changes; treatments
  stamp onto figures (the change log finally has a write API).
- /v1/mapping-contracts: versioned source-format contracts per source_ref; format
  drift becomes a visible measurement event.
- /v1/whoami: key self-check (workspace, mode, scope, roles, limits) - translator startup.
- /v1/reports: list/fetch issued report versions with grade profiles and artifacts.
- /v1/alerts (+:ack): durable stateful twin of alert.raised.
- /v1/figures/.../lineage: the endpoint behind lineage_url - evidence chain on demand.
- /v1/webhooks/{id}/deliveries: notification observability.
Plus: X-TrueFigure-Source header for per-source health attribution (multi-feed
integrations), bulk/burst normative rule (bursty -> imports, never batch loops),
and the ~800-day backfill horizon guidance for long-seasonality verticals.

## Wire rename (v2.4): roster resource persons -> users
POST /v1/roster:batch now takes users[] with user_ref (was persons[]/person_ref).
Breaking pre-1.0 change; removes the last wire<->database naming seam for the
roster identity model. Event payload field user_ref was always user_ref.

## Overlay-vendor topology round (measured AI on top of an unmeasured incumbent)
Driven by the overlay-topology analysis (a measured AI vendor overlays an
incumbent system of record that is not a party to the engagement):
- Mapping contracts now support DERIVED keys: field_map values may be derivation
  objects ({concat, sep} / {const} / {datetime}) for sources without stable
  single-column identifiers (incumbent internal ids churn on re-optimization).
  Derivations must be deterministic - dedup keys are computed from them.
- Snapshot-diff translator pattern is normative: polling sources (schedule/crew
  APIs) translate by diffing consecutive snapshots against a local state store;
  retroactive source edits emit NEW amendment events, never mutations.
- Topology rule (process): the customer-origin feed from the system of record
  is the record of truth; the overlay vendor's stream is claims to reconcile.
  The overlay vendor ADOPTS the incumbent's identifiers (or the declared
  derivation) - it never mints its own work-item ids.

## Role terminology (normative)
'vendor' / origin=vendor_product = the MEASURED AI PROVIDER sponsoring the
engagement. A customer's incumbent software supplier is 'incumbent vendor':
not a TrueFigure party, witnessed only via customer-origin feeds under the
customer's license rights, and never the subject of a verdict. Multi-vendor
documents must say measured/incumbent explicitly, never bare 'vendor'.

## G6: deployment service-account linkage
Deployments accept service_user_refs[]: roster service accounts the measured
vendor's product operates inside incumbent systems. Customer-origin events
acted by those accounts are attributable to the deployment - independent
corroboration of containment/autonomous-action claims. DB: deployment_service_users.

## Provisioning split (normative)
Organizations, workspaces, member users, API keys, and key grants are Console/
onboarding operations, never API operations: keys must not mint keys, and
plan/limits are contract artifacts. Spec 1.5 carries the full population map
for all 31 tables. Org-admin automation API = recorded post-1.0 candidate.
