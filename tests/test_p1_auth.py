"""P1 — authentication plane: whoami, key parsing/hashing, scope/role/mode.

Exercises TF-AUTH-001, TF-AUTH-002, TF-AUTH-003 and proves the ops CLI issues
keys with the secret shown once. Provisioning is via the ops CLI (Console
substitute) against the real database.
"""

from __future__ import annotations

import re

import pytest


def _issue_key(ops, *, mode: str = "production", scope: str = "workspace",
               roles: list[str] | None = None, deployments: list[str] | None = None,
               workspace: str = "ws_prod", owner: str = "u_admin") -> str:
    argv = ["key", "issue", "--workspace-ref", workspace, "--owner-user-ref", owner,
            "--mode", mode, "--scope", scope]
    for r in roles or []:
        argv += ["--role", r]
    for d in deployments or []:
        argv += ["--deployment-ref", d]
    out = ops(*argv)
    m = re.search(r"SECRET \(shown once, store it now\): (\S+)", out)
    assert m, f"no secret printed: {out}"
    return m.group(1)


@pytest.fixture
def acme(ops) -> dict[str, str]:
    """A provisioned tenant: org + prod workspace + admin member + a deployment."""
    ops("org", "create", "--ref", "org_acme", "--legal-name", "Acme Inc", "--plan", "enterprise")
    ops("workspace", "create", "--org-ref", "org_acme", "--ref", "ws_prod", "--name", "Prod",
        "--environment", "production")
    ops("user", "create", "--workspace-ref", "ws_prod", "--user-ref", "u_admin",
        "--role", "finance_params")
    return {"workspace": "ws_prod", "owner": "u_admin"}


def _make_deployment(conn, workspace_ref: str, deployment_ref: str) -> int:
    row = conn.execute("SELECT id FROM workspaces WHERE workspace_ref=%s", (workspace_ref,)).fetchone()
    dep = conn.execute(
        """INSERT INTO deployments (workspace_id, deployment_type, deployment_ref, name, created_by, updated_by)
           VALUES (%s,'saas_tool',%s,%s,1,1) RETURNING id""",
        (row["id"], deployment_ref, deployment_ref),
    ).fetchone()
    return int(dep["id"])


# ---- key issuance / hashing -------------------------------------------------
async def test_ops_issues_key_secret_shown_once(acme, ops, conn) -> None:
    secret = _issue_key(ops)
    assert secret.startswith("tf_live_") and len(secret) == len("tf_live_") + 32
    # Only the hash is stored, never the plaintext.
    from truefigure_server.api.application_services.auth import hash_key

    stored = conn.execute("SELECT secret_hash FROM api_keys ORDER BY id DESC LIMIT 1").fetchone()
    assert stored["secret_hash"] == hash_key(secret)
    got_plain = conn.execute(
        "SELECT count(*) AS n FROM api_keys WHERE secret_hash = %s", (secret,)
    ).fetchone()
    assert got_plain["n"] == 0  # the plaintext is never what's stored


# ---- whoami (happy path) ----------------------------------------------------
async def test_whoami_success(acme, ops, client) -> None:
    secret = _issue_key(ops, roles=["finance_params"])
    r = await client.get("/v1/whoami", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    data = body["data"]
    assert data["org_ref"] == "org_acme"
    assert data["plan_type"] == "enterprise"
    assert data["workspace_ref"] == "ws_prod"
    assert data["environment"] == "production"
    assert data["mode"] == "production"
    assert data["scope"] == "workspace"
    assert data["roles"] == ["finance_params"]
    assert data["rate_limit_tier"] == {"rpm": 600, "epm": 60000}
    assert body["meta"]["request_id"].startswith("req_")


# ---- TF-AUTH-001 ------------------------------------------------------------
async def test_auth001_missing_header(client) -> None:
    r = await client.get("/v1/whoami")
    assert r.status_code == 401
    assert r.json()["errors"][0]["code"] == "TF-AUTH-001"


async def test_auth001_bad_prefix(client) -> None:
    r = await client.get("/v1/whoami", headers={"Authorization": "Bearer garbage_abc"})
    assert r.status_code == 401
    assert r.json()["errors"][0]["code"] == "TF-AUTH-001"


async def test_auth001_revoked_key(acme, ops, client, conn) -> None:
    secret = _issue_key(ops)
    ref = conn.execute("SELECT api_key_ref FROM api_keys ORDER BY id DESC LIMIT 1").fetchone()["api_key_ref"]
    ops("key", "revoke", "--key-ref", ref)
    r = await client.get("/v1/whoami", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 401
    assert r.json()["errors"][0]["code"] == "TF-AUTH-001"


# ---- TF-AUTH-003 (mode vs environment) --------------------------------------
async def test_auth003_test_key_in_production(acme, ops, client) -> None:
    secret = _issue_key(ops, mode="test")  # server env defaults to production
    r = await client.get("/v1/whoami", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 403
    assert r.json()["errors"][0]["code"] == "TF-AUTH-003"


# ---- TF-AUTH-002 (deployment scope) -----------------------------------------
async def test_auth002_deployment_scope_enforced(acme, ops, client, conn) -> None:
    granted = _make_deployment(conn, "ws_prod", "dep_granted")
    other = _make_deployment(conn, "ws_prod", "dep_other")
    secret = _issue_key(ops, scope="deployment", deployments=["dep_granted"])

    from truefigure_server.api.application_services.auth import resolve_key
    from truefigure_server.errors import TFError

    principal = resolve_key(secret)
    assert principal.scope == "deployment"
    principal.require_deployment(granted)  # allowed, no raise
    with pytest.raises(TFError) as exc:
        principal.require_deployment(other)
    assert exc.value.code == "TF-AUTH-002"


async def test_require_role_rejects_missing(acme, ops) -> None:
    secret = _issue_key(ops)  # no roles
    from truefigure_server.api.application_services.auth import resolve_key
    from truefigure_server.errors import TFError

    principal = resolve_key(secret)
    with pytest.raises(TFError) as exc:
        principal.require_role("finance_params")
    assert exc.value.code == "TF-AUTH-002"
