"""P3 — ingestion: validate -> dedup -> persist/reject, status, no-content,
value-assertion, regex fail-fast, batch limits, test-mode echo, partial batch.
"""

from __future__ import annotations

from conftest import auth

TS = "2026-07-01T00:00:00Z"


async def _dep(client, key, **over) -> str:
    body = {"name": "D", "type": "saas_tool"}
    body.update(over)
    r = await client.post("/v1/deployments", json=body, headers=auth(key))
    return r.json()["data"]["deployment_id"]


def _activity(dep, **over):
    p = {"user_ref": "u_1", "timestamp": TS, "work_item_id": "W1", "action_type": "suggestion_accepted"}
    p.update(over.pop("payload", {}))
    ev = {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
          "payload": p}
    ev.update(over)
    return ev


async def test_accept_and_persist_activity(client, conn, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    r = await client.post("/v1/events:batch", json={"events": [_activity(dep)]}, headers=auth(tenant["key"]))
    assert r.status_code == 200
    res = r.json()["data"]["results"][0]
    assert res["status"] == "accepted"
    row = conn.execute(
        "SELECT origin_type, event_type, pipeline_status, occurred_at FROM events WHERE event_key=%s",
        (res["event_key"],)).fetchone()
    assert row["origin_type"] == "customer_system"  # wire 'origin' -> column origin_type
    assert row["pipeline_status"] == "accepted"


async def test_server_assigns_stable_key_and_dedups(client, tenant) -> None:
    # The client sends a raw envelope (no client-side key). The server assigns the
    # event_key; re-sending the identical envelope is a deduplicated no-op with the
    # SAME server key. Idempotency lives entirely server-side.
    dep = await _dep(client, tenant["key"])
    r1 = await client.post("/v1/events:batch", json={"events": [_activity(dep)]}, headers=auth(tenant["key"]))
    first = r1.json()["data"]["results"][0]
    assert first["status"] == "accepted" and len(first["event_key"]) == 32
    r2 = await client.post("/v1/events:batch", json={"events": [_activity(dep)]}, headers=auth(tenant["key"]))
    second = r2.json()["data"]["results"][0]
    assert second["status"] == "duplicate"
    assert second["event_key"] == first["event_key"]


async def test_no_content_guarantee_evt006(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"content": "the prompt text"})
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    res = r.json()["data"]["results"][0]
    assert res["status"] == "rejected"
    assert res["error"]["code"] == "TF-EVT-006"
    assert res["error"]["field_path"] == "payload.content"


async def test_value_assertion_rejected_evt008(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"value_usd": 1000})
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-008"


async def test_unknown_field_evt003(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"bogus": 1})
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-003"


async def test_schema_version_unsupported_evt001(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep)
    ev["schema_version"] = "2.0"
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-001"


async def test_duplicate_is_noop_evt005(client, conn, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep)
    r1 = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r1.json()["data"]["results"][0]["status"] == "accepted"
    r2 = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r2.status_code == 200
    assert r2.json()["data"]["results"][0]["status"] == "duplicate"
    n = conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"]
    assert n == 1


async def test_lifecycle_workspace_scoped_dedup(client, conn, tenant) -> None:
    d1 = await _dep(client, tenant["key"])
    d2 = await _dep(client, tenant["key"])
    life = lambda dep: {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle",  # noqa: E731
                        "origin": "customer_system",
                        "payload": {"work_item_id": "W9", "event": "created", "timestamp": TS}}
    a = await client.post("/v1/events:batch", json={"events": [life(d1)]}, headers=auth(tenant["key"]))
    b = await client.post("/v1/events:batch", json={"events": [life(d2)]}, headers=auth(tenant["key"]))
    # Same work-item fact under a different deployment -> same key -> duplicate.
    assert a.json()["data"]["results"][0]["status"] == "accepted"
    assert b.json()["data"]["results"][0]["status"] == "duplicate"


async def test_unknown_deployment_evt002_quarantined(client, conn, tenant) -> None:
    ev = _activity("dep_ghost")
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-002"
    # Quarantined with deployment_ref stored as-sent (no FK).
    row = conn.execute("SELECT deployment_ref, error_code FROM rejected_events").fetchone()
    assert row["deployment_ref"] == "dep_ghost" and row["error_code"] == "TF-EVT-002"


async def test_naive_timestamp_evt003(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"timestamp": "2026-07-01T00:00:00"})  # no tz
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-003"


async def test_timestamp_out_of_horizon_evt009(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"timestamp": "2000-01-01T00:00:00Z"})
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-009"


async def test_batch_limit_evt007(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep)
    r = await client.post("/v1/events:batch", json={"events": [ev] * 501}, headers=auth(tenant["key"]))
    assert r.status_code == 413 and r.json()["errors"][0]["code"] == "TF-EVT-007"


async def test_regex_fail_fast_evt004(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    await client.post("/v1/id-namespaces",
                      json={"field": "user_ref", "namespace": "empid", "deployment_id": dep,
                            "format_regex": "^E[0-9]+$"}, headers=auth(tenant["key"]))
    bad = _activity(dep, payload={"user_ref": "not-an-empid"})
    r = await client.post("/v1/events:batch", json={"events": [bad]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-004"
    good = _activity(dep, payload={"user_ref": "E123"})
    r2 = await client.post("/v1/events:batch", json={"events": [good]}, headers=auth(tenant["key"]))
    assert r2.json()["data"]["results"][0]["status"] == "accepted"


async def test_partial_batch_acceptance(client, conn, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    good = _activity(dep, payload={"work_item_id": "OK"})
    bad = _activity(dep, payload={"content": "x", "work_item_id": "BAD"})
    r = await client.post("/v1/events:batch", json={"events": [good, bad]}, headers=auth(tenant["key"]))
    results = r.json()["data"]["results"]
    assert results[0]["status"] == "accepted" and results[0]["index"] == 0
    assert results[1]["status"] == "rejected" and results[1]["index"] == 1
    assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 1


async def test_event_status_endpoint(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep)
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    key = r.json()["data"]["results"][0]["event_key"]
    st = await client.get(f"/v1/events/{key}/status", headers=auth(tenant["key"]))
    assert st.json()["data"]["status"] == "accepted"
    # rejected lookup — distinct work_item so its key isn't shared with an accepted event
    rej = _activity(dep, payload={"content": "x", "work_item_id": "REJONLY"})
    rkey = (await client.post("/v1/events:batch", json={"events": [rej]},
                              headers=auth(tenant["key"]))).json()["data"]["results"][0].get("event_key")
    assert rkey
    rst = await client.get(f"/v1/events/{rkey}/status", headers=auth(tenant["key"]))
    assert rst.json()["data"]["status"] == "rejected"
    missing = await client.get("/v1/events/deadbeef/status", headers=auth(tenant["key"]))
    assert missing.status_code == 404 and missing.json()["errors"][0]["code"] == "TF-READ-001"


async def test_future_timestamp_evt009(client, tenant) -> None:
    dep = await _dep(client, tenant["key"])
    ev = _activity(dep, payload={"timestamp": "2099-01-01T00:00:00Z"})
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(tenant["key"]))
    assert r.json()["data"]["results"][0]["error"]["code"] == "TF-EVT-009"


async def test_malformed_batch_bodies(client, tenant) -> None:
    # not JSON
    r = await client.post("/v1/events:batch", content=b"{not json",
                          headers={**auth(tenant["key"]), "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-EVT-003"
    # events not a list
    r2 = await client.post("/v1/events:batch", json={"events": {}}, headers=auth(tenant["key"]))
    assert r2.status_code == 400 and r2.json()["errors"][0]["code"] == "TF-EVT-003"


async def test_test_mode_echo_cost_meter_interpreted_as(client, sandbox) -> None:
    dep = await _dep(client, sandbox["key"])
    ev = {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter",
          "origin": "customer_system",
          "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": TS}}
    r = await client.post("/v1/events:batch", json={"events": [ev]}, headers=auth(sandbox["key"]))
    echo = r.json()["data"]["results"][0]["echo"]
    assert echo["interpreted_as"] == "tokens_out" and "action_class" not in echo


async def test_test_mode_echo_no_persist(client, conn, sandbox) -> None:
    # A test-mode key in the sandbox environment: validate + echo, never persist.
    dep = await _dep(client, sandbox["key"])
    r = await client.post("/v1/events:batch", json={"events": [_activity(dep)]}, headers=auth(sandbox["key"]))
    res = r.json()["data"]["results"][0]
    assert res["status"] == "accepted"
    assert res["echo"]["accepted"] is True and res["echo"]["action_class"] == "suggestion_accepted"
    # Nothing persisted.
    assert conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"] == 0
