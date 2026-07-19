"""Shared test fixtures. Tests run against a REAL Postgres database (never mocks).

A session-scoped fixture builds a fresh `tf_test` database, applies the three
migrations, and points the server at it. Provisioning goes through the ops CLI
(the Console substitute), exactly as production would.
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

ADMIN_URL = os.environ.get("TF_TEST_ADMIN_URL", "postgresql://tf:tf@127.0.0.1:5432/postgres")
TEST_DB = os.environ.get("TF_TEST_DB", "tf_test")
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB

MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))


def _run_sql_file(url: str, path: Path) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    # (Re)create the test database from the admin/maintenance connection.
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=%s AND pid<>pg_backend_pid()",
            (TEST_DB,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        # Cluster-global PostgREST roles so the lockdown migration has something to revoke.
        for role in ("anon", "authenticated"):
            exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if not exists:
                admin.execute(f"CREATE ROLE {role} NOLOGIN")

    for mig in MIGRATIONS:
        _run_sql_file(TEST_URL, mig)

    os.environ["DATABASE_URL"] = TEST_URL
    os.environ.setdefault("TF_ENVIRONMENT", "production")

    # Import settings/db AFTER env is set; clear any cached settings.
    from truefigure_sdk import config, db

    config.get_settings.cache_clear()
    db.close_pool()
    yield
    db.close_pool()


_SEED_USER = (
    "INSERT INTO users (workspace_id, user_type, user_ref, display_name, created_by, updated_by) "
    "OVERRIDING SYSTEM VALUE VALUES (NULL, 'service', 'system', 'Platform System', 1, 1)"
)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    """Truncate all data and re-seed the platform system user before each test."""
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        rows = c.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition"
        ).fetchall()
        names = ", ".join(f'public."{r[0]}"' for r in rows)
        c.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
        c.execute(_SEED_USER)
    # Reset in-process rate-limiter state so per-test counters don't leak.
    from truefigure_sdk import ratelimit

    ratelimit.reset()
    yield


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    from psycopg.rows import dict_row

    with psycopg.connect(TEST_URL, row_factory=dict_row, autocommit=True) as c:
        yield c


def run_ops(*argv: str) -> str:
    """Invoke opsctl.main and return its stdout (for parsing issued secrets)."""
    os.environ["DATABASE_URL"] = TEST_URL
    from ops import opsctl

    buf = io.StringIO()
    with redirect_stdout(buf):
        opsctl.main(list(argv))
    return buf.getvalue()


@pytest.fixture()
def ops() -> Any:
    return run_ops


@pytest.fixture()
def tenant() -> dict[str, str]:
    """Provision org_acme + prod workspace + admin (finance_params) via the ops CLI,
    and issue a production workspace-scoped key. Returns the key secret + refs."""
    import re

    run_ops("org", "create", "--ref", "org_acme", "--legal-name", "Acme Inc", "--plan", "enterprise")
    run_ops("workspace", "create", "--org-ref", "org_acme", "--ref", "ws_prod", "--name", "Prod",
            "--environment", "production")
    run_ops("user", "create", "--workspace-ref", "ws_prod", "--user-ref", "u_admin", "--role", "finance_params")
    out = run_ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_admin",
                  "--mode", "production", "--scope", "workspace", "--role", "finance_params")
    secret = re.search(r"SECRET \(shown once, store it now\): (\S+)", out).group(1)  # type: ignore[union-attr]
    return {"key": secret, "workspace": "ws_prod", "org": "org_acme"}


def auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture()
def sandbox() -> Iterator[dict[str, str]]:
    """A sandbox-environment tenant with a TEST-mode key (echo-vs-persist path)."""
    import re

    from truefigure_sdk import config

    os.environ["TF_ENVIRONMENT"] = "sandbox"
    config.get_settings.cache_clear()
    run_ops("org", "create", "--ref", "org_sbx", "--legal-name", "Sbx", "--plan", "team")
    run_ops("workspace", "create", "--org-ref", "org_sbx", "--ref", "ws_sbx", "--name", "Sbx",
            "--environment", "sandbox")
    run_ops("user", "create", "--workspace-ref", "ws_sbx", "--user-ref", "u_s")
    out = run_ops("key", "issue", "--workspace-ref", "ws_sbx", "--owner-user-ref", "u_s", "--mode", "test")
    secret = re.search(r"SECRET \(shown once, store it now\): (\S+)", out).group(1)  # type: ignore[union-attr]
    yield {"key": secret, "workspace": "ws_sbx"}
    os.environ["TF_ENVIRONMENT"] = "production"
    config.get_settings.cache_clear()


@pytest.fixture()
async def client() -> Any:
    """An httpx AsyncClient bound to the FastAPI app via ASGI transport."""
    import httpx

    from truefigure_sdk.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
