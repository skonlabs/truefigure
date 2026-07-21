"""P7 extra — time-savings (computed/refused/awaiting), change treatment,
period grammar. Deterministic arithmetic shown in comments.
"""

from __future__ import annotations

import pytest

from conftest import auth
from truefigure_sdk.domain import engine
from truefigure_sdk.domain.entities import periods
from truefigure_sdk.domain.policies import pipeline

PERIOD = "2026-07"


def test_period_bounds_month_and_quarter() -> None:
    from datetime import date

    assert periods.period_bounds("2026-07") == (date(2026, 7, 1), date(2026, 7, 31))
    assert periods.period_bounds("2026-q3") == (date(2026, 7, 1), date(2026, 9, 30))
    assert periods.period_bounds("2026-q4") == (date(2026, 10, 1), date(2026, 12, 31))
    assert periods.period_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        periods.period_bounds("2026-13")


async def _dep(client, key, dtype="saas_tool") -> str:
    r = await client.post("/v1/deployments", json={"name": "D", "type": dtype}, headers=auth(key))
    return r.json()["data"]["deployment_id"]


def _ids(conn, dep_ref):
    row = conn.execute("SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (dep_ref,)).fetchone()
    return int(row["workspace_id"]), int(row["id"])


def _life(dep, wi, event, ts, **extra):
    p = {"work_item_id": wi, "event": event, "timestamp": ts}
    p.update(extra)
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle",
            "origin": "customer_system", "payload": p}


def _act(dep, wi, ts):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": "customer_system",
            "payload": {"user_ref": "u", "timestamp": ts, "work_item_id": wi, "action_type": "draft_generated"}}


async def _post(client, key, events):
    return await client.post("/v1/events:batch", json={"events": events}, headers=auth(key))


async def test_time_savings_computed(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    # assisted items A1,A2 take 1h; unassisted U1,U2 take 5h. effect=4h, margin=(5-1)/2=2 -> computed.
    events = [
        _life(dep, "A1", "created", "2026-07-01T00:00:00Z"), _life(dep, "A1", "completed", "2026-07-01T01:00:00Z"),
        _life(dep, "A2", "created", "2026-07-02T00:00:00Z"), _life(dep, "A2", "completed", "2026-07-02T01:00:00Z"),
        _life(dep, "U1", "created", "2026-07-03T00:00:00Z"), _life(dep, "U1", "completed", "2026-07-03T05:00:00Z"),
        _life(dep, "U2", "created", "2026-07-04T00:00:00Z"), _life(dep, "U2", "completed", "2026-07-04T05:00:00Z"),
        _act(dep, "A1", "2026-07-01T00:30:00Z"), _act(dep, "A2", "2026-07-02T00:30:00Z"),
    ]
    await _post(client, key, events)
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"labor_rates": {"default": 50}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_time_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "computed" and d["value_class"] == "capacity"
    # value = effect(4h) * assisted_n(2) * rate(50) = 400
    assert d["value"] == 400.0
    assert d["parameter_set_version"] == 1


async def test_time_savings_refused_margin_exceeds_effect(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    # assisted [1h, 9h], unassisted [2h, 8h] -> means ~equal, wide spread -> margin > effect -> REFUSED.
    events = [
        _life(dep, "A1", "created", "2026-07-01T00:00:00Z"), _life(dep, "A1", "completed", "2026-07-01T01:00:00Z"),
        _life(dep, "A2", "created", "2026-07-02T00:00:00Z"), _life(dep, "A2", "completed", "2026-07-02T09:00:00Z"),
        _life(dep, "U1", "created", "2026-07-03T00:00:00Z"), _life(dep, "U1", "completed", "2026-07-03T02:00:00Z"),
        _life(dep, "U2", "created", "2026-07-04T00:00:00Z"), _life(dep, "U2", "completed", "2026-07-04T08:00:00Z"),
        _act(dep, "A1", "2026-07-01T00:30:00Z"), _act(dep, "A2", "2026-07-02T00:30:00Z"),
    ]
    await _post(client, key, events)
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_time_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "refused" and d["margin_exceeds_effect"] is True
    assert d["refusal_reason"] and d["expected_verdict_date"] is not None


async def test_time_savings_awaiting_labor_rates(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    events = [
        _life(dep, "A1", "created", "2026-07-01T00:00:00Z"), _life(dep, "A1", "completed", "2026-07-01T01:00:00Z"),
        _life(dep, "U1", "created", "2026-07-03T00:00:00Z"), _life(dep, "U1", "completed", "2026-07-03T05:00:00Z"),
        _act(dep, "A1", "2026-07-01T00:30:00Z"),
    ]
    await _post(client, key, events)
    pipeline.run_pipeline()
    # params exist but carry no labor_rates -> awaiting labor_rates.default
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_time_{PERIOD}", headers=auth(key))
    d = r.json()["data"]
    assert d["status"] == "awaiting_parameters" and d["missing_parameter"] == "labor_rates.default"


async def test_change_treatment_incumbent_tool_change_excluded(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": "2026-07-01T10:00:00Z"}}])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    # A one-sided incumbent tool_change in-period -> never credited to the measured vendor.
    await client.post("/v1/change-events",
                      json={"type": "tool_change", "sidedness": "one_sided",
                            "occurred_at": "2026-07-15T00:00:00Z", "description": "incumbent core upgrade",
                            "deployment_refs": [dep]}, headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))
    assert r.json()["data"]["change_treatment"] == "incumbent_tool_change_excluded"


async def test_change_treatment_symmetric_cancels(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": "2026-07-01T10:00:00Z"}}])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    await client.post("/v1/change-events",
                      json={"type": "seasonal", "sidedness": "symmetric",
                            "occurred_at": "2026-07-10T00:00:00Z", "description": "holiday"},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))
    assert r.json()["data"]["change_treatment"] == "symmetric_cancel"


async def test_figure_versions_append(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await _post(client, key, [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": "2026-07-01T10:00:00Z"}}])
    pipeline.run_pipeline()
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, PERIOD)
    engine.compute_deployment(ws, did, PERIOD)  # recompute -> new version
    v = conn.execute("SELECT max(version) AS v FROM figures WHERE figure_ref=%s", (f"fig_cost_{PERIOD}",)).fetchone()["v"]
    assert v == 2
    # get returns the latest version
    r = await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))
    assert r.json()["data"]["version"] == 2
