"""P2 — config plane: every wire rename asserted in STORAGE, plus idempotency,
immutability, historization, and role gates.
"""

from __future__ import annotations

from conftest import auth


async def _deployment(client, key, **over) -> str:
    body = {"name": "Claims AI", "type": "saas_tool"}
    body.update(over)
    r = await client.post("/v1/deployments", json=body, headers=auth(key))
    assert r.status_code == 201, r.text
    return r.json()["data"]["deployment_id"]


# ---- deployments: type->deployment_type, mode->deployment_mode --------------
async def test_deployment_wire_renames_stored(client, conn, tenant) -> None:
    dep = await _deployment(client, tenant["key"], type="agent")
    row = conn.execute(
        "SELECT deployment_type, deployment_mode FROM deployments WHERE deployment_ref=%s", (dep,)
    ).fetchone()
    assert row["deployment_type"] == "agent"      # wire 'type' -> column deployment_type
    assert row["deployment_mode"] == "measured"   # wire 'mode' default -> deployment_mode


async def test_deployment_planned_mode_from_rollout(client, conn, tenant) -> None:
    dep = await _deployment(client, tenant["key"], planned_rollout_at="2026-09-01T00:00:00Z")
    row = conn.execute("SELECT deployment_mode FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()
    assert row["deployment_mode"] == "planned"


async def test_deployment_external_ref_idempotent(client, tenant) -> None:
    d1 = await _deployment(client, tenant["key"], external_ref="ext-1")
    r2 = await client.post("/v1/deployments",
                           json={"name": "Claims AI", "type": "saas_tool", "external_ref": "ext-1"},
                           headers=auth(tenant["key"]))
    assert r2.status_code == 200
    assert r2.json()["data"]["deployment_id"] == d1
    assert r2.json()["data"]["idempotent"] is True


async def test_deployment_type_immutable_with_events(client, conn, tenant) -> None:
    dep = await _deployment(client, tenant["key"])
    dep_id = conn.execute("SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()
    conn.execute(
        """INSERT INTO events (workspace_id, deployment_id, event_type, origin_type, event_key, schema_version,
               occurred_at, payload, created_by, updated_by)
           VALUES (%s,%s,'activity','customer_system',%s,'1','2026-01-01T00:00:00Z','{}',1,1)""",
        (dep_id["workspace_id"], dep_id["id"], "b" * 32))
    r = await client.patch(f"/v1/deployments/{dep}", json={"type": "agent"}, headers=auth(tenant["key"]))
    assert r.status_code == 409
    assert r.json()["errors"][0]["code"] == "TF-CFG-006"


async def test_unknown_field_rejected_cfg007(client, tenant) -> None:
    r = await client.post("/v1/deployments",
                          json={"name": "x", "type": "saas_tool", "bogus": 1}, headers=auth(tenant["key"]))
    assert r.status_code == 400
    assert r.json()["errors"][0]["code"] == "TF-CFG-007"
    assert r.json()["errors"][0]["field_path"] == "bogus"


# ---- roster: kind->user_type (person->user value), role->job_role -----------
async def test_roster_wire_renames_stored(client, conn, tenant) -> None:
    r = await client.post("/v1/roster:batch", json={"users": [
        {"user_ref": "u_1", "kind": "person", "role": "adjuster", "team": "claims",
         "identifiers": [{"namespace": "okta_uid", "value": "okta-1"}]},
        {"user_ref": "svc_1", "kind": "service"},
    ]}, headers=auth(tenant["key"]))
    assert r.status_code == 200
    assert [x["status"] for x in r.json()["data"]["results"]] == ["upserted", "upserted"]
    row = conn.execute("SELECT user_type, job_role, team FROM users WHERE user_ref='u_1'").fetchone()
    assert row["user_type"] == "user"     # wire kind 'person' -> column user_type 'user'
    assert row["job_role"] == "adjuster"  # wire 'role' -> column job_role
    svc = conn.execute("SELECT user_type FROM users WHERE user_ref='svc_1'").fetchone()
    assert svc["user_type"] == "service"
    ident = conn.execute("SELECT namespace, identifier_value FROM user_identifiers WHERE identifier_value='okta-1'").fetchone()
    assert ident["namespace"] == "okta_uid"


async def test_roster_identifier_conflict_cfg004(client, tenant) -> None:
    await client.post("/v1/roster:batch", json={"users": [
        {"user_ref": "u_1", "identifiers": [{"namespace": "okta_uid", "value": "shared"}]}]},
        headers=auth(tenant["key"]))
    r = await client.post("/v1/roster:batch", json={"users": [
        {"user_ref": "u_2", "identifiers": [{"namespace": "okta_uid", "value": "shared"}]}]},
        headers=auth(tenant["key"]))
    res = r.json()["data"]["results"][0]
    assert res["status"] == "rejected"
    assert res["error"]["code"] == "TF-CFG-004"


async def test_roster_upsert_updates_fields(client, conn, tenant) -> None:
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "role": "junior"}]},
                      headers=auth(tenant["key"]))
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "role": "senior"}]},
                      headers=auth(tenant["key"]))
    n = conn.execute("SELECT count(*) AS n FROM users WHERE user_ref='u_1'").fetchone()["n"]
    role = conn.execute("SELECT job_role FROM users WHERE user_ref='u_1'").fetchone()["job_role"]
    assert n == 1 and role == "senior"


async def test_roster_list_roundtrips_kind(client, tenant) -> None:
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "kind": "person"}]},
                      headers=auth(tenant["key"]))
    r = await client.get("/v1/roster?kind=person", headers=auth(tenant["key"]))
    users = r.json()["data"]["results"]
    assert users and users[0]["kind"] == "person"  # column user_type 'user' -> wire 'person'


# ---- license: period_unit->period_unit_type, historized ---------------------
async def test_license_wire_rename_and_historization(client, conn, tenant) -> None:
    dep = await _deployment(client, tenant["key"])
    r1 = await client.put(f"/v1/deployments/{dep}/license",
                          json={"seats_paid": 100, "price_per_seat": 20.0, "period_unit": "month",
                                "valid_from": "2026-01-01", "licensed_user_refs": ["okta-1"]},
                          headers=auth(tenant["key"]))
    assert r1.status_code == 200
    row = conn.execute(
        "SELECT period_unit_type, seats_paid FROM deployment_licenses WHERE valid_to IS NULL").fetchone()
    assert row["period_unit_type"] == "month"  # wire 'period_unit' -> column period_unit_type
    assert row["seats_paid"] == 100
    # New declaration closes the old row -> exactly one current row.
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 150, "valid_from": "2026-06-01"}, headers=auth(tenant["key"]))
    n_current = conn.execute("SELECT count(*) AS n FROM deployment_licenses WHERE valid_to IS NULL").fetchone()["n"]
    n_total = conn.execute("SELECT count(*) AS n FROM deployment_licenses").fetchone()["n"]
    assert n_current == 1 and n_total == 2


# ---- id-namespaces: field->identifier_field_type ----------------------------
async def test_id_namespace_wire_rename(client, conn, tenant) -> None:
    r = await client.post("/v1/id-namespaces",
                          json={"field": "user_ref", "namespace": "okta_uid"}, headers=auth(tenant["key"]))
    assert r.status_code == 201
    row = conn.execute("SELECT identifier_field_type, namespace FROM identifier_namespaces").fetchone()
    assert row["identifier_field_type"] == "user_ref"  # wire 'field' -> column identifier_field_type
    # Second active declaration for the same (scope, field, source) conflicts.
    r2 = await client.post("/v1/id-namespaces",
                           json={"field": "user_ref", "namespace": "other"}, headers=auth(tenant["key"]))
    assert r2.status_code == 409
    assert r2.json()["errors"][0]["code"] == "TF-CFG-004"


# ---- parameters: immutable versions, role gate, overlap ---------------------
async def test_parameters_role_and_overlap(client, conn, tenant) -> None:
    r = await client.post("/v1/parameters",
                          json={"effective_from": "2026-01-01", "payload": {"labor_rates": {"adjuster": 50}}},
                          headers=auth(tenant["key"]))
    assert r.status_code == 201
    assert r.json()["data"]["version"] == 1
    row = conn.execute("SELECT version, payload FROM parameter_sets WHERE version=1").fetchone()
    assert row["payload"]["labor_rates"]["adjuster"] == 50
    # Same effective_from overlaps -> TF-CFG-001.
    r2 = await client.post("/v1/parameters", json={"effective_from": "2026-01-01", "payload": {}},
                           headers=auth(tenant["key"]))
    assert r2.status_code == 409 and r2.json()["errors"][0]["code"] == "TF-CFG-001"
    # Next version increments.
    r3 = await client.post("/v1/parameters", json={"effective_from": "2026-06-01", "payload": {}},
                           headers=auth(tenant["key"]))
    assert r3.json()["data"]["version"] == 2


async def test_parameters_requires_finance_role(client, ops, tenant) -> None:
    # A key WITHOUT finance_params.
    import re
    ops("user", "create", "--workspace-ref", "ws_prod", "--user-ref", "u_plain")
    out = ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_plain", "--mode", "production")
    plain = re.search(r"SECRET \(shown once, store it now\): (\S+)", out).group(1)
    r = await client.post("/v1/parameters", json={"effective_from": "2026-01-01", "payload": {}},
                          headers=auth(plain))
    assert r.status_code == 403 and r.json()["errors"][0]["code"] == "TF-CFG-002"


# ---- qa-labels --------------------------------------------------------------
async def test_qa_label_schema(client, conn, tenant) -> None:
    r = await client.post("/v1/qa-labels/schemas",
                          json={"label_schema_ref": "qa_v1", "name": "Quality", "label_values": ["pass", "fail"]},
                          headers=auth(tenant["key"]))
    assert r.status_code == 201
    row = conn.execute("SELECT label_values, grader_status FROM qa_label_definitions WHERE label_schema_ref='qa_v1'").fetchone()
    assert row["label_values"] == ["pass", "fail"] and row["grader_status"] == "uncalibrated"


# ---- webhooks: events->webhook_event_type -----------------------------------
async def test_webhook_wire_rename_and_idempotency(client, conn, tenant) -> None:
    r = await client.post("/v1/webhooks",
                          json={"url": "https://x.example/hook", "events": ["figure.updated", "alert.raised"],
                                "secret": "s3cr3t"}, headers=auth(tenant["key"]))
    assert r.status_code == 201
    row = conn.execute("SELECT webhook_event_type::text[] AS webhook_event_type, webhook_status, secret_hash FROM webhooks").fetchone()
    assert set(row["webhook_event_type"]) == {"figure.updated", "alert.raised"}  # wire 'events' -> webhook_event_type
    assert row["webhook_status"] == "unverified"
    assert row["secret_hash"] != "s3cr3t"  # secret hashed, not stored raw
    # Idempotent by (url, events).
    r2 = await client.post("/v1/webhooks",
                           json={"url": "https://x.example/hook", "events": ["figure.updated", "alert.raised"],
                                 "secret": "s3cr3t"}, headers=auth(tenant["key"]))
    assert r2.status_code == 200 and r2.json()["data"]["idempotent"] is True


async def test_webhook_delete(client, conn, tenant) -> None:
    r = await client.post("/v1/webhooks", json={"url": "https://y/hook", "events": ["report.issued"], "secret": "s"},
                          headers=auth(tenant["key"]))
    wid = r.json()["data"]["webhook_id"]
    d = await client.delete(f"/v1/webhooks/{wid}", headers=auth(tenant["key"]))
    assert d.status_code == 200
    st = conn.execute("SELECT webhook_status FROM webhooks WHERE webhook_ref=%s", (wid,)).fetchone()
    assert st["webhook_status"] == "deleted"


# ---- change-events: type->change_type, sidedness->sided_type, supersedes -----
async def test_change_event_wire_renames(client, conn, tenant) -> None:
    r = await client.post("/v1/change-events",
                          json={"type": "tool_change", "sidedness": "one_sided",
                                "occurred_at": "2026-03-01T00:00:00Z", "description": "incumbent upgrade"},
                          headers=auth(tenant["key"]))
    assert r.status_code == 201
    row = conn.execute("SELECT change_type, sided_type FROM change_log").fetchone()
    assert row["change_type"] == "tool_change" and row["sided_type"] == "one_sided"


async def test_change_event_supersedes_sets_old_row(client, conn, tenant) -> None:
    r1 = await client.post("/v1/change-events",
                           json={"type": "seasonal", "sidedness": "symmetric",
                                 "occurred_at": "2026-01-01T00:00:00Z", "description": "orig"},
                           headers=auth(tenant["key"]))
    old_ref = r1.json()["data"]["change_event_ref"]
    r2 = await client.post("/v1/change-events",
                           json={"type": "seasonal", "sidedness": "symmetric",
                                 "occurred_at": "2026-01-01T00:00:00Z", "description": "corrected",
                                 "supersedes": old_ref}, headers=auth(tenant["key"]))
    new_ref = r2.json()["data"]["change_event_ref"]
    old = conn.execute("SELECT superseded_by_id FROM change_log WHERE change_event_ref=%s", (old_ref,)).fetchone()
    new = conn.execute("SELECT id, superseded_by_id FROM change_log WHERE change_event_ref=%s", (new_ref,)).fetchone()
    # superseded_by_id is set ON THE OLD ROW, pointing at the new correction.
    assert old["superseded_by_id"] == new["id"]
    assert new["superseded_by_id"] is None


# ---- mapping-contracts: immutable per (workspace, source_ref, version) -------
async def test_mapping_contract_immutable(client, conn, tenant) -> None:
    body = {"source_ref": "crew_api", "event_type": "lifecycle", "version": 1,
            "field_map": {"work_item_id": {"concat": ["a", "b"], "sep": "-"}}}
    r = await client.post("/v1/mapping-contracts", json=body, headers=auth(tenant["key"]))
    assert r.status_code == 201
    row = conn.execute("SELECT event_type, field_map FROM mapping_contracts WHERE source_ref='crew_api'").fetchone()
    assert row["event_type"] == "lifecycle"
    assert row["field_map"]["work_item_id"]["sep"] == "-"
    r2 = await client.post("/v1/mapping-contracts", json=body, headers=auth(tenant["key"]))
    assert r2.status_code == 409 and r2.json()["errors"][0]["code"] == "TF-CFG-006"
