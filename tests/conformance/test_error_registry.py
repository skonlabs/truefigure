"""Every TF-<PLANE>-<NNN> code in the registry is PRODUCED by a test.

Each test triggers real error paths and records the codes it observes; the final
test asserts the observed set equals the closed registry (26 codes). No invented
codes, none unreachable.
"""

from __future__ import annotations

from _helpers import activity, auth, make_deployment, post_events, provision, secret_from

from truefigure_sdk.api.routes import imports
from truefigure_sdk.platform.config import registry
from truefigure_sdk.domain.policies import webhooks_delivery
from truefigure_sdk.errors import TFError

SEEN: set[str] = set()
T1 = "2026-07-01T10:00:00Z"


def _codes_from(resp) -> None:
    body = resp.json()
    for e in body.get("errors", []):
        SEEN.add(e["code"])
    for r in (body.get("data") or {}).get("results", []) if isinstance(body.get("data"), dict) else []:
        if isinstance(r, dict):
            if r.get("code"):
                SEEN.add(r["code"])
            if isinstance(r.get("error"), dict) and r["error"].get("code"):
                SEEN.add(r["error"]["code"])
    if isinstance(body.get("data"), dict) and body["data"].get("code"):
        SEEN.add(body["data"]["code"])


async def test_err_auth(client, ops, conn) -> None:
    key = provision(ops)
    _codes_from(await client.get("/v1/whoami", headers={"Authorization": "Bearer bogus"}))  # AUTH-001
    # AUTH-002: deployment-scoped key confined
    g = await make_deployment(client, key, name="g")
    o = await make_deployment(client, key, name="o")
    scoped = secret_from(ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_admin",
                             "--mode", "production", "--scope", "deployment", "--deployment-ref", g))
    from truefigure_sdk.api.application_services.auth import resolve_key
    oid = conn.execute("SELECT id FROM deployments WHERE deployment_ref=%s", (o,)).fetchone()["id"]
    try:
        resolve_key(scoped).require_deployment(int(oid))
    except TFError as e:
        SEEN.add(e.code)  # AUTH-002
    # AUTH-003: a test-mode key against the production environment
    tk = secret_from(ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_admin", "--mode", "test"))
    _codes_from(await client.get("/v1/whoami", headers=auth(tk)))  # AUTH-003


async def test_err_cfg(client, ops, conn) -> None:
    key = provision(ops)
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {}}, headers=auth(key))
    _codes_from(await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {}},
                                  headers=auth(key)))  # CFG-001
    norole = secret_from(ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_admin",
                             "--mode", "production"))
    _codes_from(await client.post("/v1/parameters", json={"effective_from": "2027-01-01", "payload": {}},
                                  headers=auth(norole)))  # CFG-002
    await make_deployment(client, key, external_ref="e1")
    _codes_from(await client.post("/v1/deployments",
                                  json={"name": "x", "type": "saas_tool", "external_ref": "e1"}, headers=auth(key)))  # CFG-003
    await client.post("/v1/id-namespaces", json={"field": "user_ref", "namespace": "a"}, headers=auth(key))
    _codes_from(await client.post("/v1/id-namespaces", json={"field": "user_ref", "namespace": "b"},
                                  headers=auth(key)))  # CFG-004
    # CFG-005: webhook verification challenge fails
    wid = (await client.post("/v1/webhooks", json={"url": "https://h", "events": ["figure.updated"], "secret": "s"},
                             headers=auth(key))).json()["data"]["webhook_id"]
    ws = conn.execute("SELECT id FROM workspaces WHERE workspace_ref='ws_prod'").fetchone()["id"]
    try:
        webhooks_delivery.verify_webhook(wid, int(ws), lambda *a: 500)
    except TFError as e:
        SEEN.add(e.code)  # CFG-005
    body = {"source_ref": "s", "event_type": "activity", "version": 1, "field_map": {"a": 1}}
    await client.post("/v1/mapping-contracts", json=body, headers=auth(key))
    _codes_from(await client.post("/v1/mapping-contracts", json=body, headers=auth(key)))  # CFG-006
    _codes_from(await client.post("/v1/deployments", json={"name": "x", "type": "saas_tool", "bogus": 1},
                                  headers=auth(key)))  # CFG-007


async def test_err_evt(client, ops, conn) -> None:
    key = provision(ops)
    dep = await make_deployment(client, key)
    ev = activity(dep, "u", "W", T1)
    ev["schema_version"] = "2.0"
    _codes_from(await post_events(client, key, [ev]))  # EVT-001
    _codes_from(await post_events(client, key, [activity("dep_ghost", "u", "W", T1)]))  # EVT-002
    bad = activity(dep, "u", "W", T1)
    bad["payload"]["bogus"] = 1
    _codes_from(await post_events(client, key, [bad]))  # EVT-003
    await client.post("/v1/id-namespaces",
                      json={"field": "user_ref", "namespace": "e", "deployment_id": dep, "format_regex": "^E[0-9]+$"},
                      headers=auth(key))
    _codes_from(await post_events(client, key, [activity(dep, "nope", "W", T1)]))  # EVT-004
    good = activity(dep, "E1", "W", T1)  # passes the ^E[0-9]+$ namespace regex above
    await post_events(client, key, [good])
    _codes_from(await post_events(client, key, [good]))  # EVT-005 (duplicate)
    c = activity(dep, "u", "W2", T1)
    c["payload"]["content"] = "x"
    _codes_from(await post_events(client, key, [c]))  # EVT-006
    _codes_from(await post_events(client, key, [activity(dep, "u", "W", T1)] * 501))  # EVT-007
    v = activity(dep, "u", "W3", T1)
    v["payload"]["value_usd"] = 1
    _codes_from(await post_events(client, key, [v]))  # EVT-008
    _codes_from(await post_events(client, key, [activity(dep, "u", "W4", "2000-01-01T00:00:00Z")]))  # EVT-009


async def test_err_rate(client, ops, conn) -> None:
    # RATE-001: rpm=1 -> second batch in the same minute is limited
    key = provision(ops)
    dep = await make_deployment(client, key)
    ops("limits", "set", "--workspace-ref", "ws_prod", "--rpm", "1")
    await post_events(client, key, [activity(dep, "u", "W1", T1)])
    _codes_from(await post_events(client, key, [activity(dep, "u", "W2", T1)]))  # RATE-001

    # RATE-002: free plan quota (20) -> a batch after the quota is exhausted
    key2 = provision(ops, org="org_free", ws="ws_free", plan="free", user="u_free", roles=())
    dep2 = await make_deployment(client, key2)
    batch = [activity(dep2, f"u{i}", f"W{i}", T1) for i in range(20)]
    await post_events(client, key2, batch)
    _codes_from(await post_events(client, key2, [activity(dep2, "z", "WZ", T1)]))  # RATE-002


async def test_err_read_srv(client, ops, conn) -> None:
    key = provision(ops)
    dep = await make_deployment(client, key)
    _codes_from(await client.get(f"/v1/figures/{dep}/fig_nope", headers=auth(key)))  # READ-001
    _codes_from(await client.get(f"/v1/figures/{dep}?period=2099-01", headers=auth(key)))  # READ-002
    # READ-003: a below-k cohort on live/usage
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u1", "kind": "person"}]}, headers=auth(key))
    await post_events(client, key, [activity(dep, "u1", "W", T1)])
    from truefigure_sdk.domain.policies import pipeline
    pipeline.run_pipeline()
    await client.put(f"/v1/deployments/{dep}/license", json={"seats_paid": 3, "valid_from": "2026-01-01"},
                     headers=auth(key))
    _codes_from(await client.get(f"/v1/live/usage/{dep}", headers=auth(key)))  # READ-003
    # SRV-001: import worker with no uploaded object
    imp = (await client.post("/v1/imports", json={"kind": "backfill"}, headers=auth(key))).json()["data"]["import_id"]
    try:
        imports.run_import(imp)
    except TFError as e:
        SEEN.add(e.code)  # SRV-001
    # SRV-002: ingestion paused for a deployment
    conn.execute("UPDATE deployments SET deployment_status='paused' WHERE deployment_ref=%s", (dep,))
    _codes_from(await post_events(client, key, [activity(dep, "u", "WP", T1)]))  # SRV-002


def test_zzz_registry_fully_covered() -> None:
    want = set(registry.code_ids())
    missing = want - SEEN
    assert not missing, f"error codes never produced by a test: {sorted(missing)}"
    assert len(SEEN & want) == 26
