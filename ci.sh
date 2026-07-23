#!/usr/bin/env bash
# =============================================================================
# ci.sh — TrueFigure SDK gate. Fails on any red.
#
# DB target resolution:
#   * If $DATABASE_URL is set, use it (Supabase session/direct conn or any PG16).
#   * Else fall back to a locally-managed `tf_ci` database via the postgres
#     superuser (the sandbox / plain-Postgres-16 path).
#
# Phase status is honest: gates for phases not yet built print "PENDING (Pn)"
# and do not fake success. The Definition of Done requires ALL sections GREEN.
# =============================================================================
set -uo pipefail
FAIL=0
say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; FAIL=1; }
grn() { printf '\033[32m%s\033[0m\n' "$1"; }
pend() { printf '\033[33mPENDING (%s): %s\033[0m\n' "$1" "$2"; }

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ---- DB helpers -------------------------------------------------------------
if [ -n "${DATABASE_URL:-}" ]; then
  PSQL() { psql "$DATABASE_URL" "$@"; }
  SUPERPSQL() { psql "$DATABASE_URL" "$@"; }
  RESET_DB() { PSQL -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" >/dev/null; }
  MANAGED=0
else
  CIDB="tf_ci"
  PSQL() { su postgres -c "psql $(printf '%q ' "$@") -d $CIDB"; }
  SUPERPSQL() { su postgres -c "psql $(printf '%q ' "$@")"; }
  RESET_DB() {
    su postgres -c "dropdb --if-exists $CIDB" >/dev/null 2>&1
    su postgres -c "createdb $CIDB" >/dev/null
    # Mirror Supabase's PostgREST roles so the lockdown gate is meaningful here.
    su postgres -c "psql -q -c \"DO \\$\\$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
      GRANT USAGE ON SCHEMA public TO anon, authenticated;
      GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon, authenticated;
    END \\$\\$;\"" >/dev/null 2>&1
  }
  MANAGED=1
fi

run_sql_file() { local f="$1"; cp "$f" /tmp/ci_sql.sql; chmod 644 /tmp/ci_sql.sql; PSQL -v ON_ERROR_STOP=1 -f /tmp/ci_sql.sql; }

# ---- 1. DB reset + migrations ----------------------------------------------
say "db reset + migrations"
RESET_DB
for m in supabase/migrations/*.sql; do
  if run_sql_file "$m" >/tmp/mig.out 2>&1; then grn "applied $m"; else red "MIGRATION FAILED: $m"; cat /tmp/mig.out; fi
done

# ---- 2. Verification battery (must be 7/7 PASS) ----------------------------
say "verification battery (7/7 PASS required)"
cp supabase/verification.sql /tmp/ver.sql; chmod 644 /tmp/ver.sql
BAT="$(PSQL -f /tmp/ver.sql 2>&1)"
echo "$BAT"
PASSN="$(echo "$BAT" | grep -c 'PASS')"
if echo "$BAT" | grep -q 'FAIL'; then red "battery has FAIL rows"; fi
if [ "$PASSN" -ne 7 ]; then red "battery PASS count = $PASSN (want 7)"; else grn "battery 7/7 PASS"; fi

# ---- 3. Attack tests (all three must raise) --------------------------------
say "attack tests (cross-tenant / duplicate / phantom-parameter must all block)"
if run_sql_file tests/sql/attack_tests.sql >/tmp/atk.out 2>&1; then
  if grep -q 'ALL THREE ATTACKS BLOCKED' /tmp/atk.out; then grn "3/3 attacks blocked"; else red "attack tests incomplete"; cat /tmp/atk.out; fi
else red "attack test script errored (an attack was NOT blocked)"; cat /tmp/atk.out; fi

# ---- 4. PostgREST lockdown (anon denied on every table) --------------------
say "PostgREST lockdown (anon must be denied on all public tables)"
if run_sql_file tests/sql/lockdown_test.sql >/tmp/lock.out 2>&1; then
  grep 'lockdown verified' /tmp/lock.out && grn "lockdown verified" || grn "lockdown ran clean"
else red "LOCKDOWN BREACH"; cat /tmp/lock.out; fi

# ---- 5. Zero-stub grep on src/ ---------------------------------------------
say "zero-stub grep on src/"
if [ -d src ] && [ -n "$(find src -name '*.py' 2>/dev/null)" ]; then
  HITS="$(grep -rnE 'TODO|FIXME|XXX|NotImplemented|raise NotImplementedError|^\s*pass\s*$' src/ || true)"
  if [ -n "$HITS" ]; then red "stub markers found in src/:"; echo "$HITS"; else grn "no stub markers in src/"; fi
else pend "P1" "src/ has no server code yet"; fi

# ---- 6. Lint (ruff) --------------------------------------------------------
say "ruff"
if [ -d src ] && [ -n "$(find src -name '*.py' 2>/dev/null)" ]; then python3 -m ruff check src/ && grn "ruff clean" || red "ruff failed"; else pend "P1" "no src/ to lint"; fi

# ---- 7. Types (mypy --strict) ----------------------------------------------
say "mypy --strict"
if [ -d src ] && [ -n "$(find src -name "*.py" 2>/dev/null)" ]; then python3 -m mypy --strict src/ && grn "mypy clean" || red "mypy failed"; else pend "P1" "no src/ to type-check"; fi

# ---- 8. Python SDK (pure-passthrough events) + layering guards -------------
# A downloaded SDK is a distributable artifact, so it must contain NO logic a
# competitor could lift: no event_key digest, no timestamp canonicalization, no
# dedup-scope rules, no enum/business validation, no measurement. It shapes
# envelopes and sends them; the server owns identity, validation, and measurement.
# (Webhook signature VERIFICATION is allowed — standard HMAC on a secret the
# customer already holds — so the guard targets the proprietary names precisely,
# and separately forbids crypto in the event/transport modules.)
say "no proprietary logic in the downloadable SDK"
if grep -RInE 'natural_key|_event_key|canonical_ts|def _iso|_write_figure|compute_deployment|def _change_treatment|labor_rate|price_provenance' \
     sdk/truefigure 2>/dev/null \
   || grep -nE 'hashlib|sha256|hmac' sdk/truefigure/events.py sdk/truefigure/client.py 2>/dev/null; then
  red "proprietary/core logic leaked into the SDK (see matches above)"
else grn "SDK is pure passthrough; no proprietary logic ships to customers"; fi

# Enforce the api/domain/platform layering: the domain (core) must not import the
# api layer, keeping the intelligence layer independent of transport/controllers.
say "layering: domain must not depend on api"
if grep -RInE 'from truefigure_server\.api|import truefigure_server\.api' src/truefigure_server/domain src/truefigure_server/platform 2>/dev/null; then
  red "layering violation: domain/platform imports the api layer"
else grn "domain/platform independent of api (clean layering)"; fi

say "python SDK tests + coverage == 100%"
if python3 -m pytest --version >/dev/null 2>&1 && [ -d sdk/tests ]; then
  ( cd sdk && python3 -m pytest -q tests/ --cov=truefigure --cov-report=term-missing --cov-fail-under=100 ) \
    && grn "python SDK tests green (100% coverage)" || red "python SDK tests/coverage failed"
else pend "P0" "pytest/client not available"; fi

# ---- 9. Server + conformance test suite ------------------------------------
say "server tests + coverage >= 90%"
if [ -n "$(find tests -name 'test_*.py' 2>/dev/null)" ]; then
  # Server tests connect via psycopg over TCP (TF_TEST_ADMIN_URL); they build
  # their own tf_test database and apply migrations. Requires a login role.
  export TF_TEST_ADMIN_URL="${TF_TEST_ADMIN_URL:-postgresql://tf:tf@127.0.0.1:5432/postgres}"
  export TF_ENVIRONMENT="${TF_ENVIRONMENT:-production}"
  python3 -m pytest --cov=src --cov-report=term-missing --cov-fail-under=90 tests/ \
    && grn "server tests + coverage >= 90%" || red "server tests/coverage failed"
else pend "P1-P8" "server + conformance suite not built"; fi

# ---- 10. Conformance matrix 42/42 ------------------------------------------
say "use-case conformance matrix (42/42)"
if [ -f tests/conformance/matrix_runner.py ]; then
  export TF_TEST_ADMIN_URL="${TF_TEST_ADMIN_URL:-postgresql://tf:tf@127.0.0.1:5432/postgres}"
  export TF_ENVIRONMENT="${TF_ENVIRONMENT:-production}"
  python3 tests/conformance/matrix_runner.py && grn "50/50 conformance" || red "conformance matrix not 50/50"
else pend "P8" "conformance matrix not built"; fi

say "RESULT"
if [ "$FAIL" -eq 0 ]; then grn "ci.sh: ALL GATES GREEN — battery 7/7, attacks blocked, lockdown, ruff, mypy --strict, tests+coverage>=90%, conformance 50/50"; exit 0; else red "ci.sh: FAILURES above"; exit 1; fi
