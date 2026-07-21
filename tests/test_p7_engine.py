"""P7 — engine + read plane: deterministic figures (arithmetic in comments),
refusal/awaiting states, grades, parameter stamping, reports, live/*, alerts.
"""

from __future__ import annotations

from conftest import auth
from truefigure_sdk.domain import engine

PERIOD = "2026-07"
D1 = "2026-07-01T10:00:00Z"
D2 = "2026-07-02T10:00:00Z"


async def _dep(client, key, dtype="saas_tool") -> tuple[str, int, int]:
    r = await client.post("/v1/deployments", json={"name": "D", "type": dtype}, headers=auth(key))
    ref = r.json()["data"]["deployment_id"]
    return ref, *(0, 0)


def _ids(conn, dep_ref):
    row = conn.execute("SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (dep_ref,)).fetchone()
    return int(row["workspace_id"]), int(row["id"])


async def _post(client, key, events, source=None):
    h = auth(key)
    if source:
        h["X-TrueFigure-Source"] = source
    return await client.post("/v1/events:batch", json={"events": events}, headers=h)


def _meter(dep, meter, qty, ts):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter",
            "origin": "customer_system", "payload": {"meter": meter, "quantity": qty, "timestamp": ts}}


# ---- cost figure: dollarized, parameter stamped -----------------------------
async def test_cost_figure_priced_and_stamped(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    # 100 + 250 = 350 tokens_out; price 0.002/unit -> 0.70 usd
    await _post(client, key, [_meter(dep, "tokens_out", 100, D1), _meter(dep, "tokens_out", 250, D2)])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.002}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "computed" and d["grade"] == "measured"
    assert d["value"] == 0.70  # 350 * 0.002
    assert d["parameter_set_version"] == 1  # stamped


async def test_cost_awaiting_parameters(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await _post(client, key, [_meter(dep, "tokens_out", 100, D1)])
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)  # no parameters declared
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "awaiting_parameters" and d["missing_parameter"] == "parameter_set"


# ---- seat utilization / waste ----------------------------------------------
async def test_seat_utilization_and_waste(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "kind": "person"},
                                                          {"user_ref": "u_2", "kind": "person"}]},
                      headers=auth(key))
    # 2 of 10 paid seats active
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
         "payload": {"user_ref": u, "timestamp": D1, "work_item_id": "W", "action_type": "suggestion_accepted"}}
        for u in ("u_1", "u_2")])
    pipeline.run_pipeline()
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 10, "price_per_seat": 100.0, "period_unit": "year",
                           "valid_from": "2026-01-01"}, headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_seats_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    # waste = (10 - 2) * 100 = 800
    assert d["status"] == "computed" and d["value"] == 800.0


async def test_live_usage_classification(client, conn, tenant) -> None:
    from datetime import UTC, datetime, timedelta

    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "kind": "person"}]},
                      headers=auth(key))
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
         "payload": {"user_ref": "u_1", "timestamp": recent, "work_item_id": "W", "action_type": "suggestion_accepted"}}])
    pipeline.run_pipeline()
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 5, "price_per_seat": 50.0, "period_unit": "year", "valid_from": "2026-01-01"},
                     headers=auth(key))
    r = await client.get(f"/v1/live/usage/{dep}", headers=auth(key))
    d = r.json()["data"]
    # activated=1 is below the k-anonymity floor (BR-012): totals kept, split suppressed.
    assert d["seats"]["paid"] == 5 and d["seats"]["activated"] == 1
    assert d["seats"]["never_activated"] == 4
    assert d["cohort_status"] == "not_disclosable" and d["code"] == "TF-READ-003"
    assert "near_zero" not in d["seats"]


# ---- live/cost --------------------------------------------------------------
async def test_live_cost_priced_and_unpriced(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await _post(client, key, [_meter(dep, "tokens_out", 1000, D1), _meter(dep, "seats_active", 40, D1)])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.001}}},
                      headers=auth(key))
    r = await client.get(f"/v1/live/cost/{dep}?period={PERIOD}", headers=auth(key))
    d = r.json()["data"]
    by = {m["meter"]: m for m in d["meters"]}
    assert by["tokens_out"]["usd"] == 1.0  # 1000 * 0.001
    assert by["seats_active"]["usd"] is None and by["seats_active"]["status"] == "awaiting_parameters"
    assert d["period_to_date_usd"] == 1.0  # priced meters only


# ---- containment (agent) with grade ----------------------------------------
async def test_containment_agent(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key, dtype="agent")
    acts = [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
         "payload": {"user_ref": f"u{i}", "timestamp": D1, "work_item_id": f"W{i}",
                     "action_type": "case_handled_autonomous"}} for i in range(3)]
    acts.append({"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
                 "payload": {"user_ref": "u9", "timestamp": D2, "work_item_id": "W9",
                             "action_type": "escalated_to_human"}})
    await _post(client, key, acts)
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_containment_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "computed" and d["value"] == 0.75  # 3/(3+1)


# ---- report issuance binds exact figure versions ---------------------------
async def test_report_issuance_and_get(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await _post(client, key, [_meter(dep, "tokens_out", 100, D1)])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    rep_ref = engine.issue_report(ws, did, PERIOD)
    r = await client.get(f"/v1/reports/{dep}/{rep_ref}", headers=auth(key))
    d = r.json()["data"]
    assert d["version"] == 1 and d["period"] == PERIOD
    assert any(f["figure_id"] == f"fig_cost_{PERIOD}" for f in d["figures"])
    assert d["artifact_url"] is not None
    # listed too
    lst = await client.get(f"/v1/reports/{dep}", headers=auth(key))
    assert lst.json()["data"]["results"][0]["report_id"] == rep_ref


# ---- lineage ----------------------------------------------------------------
async def test_lineage_references(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await _post(client, key, [_meter(dep, "tokens_out", 500, D1)])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}/lineage", headers=auth(key))
    d = r.json()["data"]
    assert d["parameter_set_version"] == 1
    assert d["lineage"]["priced_meters"] == ["tokens_out"]


# ---- live/health ------------------------------------------------------------
async def test_live_health_rates(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "kind": "person"}]},
                      headers=auth(key))
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
         "payload": {"user_ref": "u_1", "timestamp": D1, "work_item_id": "W1", "action_type": "suggestion_accepted"}}])
    pipeline.run_pipeline()
    r = await client.get(f"/v1/live/health/{dep}", headers=auth(key))
    d = r.json()["data"]
    assert d["identity_resolution_rate"] == 1.0  # 1 resolved / 1 identity-bearing
    assert d["work_item_join_rate"] == 1.0  # 1 joined / 1 activity
    assert d["last_event_at"] is not None


# ---- alerts + ack -----------------------------------------------------------
async def test_alerts_monitor_and_ack(client, conn, tenant) -> None:
    from truefigure_sdk.domain.policies import pipeline
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    # 10 activity events, none joinable (no work_item_id) -> join_rate_drop
    acts = [{"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter",
             "origin": "customer_system", "payload": {"meter": "api_calls", "quantity": 1,
                                                       "timestamp": f"2026-07-01T10:{i:02d}:00Z"}} for i in range(3)]
    # use activity without work item to force activity_count>0 joined=0
    acts = [{"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
             "payload": {"user_ref": f"u{i}", "timestamp": f"2026-07-01T10:{i:02d}:00Z",
                         "work_item_id": "", "action_type": "lookup_performed"}} for i in range(10)]
    # work_item_id empty string -> not joined
    await _post(client, key, acts)
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    raised = engine.run_monitors(ws)
    assert raised  # at least one alert
    lst = await client.get("/v1/alerts?status=open", headers=auth(key))
    alerts = lst.json()["data"]["results"]
    assert alerts and alerts[0]["code"] in ("join_rate_drop", "event_silence")
    aid = alerts[0]["alert_id"]
    ack = await client.post(f"/v1/alerts/{aid}:ack", headers=auth(key))
    assert ack.status_code == 200
    row = conn.execute("SELECT alert_status, acked_by, acked_at FROM alerts WHERE alert_ref=%s", (aid,)).fetchone()
    assert row["alert_status"] == "acked" and row["acked_by"] is not None and row["acked_at"] is not None


async def test_figure_not_found_and_period_not_computed(client, tenant) -> None:
    key = tenant["key"]
    dep, *_ = await _dep(client, key)
    r = await client.get(f"/v1/figures/{dep}/fig_nope", headers=auth(key))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"
    pnc = await client.get(f"/v1/figures/{dep}?period=2099-01", headers=auth(key))
    assert pnc.json()["data"]["status"] == "period_not_computed"
