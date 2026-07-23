"""Every behavior-driving enum value is exercised, and DB<->wire enum set-equality
for the shared enums (event_type, origin_type + shared vocabularies).
"""

from __future__ import annotations

from _helpers import activity, auth, cost_meter, lifecycle, make_deployment, post_events, provision

from truefigure_server.domain import engine
from truefigure_server.domain.policies import pipeline
from truefigure_server.domain.policies import webhooks_delivery


def _ids(conn, dep):
    r = conn.execute("SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()
    return int(r["workspace_id"]), int(r["id"])


async def test_all_figure_statuses_produced(client, ops, conn) -> None:
    """computed, refused, awaiting_parameters all reachable."""
    key = provision(ops)
    dep = await make_deployment(client, key)
    # awaiting_parameters (cost, no params) + computed (cost, with params) via two periods
    await post_events(client, key, [cost_meter(dep, "tokens_out", 100, "2026-07-01T00:00:00Z")])
    # refused time figure: 2-per-group wide spread
    await post_events(client, key, [
        lifecycle(dep, "A1", "created", "2026-07-01T00:00:00Z"), lifecycle(dep, "A1", "completed", "2026-07-01T01:00:00Z"),
        lifecycle(dep, "A2", "created", "2026-07-02T00:00:00Z"), lifecycle(dep, "A2", "completed", "2026-07-02T09:00:00Z"),
        lifecycle(dep, "U1", "created", "2026-07-03T00:00:00Z"), lifecycle(dep, "U1", "completed", "2026-07-03T02:00:00Z"),
        lifecycle(dep, "U2", "created", "2026-07-04T00:00:00Z"), lifecycle(dep, "U2", "completed", "2026-07-04T08:00:00Z"),
        activity(dep, "a", "A1", "2026-07-01T00:30:00Z"), activity(dep, "b", "A2", "2026-07-02T00:30:00Z")])
    pipeline.run_pipeline()
    ws, did = _ids(conn, dep)
    engine.compute_deployment(ws, did, "2026-07")  # cost awaiting, time refused
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"unit_prices": {"tokens_out": 0.01}}},
                      headers=auth(key))
    engine.compute_deployment(ws, did, "2026-07")  # cost computed now
    statuses = {r["figure_status"] for r in conn.execute("SELECT DISTINCT figure_status FROM figures").fetchall()}
    assert {"computed", "refused", "awaiting_parameters"} <= statuses


async def test_all_grades_produced(client, ops, conn) -> None:
    """estimate (thin time-savings), measured (cost), verified (containment + G6)."""
    key = provision(ops)
    # estimate: time-savings with < 4 samples
    d1 = await make_deployment(client, key, name="thin")
    await post_events(client, key, [
        lifecycle(d1, "A", "created", "2026-07-01T00:00:00Z"), lifecycle(d1, "A", "completed", "2026-07-01T01:00:00Z"),
        lifecycle(d1, "U", "created", "2026-07-02T00:00:00Z"), lifecycle(d1, "U", "completed", "2026-07-02T05:00:00Z"),
        activity(d1, "u", "A", "2026-07-01T00:30:00Z")])
    # verified: agent + G6 corroboration
    d2 = await make_deployment(client, key, name="agent", dtype="agent")
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "svc", "kind": "service"}]}, headers=auth(key))
    conn.execute("INSERT INTO deployment_service_users (workspace_id, deployment_id, user_id, created_by, updated_by) "
                 "SELECT d.workspace_id,d.id,u.id,1,1 FROM deployments d,users u WHERE d.deployment_ref=%s AND u.user_ref='svc'",
                 (d2,))
    await post_events(client, key, [
        activity(d2, "a", "W1", "2026-07-01T00:00:00Z", action="case_handled_autonomous"),
        activity(d2, "svc", "W1", "2026-07-01T02:00:00Z", action="lookup_performed", origin="customer_system"),
        cost_meter(d1, "tokens_out", 100, "2026-07-01T00:00:00Z")])
    pipeline.run_pipeline()
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01",
                                              "payload": {"unit_prices": {"tokens_out": 0.01}, "labor_rates": {"default": 50}}},
                      headers=auth(key))
    for d in (d1, d2):
        ws, did = _ids(conn, d)
        engine.compute_deployment(ws, did, "2026-07")
    grades = {r["grade_type"] for r in conn.execute("SELECT DISTINCT grade_type FROM figures WHERE grade_type IS NOT NULL").fetchall()}
    assert {"estimate", "measured", "verified"} <= grades


async def test_all_webhook_event_types_delivered(client, ops, conn) -> None:
    """figure.updated, refusal.lifted, alert.raised, report.issued all delivered."""
    key = provision(ops)
    ws = conn.execute("SELECT id FROM workspaces WHERE workspace_ref='ws_prod'").fetchone()["id"]
    # subscribe + verify a webhook for all four topics
    wid = (await client.post("/v1/webhooks",
                             json={"url": "https://h", "events": ["figure.updated", "refusal.lifted",
                                                                  "alert.raised", "report.issued"], "secret": "s"},
                             headers=auth(key))).json()["data"]["webhook_id"]
    webhooks_delivery.verify_webhook(wid, int(ws), lambda *a: 200)
    dep = await make_deployment(client, key)
    # refused -> computed to fire refusal.lifted
    await post_events(client, key, [
        lifecycle(dep, "A1", "created", "2026-07-01T00:00:00Z"), lifecycle(dep, "A1", "completed", "2026-07-01T01:00:00Z"),
        lifecycle(dep, "A2", "created", "2026-07-02T00:00:00Z"), lifecycle(dep, "A2", "completed", "2026-07-02T09:00:00Z"),
        lifecycle(dep, "U1", "created", "2026-07-03T00:00:00Z"), lifecycle(dep, "U1", "completed", "2026-07-03T02:00:00Z"),
        lifecycle(dep, "U2", "created", "2026-07-04T00:00:00Z"), lifecycle(dep, "U2", "completed", "2026-07-04T08:00:00Z"),
        activity(dep, "a", "A1", "2026-07-01T00:30:00Z"), activity(dep, "b", "A2", "2026-07-02T00:30:00Z"),
        activity(dep, "c", "", "2026-07-01T00:00:00Z", action="lookup_performed")])
    pipeline.run_pipeline()
    ws_id, did = _ids(conn, dep)
    engine.compute_deployment(ws_id, did, "2026-07")  # time refused (margin > effect)
    await client.post("/v1/parameters", json={"effective_from": "2026-07-01", "payload": {"labor_rates": {"default": 50}}},
                      headers=auth(key))
    # Add consistent unassisted-slow samples: raises the effect and (via sqrt(n))
    # tightens the margin so the claim becomes answerable -> refusal.lifted.
    more = []
    for i in range(6):
        d = 10 + i
        more += [lifecycle(dep, f"UX{i}", "created", f"2026-07-{d:02d}T00:00:00Z"),
                 lifecycle(dep, f"UX{i}", "completed", f"2026-07-{d:02d}T08:00:00Z")]
    await post_events(client, key, more)
    pipeline.run_pipeline()
    engine.compute_deployment(ws_id, did, "2026-07")
    engine.issue_report(ws_id, did, "2026-07")           # report.issued
    engine.run_monitors(ws_id)                            # alert.raised (join_rate_drop from empty work items)
    types = {r["webhook_event_type"] for r in conn.execute(
        "SELECT DISTINCT webhook_event_type::text AS webhook_event_type FROM webhook_deliveries").fetchall()}
    assert {"figure.updated", "refusal.lifted", "alert.raised", "report.issued"} <= types


async def test_all_import_types(client, ops, conn) -> None:
    key = provision(ops)
    seen = set()
    for kind in ("backfill", "publication", "migration", "other"):
        r = await client.post("/v1/imports", json={"kind": kind}, headers=auth(key))
        assert r.status_code == 201
        seen.add(kind)
    stored = {r["import_type"] for r in conn.execute("SELECT DISTINCT import_type FROM import_jobs").fetchall()}
    assert seen == stored == {"backfill", "publication", "migration", "other"}


async def test_both_origins_and_modes(client, ops, conn) -> None:
    key = provision(ops)
    dep = await make_deployment(client, key)
    await post_events(client, key, [activity(dep, "u", "W1", "2026-07-01T00:00:00Z", origin="vendor_product"),
                                    activity(dep, "u", "W2", "2026-07-01T00:00:00Z", origin="customer_system")])
    origins = {r["origin_type"] for r in conn.execute("SELECT DISTINCT origin_type FROM events").fetchall()}
    assert origins == {"vendor_product", "customer_system"}
    # both api_key_mode values exist (production issued above; issue a test-mode key too)
    ops("key", "issue", "--workspace-ref", "ws_prod", "--owner-user-ref", "u_admin", "--mode", "test")
    modes = {r["api_key_mode"] for r in conn.execute("SELECT DISTINCT api_key_mode FROM api_keys").fetchall()}
    assert modes == {"production", "test"}


def test_db_wire_enum_set_equality(conn) -> None:
    """The shared vocabularies match between the wire contract and DB enums."""
    import json as _json
    from pathlib import Path

    schema = _json.loads(Path(__file__).resolve().parents[2].joinpath("contract/events.schema.json").read_text())
    defs = schema["$defs"]
    # event_type (envelope) <-> DB enum event_type
    wire_event_type = set(defs["envelope"]["properties"]["event_type"]["enum"])
    db_event_type = {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='event_type'").fetchall()}
    assert wire_event_type == db_event_type
    # origin (envelope) <-> DB enum origin_type
    wire_origin = set(defs["envelope"]["properties"]["origin"]["enum"])
    db_origin = {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='origin_type'").fetchall()}
    assert wire_origin == db_origin
    # activity action_type <-> nothing in DB (payload jsonb) but is closed on the wire; assert size
    assert len(defs["activity"]["properties"]["action_type"]["enum"]) == 10
    # meter <-> DB enum meter_type
    wire_meter = set(defs["cost_meter"]["properties"]["meter"]["enum"])
    db_meter = {r["enumlabel"] for r in conn.execute(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='meter_type'").fetchall()}
    assert wire_meter == db_meter
