"""P2 — config-plane read endpoints and remaining branches (coverage + behavior)."""

from __future__ import annotations

import pytest

from conftest import auth
from truefigure_sdk import refs


def test_refs_encode_decode_roundtrip() -> None:
    wid = refs.encode_id("namespace", 42)
    assert wid == "ns_42"
    assert refs.decode_id("namespace", wid) == 42
    assert refs.decode_id("namespace", "wh_42") is None
    assert refs.decode_id("namespace", "ns_notanint") is None


async def _dep(client, key, **over) -> str:
    body = {"name": "D", "type": "saas_tool"}
    body.update(over)
    r = await client.post("/v1/deployments", json=body, headers=auth(key))
    return r.json()["data"]["deployment_id"]


async def test_deployment_list_and_get(client, tenant) -> None:
    dep = await _dep(client, tenant["key"], external_ref="e1")
    lst = await client.get("/v1/deployments", headers=auth(tenant["key"]))
    assert any(d["deployment_id"] == dep for d in lst.json()["data"]["results"])
    one = await client.get(f"/v1/deployments/{dep}", headers=auth(tenant["key"]))
    assert one.status_code == 200
    assert one.json()["data"]["external_ref"] == "e1"
    assert one.json()["meta"]["deployment_id"] == dep


async def test_get_unknown_deployment_evt002(client, tenant) -> None:
    r = await client.get("/v1/deployments/dep_nope", headers=auth(tenant["key"]))
    assert r.status_code == 404
    assert r.json()["errors"][0]["code"] == "TF-EVT-002"


async def test_deployment_patch_name(client, conn, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    r = await client.patch(f"/v1/deployments/{dep}", json={"name": "Renamed"}, headers=auth(tenant["key"]))
    assert r.status_code == 200
    nm = conn.execute("SELECT name FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()["name"]
    assert nm == "Renamed"


async def test_service_user_refs_link_and_reject(client, conn, tenant) -> None:
    # A roster service account exists -> link succeeds.
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "svc_a", "kind": "service"}]},
                      headers=auth(tenant["key"]))
    dep = await _dep(client, tenant["key"], service_user_refs=["svc_a"])
    n = conn.execute(
        "SELECT count(*) AS n FROM deployment_service_users dsu "
        "JOIN deployments d ON d.id=dsu.deployment_id WHERE d.deployment_ref=%s", (dep,)).fetchone()["n"]
    assert n == 1
    # A non-service (or missing) ref -> CFG-007.
    r = await client.post("/v1/deployments",
                          json={"name": "X", "type": "agent", "service_user_refs": ["not_a_service"]},
                          headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"


async def test_id_namespace_deployment_scoped_and_list(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    r = await client.post("/v1/id-namespaces",
                          json={"field": "work_item_id", "namespace": "crew_ids", "deployment_id": dep,
                                "source_ref": "crew_api", "format_regex": "^C[0-9]+$"},
                          headers=auth(tenant["key"]))
    assert r.status_code == 201
    lst = await client.get("/v1/id-namespaces", headers=auth(tenant["key"]))
    items = lst.json()["data"]["results"]
    assert items[0]["deployment_id"] == dep and items[0]["field"] == "work_item_id"


async def test_license_get_present_and_absent(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    absent = await client.get(f"/v1/deployments/{dep}/license", headers=auth(tenant["key"]))
    assert absent.status_code == 200 and absent.json()["data"] is None
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 10, "valid_from": "2026-01-01"}, headers=auth(tenant["key"]))
    got = await client.get(f"/v1/deployments/{dep}/license", headers=auth(tenant["key"]))
    assert got.json()["data"]["seats_paid"] == 10


async def test_parameters_list_and_get_and_404(client, tenant) -> None:
    await client.post("/v1/parameters", json={"effective_from": "2026-01-01", "payload": {"k": 1}},
                      headers=auth(tenant["key"]))
    lst = await client.get("/v1/parameters", headers=auth(tenant["key"]))
    assert lst.json()["data"]["results"][0]["version"] == 1
    got = await client.get("/v1/parameters/1", headers=auth(tenant["key"]))
    assert got.json()["data"]["payload"] == {"k": 1}
    missing = await client.get("/v1/parameters/99", headers=auth(tenant["key"]))
    assert missing.status_code == 404 and missing.json()["errors"][0]["code"] == "TF-READ-001"


async def test_qa_and_webhook_and_change_and_mapping_lists(client, tenant) -> None:
    key = tenant["key"]
    await client.post("/v1/qa-labels/schemas",
                      json={"label_schema_ref": "q", "name": "n", "label_values": ["a", "b"]}, headers=auth(key))
    assert (await client.get("/v1/qa-labels/schemas", headers=auth(key))).json()["data"]["results"][0]["label_schema_ref"] == "q"

    await client.post("/v1/webhooks", json={"url": "https://h", "events": ["figure.updated"], "secret": "s"},
                      headers=auth(key))
    whs = (await client.get("/v1/webhooks", headers=auth(key))).json()["data"]["results"]
    assert whs[0]["events"] == ["figure.updated"]

    await client.post("/v1/change-events",
                      json={"type": "staffing", "sidedness": "symmetric",
                            "occurred_at": "2026-02-01T00:00:00Z", "description": "d"}, headers=auth(key))
    ces = (await client.get("/v1/change-events", headers=auth(key))).json()["data"]["results"]
    assert ces[0]["type"] == "staffing" and ces[0]["superseded"] is False

    await client.post("/v1/mapping-contracts",
                      json={"source_ref": "s", "event_type": "activity", "version": 1, "field_map": {"a": 1}},
                      headers=auth(key))
    mcs = (await client.get("/v1/mapping-contracts", headers=auth(key))).json()["data"]["results"]
    assert mcs[0]["source_ref"] == "s" and mcs[0]["version"] == 1


async def test_change_event_supersedes_unknown(client, tenant) -> None:
    r = await client.post("/v1/change-events",
                          json={"type": "other", "sidedness": "symmetric", "occurred_at": "2026-01-01T00:00:00Z",
                                "description": "d", "supersedes": "ce_missing"}, headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"


async def test_change_event_scoped_to_deployment(client, conn, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    r = await client.post("/v1/change-events",
                          json={"type": "process_change", "sidedness": "one_sided",
                                "occurred_at": "2026-01-01T00:00:00Z", "description": "d",
                                "deployment_refs": [dep]}, headers=auth(tenant["key"]))
    assert r.status_code == 201
    n = conn.execute("SELECT count(*) AS n FROM change_log_deployments").fetchone()["n"]
    assert n == 1


async def test_webhook_unknown_event_rejected(client, tenant) -> None:
    r = await client.post("/v1/webhooks", json={"url": "https://h", "events": ["nope.topic"], "secret": "s"},
                          headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"


async def test_malformed_body_not_object(client, tenant) -> None:
    r = await client.post("/v1/deployments", json=[1, 2, 3], headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"


@pytest.mark.parametrize("missing", ["name", "type"])
async def test_deployment_required_fields(client, tenant, missing) -> None:
    body = {"name": "x", "type": "saas_tool"}
    del body[missing]
    r = await client.post("/v1/deployments", json=body, headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"
