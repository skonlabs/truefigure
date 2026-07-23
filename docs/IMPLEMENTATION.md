# TrueFigure SDK - Implementation Handoff

Certified artifact set for beginning SDK and database implementation.

## Inventory and versions
| Artifact | Version | Normative for |
|---|---|---|
| openapi/openapi.yaml | v2 (29 paths) | HTTP surface: paths, methods, request/response shapes |
| schemas/events.schema.json | Draft 2020-12 | Wire event contract; the no-content guarantee is structural (additionalProperties=false) |
| registry/error-codes.json | v1.2.0 | Error codes NP-<plane>-<nnn>; names keep registry (upper) form |
| db/schema.sql | v2.4 (31 tables, 36 enums; complete wire-name map in header) | Storage model; all invariants constraint-enforced |
| db/verification.sql | v2.4 | CI battery - every row must PASS after schema apply |
| python/ | reference client | Transport, batching, retry, idempotency, test mode; 19 tests |
| docs/semantics.md | changelog + normative notes | Dedup keys, derivations, snapshot-diff, amendments, role terminology |

The SDK Specification document (v2, 52pp) is the umbrella normative text; where prose
and machine artifacts could ever disagree, the machine artifacts in this package win
and the disagreement is a bug to file.

## Column-level accountability
TrueFigure_Data_Dictionary.docx documents every column of every table (377 columns):
type, nullability, wire correspondence, writer channel, purpose - generated from
the live database with coverage asserted by the generator (it fails on any
uncovered column). Regenerate after any schema change.

## Database bring-up
    createdb truefigure
    psql -d truefigure -f db/schema.sql        # ends with COMMIT
    psql -d truefigure -f db/verification.sql  # every row must PASS

Notes: monthly range partitions on events are provisioned by ops ahead of time
(events_default catches gaps); row id=1 in users is the seeded platform system user;
retention jobs (rejected_events ~90d, webhook_deliveries 7d refetch window) are
app-managed - the schema deliberately does not encode them.

## Client verification
    cd sdk && pip install -e . && python -m pytest tests/   # 19 passed

## Wire-contract verification (any language)
Validate emitter output against schemas/events.schema.json with a Draft 2020-12
validator. A payload carrying any undeclared field (e.g. "content") MUST fail -
that failure is the no-content guarantee working.

## Casing rule (normative)
All enum values are lowercase snake_case at every layer: wire = storage = client.
Error-code NAMES are the single exception (registry form). CI checks: battery
queries A and B above.

## Known per-engagement unknowns
Items tagged [A] in the Overlay Integration Specification (incumbent API schema,
identifier churn behavior, addressable work items, service-account inventory)
are engagement facts, not contract gaps - close them in the event-map workshop.

## What is NOT in this package
Server-side implementations (ingestion pipeline, identity resolution, measurement
engine, authorizer) - the contract defines their behavior; this package is what
they must conform to.
