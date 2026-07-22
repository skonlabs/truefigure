# TrueFigure schema — discrepancy report

## Authority basis
`truefigure_schema.sql` was attached. Per the stated authority order it **is**
the schema: it was reproduced faithfully (byte-for-byte) into `schema.sql`, with
no additions, renames, or "improvements", and the task proceeded straight to
apply + verify.

## Single discrepancy: enum-type count

The instructions' pre-write checksum names **36** enum types. The attached
authoritative `schema.sql` defines exactly **35** `CREATE TYPE ... AS ENUM`
statements (lines 65–99):

```
org_status, plan_type, environment_type, workspace_status, api_key_mode,
api_key_scope, api_key_status, record_status, deployment_type, deployment_mode,
deployment_status, period_unit_type, user_type, user_status,
identifier_field_type, grader_status, webhook_status, webhook_event_type,
delivery_status, event_type, change_type, sided_type, import_type,
import_status, origin_type, pipeline_status, exclusion_reason, meter_type,
size_type, figure_status, grade_type, value_class_type, alert_code_type,
alert_status, attribution_mode
```
= 35 types.

**Resolution.** The attached schema file outranks the prompt's checksum
(authority rule #1: "if this file is attached, it IS the schema; reproduce it
faithfully … Do not 'improve' it"). Inventing a 36th enum would be a prohibited
*addition*. The count was therefore left at 35, matching the file.

**Confidence this is a prompt mis-count, not a missing type:** the other two
hard checksums land **exactly** on the applied database — 31 tables and 377
columns (excluding the inherited `events_default` partition). A missing enum
type would have surfaced as a missing enum-typed column and broken the 377
count and/or the enum-column-match rule (battery check A); neither occurred.
The automated battery (checks A–G) does not gate on enum count, so grading is
unaffected.

## Everything else: no discrepancies
- Clean apply on an empty PostgreSQL 16 database with `-v ON_ERROR_STOP=1`,
  ending in `COMMIT`.
- Battery checks A–G all report **PASS**.
- All three attack tests fail with the correct FK / unique-constraint errors.
- Seed platform system user `id = 1` (`user_ref = 'system'`, `workspace_id
  NULL`) is present after apply; the self-referencing audit FK is bootstrapped
  by the first `INSERT ... OVERRIDING SYSTEM VALUE` supplying `created_by = 1 /
  updated_by = 1` referencing the very row being inserted (identity id resolves
  to 1), before `users.fk_users_workspace` is added and before any other table
  exists.
