# TrueFigure schema — Supabase deployment audit

**Question:** Was anything missed when applying `schema.sql` to Supabase?
**Answer:** No. The Supabase `public` schema is a complete, faithful reproduction of `schema.sql`.

## Method
Applied the authoritative `schema.sql` to a fresh local PostgreSQL 16 database
(the reference — `schema.sql` by definition), then ran an **identical**
deterministic structural fingerprint (md5 of ordered object metadata) on both
the reference and the Supabase project `xcqcuorjiarexbyvqbds` (Postgres 17).
Extension-owned functions were excluded (pgcrypto installs into `public` on
stock PG but into `extensions` on Supabase — an environment difference, not a
schema one).

## Result — 8 object classes compared

| section          | rows | reference hash                    | supabase hash                     | match |
|------------------|------|-----------------------------------|-----------------------------------|-------|
| columns          | 397  | a6b0558ad578c872c6c4a66847109014  | a281c9327084bb4230c2ceb33c16a14a  | see note |
| constraints      | 206  | bdb964ec37ffe4d87b7703de13afe0a4  | bdb964ec37ffe4d87b7703de13afe0a4  | IDENTICAL |
| indexes          | 99   | 18cbfb02ec44544b97dac9205f6ff23b  | 18cbfb02ec44544b97dac9205f6ff23b  | IDENTICAL |
| triggers         | 31   | ae54fa4ca4f66367c15358b1a44c215e  | ae54fa4ca4f66367c15358b1a44c215e  | IDENTICAL |
| enums            | 121  | 831bc24082c525bcb32fa385692f8e98  | 831bc24082c525bcb32fa385692f8e98  | IDENTICAL |
| table_comments   | 31   | ee3c5da0d44baeb3d8575d2a59e32d8e  | ee3c5da0d44baeb3d8575d2a59e32d8e  | IDENTICAL |
| column_comments  | 10   | e766970780f0a048c1964b91e5b183b7  | e766970780f0a048c1964b91e5b183b7  | IDENTICAL |
| functions        | 1    | 55e8f8f6ea3c1625a4531cadf87aff83  | 55e8f8f6ea3c1625a4531cadf87aff83  | IDENTICAL |

Row counts are equal in every section — nothing added, nothing missing.
- 31 tables, 35 enum types (121 values), 206 constraints, 99 indexes,
  31 `updated_dt` triggers, 31 table comments, 10 column comments,
  1 trigger function, seed system user id=1.

## The single `columns` hash difference (benign)
Per-table drill-down showed **30 base tables + the partitioned parent `events`
are byte-identical**; the only differing object is the default partition
`events_default`, at exactly one attribute:

- reference (PG16): `events_default.id  is_identity = NO`
- supabase  (PG17): `events_default.id  is_identity = YES`

This is a PostgreSQL 16→17 `information_schema` reporting change for a partition
child's **inherited** identity column. The identity is defined once on the
parent `events` (identical in both); behavior on insert is identical; and
`events_default` is excluded from the 377-column spec (it inherits). Not a
schema-fidelity difference.

## Conclusion
Every table, column, type, constraint, index, trigger, comment, function, and
the seed row present in `schema.sql` is present in Supabase with identical
definitions. Nothing was missed.
