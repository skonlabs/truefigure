"""Deep-audit coverage: exercises every remaining reachable error/edge branch
across the SDK so coverage is comprehensive and the branches are proven correct.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from conftest import auth

T1 = "2026-07-01T10:00:00Z"


def _act(dep, user, wi, ts, action="suggestion_accepted", origin="customer_system"):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": origin,
            "payload": {"user_ref": user, "timestamp": ts, "work_item_id": wi, "action_type": action}}


async def _mkdep(client, key, **kw):
    body = {"name": "D", "type": "saas_tool"}
    body.update(kw)
    r = await client.post("/v1/deployments", json=body, headers=auth(key))
    return r.json()["data"]["deployment_id"]


async def _post(client, key, events, source=None):
    h = auth(key)
    if source:
        h["X-TrueFigure-Source"] = source
    return await client.post("/v1/events:batch", json={"events": events}, headers=h)


def _ids(conn, dep):
    r = conn.execute("SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()
    return int(r["workspace_id"]), int(r["id"])


# ---- config module -----------------------------------------------------------
def test_config_module_edges() -> None:
    import os

    from truefigure_sdk import config

    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.is_production is True  # config.py:24
    # missing required var
    saved = os.environ.pop("DATABASE_URL")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        config.get_settings()  # config.py:30
    os.environ["DATABASE_URL"] = saved
    # invalid environment
    os.environ["TF_ENVIRONMENT"] = "weird"
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        config.get_settings()  # config.py:39
    os.environ["TF_ENVIRONMENT"] = "production"
    config.get_settings.cache_clear()


# ---- auth empty bearer -------------------------------------------------------
async def test_auth_empty_bearer(client) -> None:
    r = await client.get("/v1/whoami", headers={"Authorization": "Bearer   "})
    assert r.status_code == 401 and r.json()["errors"][0]["code"] == "TF-AUTH-001"


# ---- app generic exception handler ------------------------------------------
async def test_generic_exception_handler_returns_srv001() -> None:
    from types import SimpleNamespace

    from truefigure_sdk.app import handle_unexpected

    req = SimpleNamespace(state=SimpleNamespace(request_id="req_test"))
    resp = await handle_unexpected(req, RuntimeError("boom"))  # type: ignore[arg-type]
    import json as _json

    body = _json.loads(bytes(resp.body))
    assert resp.status_code == 500 and body["errors"][0]["code"] == "TF-SRV-001"


# ---- config_plane edges ------------------------------------------------------
async def test_patch_planned_rollout_and_roster_filter(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _mkdep(client, key)
    r = await client.patch(f"/v1/deployments/{dep}", json={"planned_rollout_at": "2026-12-01T00:00:00Z"},
                           headers=auth(key))
    assert r.status_code == 200
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u1", "kind": "person"}]}, headers=auth(key))
    # roster list with status filter (config_plane:250-251)
    lst = await client.get("/v1/roster?status=active", headers=auth(key))
    assert any(u["user_ref"] == "u1" for u in lst.json()["data"]["results"])


async def test_delete_webhook_not_found(client, tenant) -> None:
    r = await client.delete("/v1/webhooks/wh_missing", headers=auth(tenant["key"]))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"


# ---- engine branches ---------------------------------------------------------
async def test_change_treatment_one_sided_adjusted(client, conn, tenant) -> None:
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await _post(client, key, [{"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter",
                               "origin": "customer_system",
                               "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": T1}}])
    pipeline.run_pipeline()
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    # one-sided, NON tool_change -> one_sided_adjusted (engine.py:98)
    await client.post("/v1/change-events",
                      json={"type": "process_change", "sidedness": "one_sided", "occurred_at": "2026-07-10T00:00:00Z",
                            "description": "d", "deployment_refs": [dep]}, headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")
    d = (await client.get(f"/v1/figures/{dep}/fig_cost_2026-07", headers=auth(key))).json()["data"]
    assert d["change_treatment"] == "one_sided_adjusted"


async def test_cost_only_unpriced_meters_awaiting(client, conn, tenant) -> None:
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await _post(client, key, [{"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter",
                               "origin": "customer_system",
                               "payload": {"meter": "api_calls", "quantity": 5, "timestamp": T1}}])
    pipeline.run_pipeline()
    # parameters exist but price only a DIFFERENT meter -> the present meter is unpriced (engine 126-127,131)
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")
    d = (await client.get(f"/v1/figures/{dep}/fig_cost_2026-07", headers=auth(key))).json()["data"]
    assert d["status"] == "awaiting_parameters" and d["missing_parameter"] == "unit_prices.api_calls"


async def test_seats_price_from_parameters_customer_entered(client, conn, tenant) -> None:
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u1", "kind": "person"}]}, headers=auth(key))
    await _post(client, key, [_act(dep, "u1", "W", T1)])
    pipeline.run_pipeline()
    # license WITHOUT price; parameters supply seats_active price -> customer_entered (engine 166-167)
    await client.put(f"/v1/deployments/{dep}/license", json={"seats_paid": 4, "valid_from": "2026-01-01"},
                     headers=auth(key))
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"seats_active": 10}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")
    d = (await client.get(f"/v1/figures/{dep}/fig_seats_2026-07/lineage", headers=auth(key))).json()["data"]
    assert d["lineage"]["price_provenance"] == "customer_entered"


async def test_containment_none_when_no_agent_actions(client, conn, tenant) -> None:
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key, type="agent")
    await _post(client, key, [_act(dep, "u", "W", T1, action="suggestion_accepted")])  # not autonomous/escalated
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")  # containment returns None (engine 193)
    r = await client.get(f"/v1/figures/{dep}/fig_containment_2026-07", headers=auth(key))
    assert r.status_code == 404


async def test_time_savings_measured_grade(client, conn, tenant) -> None:
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    ev = []
    for i in range(3):  # 3 assisted at 1h
        ev += [{"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"A{i}", "event": "created", "timestamp": f"2026-07-0{i+1}T00:00:00Z"}},
               {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"A{i}", "event": "completed", "timestamp": f"2026-07-0{i+1}T01:00:00Z"}},
               _act(dep, "u", f"A{i}", f"2026-07-0{i+1}T00:30:00Z")]
    for i in range(3):  # 3 unassisted at 5h
        ev += [{"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"U{i}", "event": "created", "timestamp": f"2026-07-1{i}T00:00:00Z"}},
               {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"U{i}", "event": "completed", "timestamp": f"2026-07-1{i}T05:00:00Z"}}]
    await _post(client, key, ev)
    pipeline.run_pipeline()
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"labor_rates": {"default": 40}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")
    d = (await client.get(f"/v1/figures/{dep}/fig_time_2026-07", headers=auth(key))).json()["data"]
    assert d["status"] == "computed" and d["grade"] == "measured"  # n=6 >= 4 (engine 261)


# ---- imports edges -----------------------------------------------------------
async def test_import_not_found_and_blank_lines(client, conn, tenant) -> None:
    from truefigure_sdk import imports, storage
    from truefigure_sdk.errors import TFError
    key = tenant["key"]
    with pytest.raises(TFError):
        imports.run_import("imp_missing")  # imports.py:88
    dep = await _mkdep(client, key)
    imp = (await client.post("/v1/imports", json={"kind": "backfill"}, headers=auth(key))).json()["data"]["import_id"]
    ws = conn.execute("SELECT workspace_id FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()["workspace_id"]
    import json as _json
    ndjson = _json.dumps(_act(dep, "u", "W", T1)) + "\n\n   \n"  # blank lines skipped (imports.py:105)
    storage.get_storage().put("import-uploads", f"{ws}/{imp}.ndjson", ndjson.encode())
    assert imports.run_import(imp)["received"] == 1


# ---- ingest edges ------------------------------------------------------------
async def test_body_too_large_and_non_object_event(client, tenant) -> None:
    key = tenant["key"]
    dep = await _mkdep(client, key)
    big = {"events": [_act(dep, "u", "W" + "x" * 2000, T1) for _ in range(500)]}
    import json as _json
    # ensure > 1 MiB
    payload = _json.dumps(big)
    if len(payload) <= 1024 * 1024:
        big = {"events": [_act(dep, "u", "W" + "x" * 5000, T1) for _ in range(300)]}
    r = await client.post("/v1/events:batch", content=_json.dumps(big).encode(),
                          headers={**auth(key), "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["errors"][0]["code"] == "TF-EVT-007"  # ingest 136
    # non-object event + unkeyable event
    r2 = await _post(client, key, [123, {"schema_version": "1.0"}])  # ingest 173, 239
    codes = [x["error"]["code"] for x in r2.json()["data"]["results"]]
    assert codes[0] == "TF-EVT-003"


# ---- pipeline edges ----------------------------------------------------------
async def test_pipeline_resolved_no_workitem(client, conn, tenant) -> None:
    from truefigure_sdk import pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u1", "kind": "person"}]}, headers=auth(key))
    await _post(client, key, [_act(dep, "u1", "", T1)])  # empty work_item -> resolved, not joined (pipeline 68)
    pipeline.run_pipeline()
    row = conn.execute("SELECT pipeline_status, resolved_user_id, work_item_id FROM events").fetchone()
    assert row["pipeline_status"] == "resolved" and row["resolved_user_id"] is not None and row["work_item_id"] is None


async def test_pipeline_blocked_user_excluded(client, conn, tenant) -> None:
    from truefigure_sdk import pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "b1", "kind": "person"}]}, headers=auth(key))
    conn.execute("UPDATE users SET user_status='blocked' WHERE user_ref='b1'")
    await _post(client, key, [_act(dep, "b1", "W", T1)])
    pipeline.run_pipeline()
    row = conn.execute("SELECT exclusion_reason FROM events").fetchone()
    assert row["exclusion_reason"] == "unresolved_identity"  # pipeline 141


async def test_pipeline_effective_window_closed(client, conn, tenant) -> None:
    from truefigure_sdk import pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    await client.post("/v1/roster:batch",
                      json={"users": [{"user_ref": "x1", "kind": "person", "effective_to": "2026-06-01"}]},
                      headers=auth(key))
    await _post(client, key, [_act(dep, "x1", "W", T1)])  # event after effective_to -> unresolved (pipeline 143)
    pipeline.run_pipeline()
    row = conn.execute("SELECT resolved_user_id, exclusion_reason FROM events").fetchone()
    assert row["resolved_user_id"] is None and row["exclusion_reason"] is None


# ---- read_plane edges --------------------------------------------------------
async def test_read_unknown_deployment_and_missing(client, tenant) -> None:
    key = tenant["key"]
    for url in ("/v1/figures/dep_ghost", "/v1/reports/dep_ghost"):
        r = await client.get(url, headers=auth(key))
        assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"  # read_plane 30
    dep = await _mkdep(client, key)
    assert (await client.get(f"/v1/figures/{dep}/fig_x", headers=auth(key))).status_code == 404  # 95
    assert (await client.get(f"/v1/reports/{dep}/rep_x", headers=auth(key))).status_code == 404  # 129


async def test_live_usage_no_license_and_full_classification(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _mkdep(client, key)
    # no license -> awaiting_license_declaration (read_plane 186-189); 0 activated avoids k-floor
    d0 = (await client.get(f"/v1/live/usage/{dep}", headers=auth(key))).json()["data"]
    assert d0["waste"]["status"] == "awaiting_license_declaration"

    # Seed >=5 activated users with distinct classifications in the trailing 30d window.
    users = [{"user_ref": f"c{i}", "kind": "person"} for i in range(5)]
    await client.post("/v1/roster:batch", json={"users": users}, headers=auth(key))
    ws, did = _ids(conn, dep)
    uids = {r["user_ref"]: r["id"] for r in conn.execute("SELECT id, user_ref FROM users WHERE user_ref LIKE 'c%'").fetchall()}
    today = datetime.now(UTC).date()

    def seed(user_ref, ndays, per_day):
        for k in range(ndays):
            d = today - timedelta(days=k)
            conn.execute("INSERT INTO user_activity_daily (workspace_id, deployment_id, user_id, activity_date, "
                         "event_count, created_by, updated_by) VALUES (%s,%s,%s,%s,%s,1,1)",
                         (ws, did, uids[user_ref], d, per_day))
    seed("c0", 16, 10)   # heavy: 16 days, 160 events
    seed("c1", 8, 3)     # moderate: 8 days, 24 events
    seed("c2", 1, 1)     # near_zero
    seed("c3", 2, 1)     # near_zero (days<3)
    seed("c4", 4, 1)     # near_zero (events=4 < 5)
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 8, "price_per_seat": 100.0, "period_unit": "month", "valid_from": "2026-01-01"},
                     headers=auth(key))
    d = (await client.get(f"/v1/live/usage/{dep}", headers=auth(key))).json()["data"]
    assert d["seats"]["activated"] == 5
    assert d["seats"]["heavy"] == 1 and d["seats"]["moderate"] == 1 and d["seats"]["near_zero"] == 3  # 161-164
    # waste priced, period_unit month -> annualized *12 (read_plane 196-197)
    assert d["waste"]["annual_usd"] == 100.0 * 3 * 12 and d["waste"]["price_provenance"] == "license_data"


async def test_ack_unknown_alert(client, tenant) -> None:
    r = await client.post("/v1/alerts/al_missing:ack", headers=auth(tenant["key"]))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"  # read_plane 319


# ---- secretbox + webhook verify edges ---------------------------------------
def test_secretbox_rejects_bad_token() -> None:
    from truefigure_sdk import secretbox

    with pytest.raises(ValueError):
        secretbox.decrypt("not-an-enc1-token")  # secretbox 45
    # roundtrip
    assert secretbox.decrypt(secretbox.encrypt("hello")) == "hello"


async def test_verify_webhook_unknown_returns_false(conn, tenant) -> None:
    from truefigure_sdk import webhooks_delivery
    ws = conn.execute("SELECT id FROM workspaces WHERE workspace_ref='ws_prod'").fetchone()["id"]
    assert webhooks_delivery.verify_webhook("wh_missing", int(ws), lambda *a: 200) is False  # webhooks 182


async def test_time_savings_awaiting_parameters_when_no_param_version(client, conn, tenant) -> None:
    # Same measurable effect as the measured test, but NO parameter version -> awaiting labor_rates.
    from truefigure_sdk import engine, pipeline
    key = tenant["key"]
    dep = await _mkdep(client, key)
    ev = []
    for i in range(3):  # 3 assisted at 1h
        ev += [{"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"A{i}", "event": "created", "timestamp": f"2026-07-0{i+1}T00:00:00Z"}},
               {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"A{i}", "event": "completed", "timestamp": f"2026-07-0{i+1}T01:00:00Z"}},
               _act(dep, "u", f"A{i}", f"2026-07-0{i+1}T00:30:00Z")]
    for i in range(3):  # 3 unassisted at 5h
        ev += [{"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"U{i}", "event": "created", "timestamp": f"2026-07-1{i}T00:00:00Z"}},
               {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": "customer_system",
                "payload": {"work_item_id": f"U{i}", "event": "completed", "timestamp": f"2026-07-1{i}T05:00:00Z"}}]
    await _post(client, key, ev)
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")  # no /v1/parameters posted -> pv is None
    d = (await client.get(f"/v1/figures/{dep}/fig_time_2026-07", headers=auth(key))).json()["data"]
    assert d["status"] == "awaiting_parameters" and d["missing_parameter"] == "labor_rates"  # engine 261-264


async def test_figure_lineage_not_found(client, tenant) -> None:
    key = tenant["key"]
    dep = await _mkdep(client, key)
    r = await client.get(f"/v1/figures/{dep}/fig_ghost/lineage", headers=auth(key))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"  # read_plane 95
