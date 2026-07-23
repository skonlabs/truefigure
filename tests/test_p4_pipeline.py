"""P4 — pipeline: identity resolution, work-item join, exclusion (BR-011), and
the three daily aggregates with EXACT hand-computed expectations.
"""

from __future__ import annotations

from conftest import auth
from truefigure_server.domain.policies import pipeline

TS1 = "2026-07-01T10:00:00Z"
TS2 = "2026-07-01T11:00:00Z"
TS3 = "2026-07-02T10:00:00Z"


async def _dep(client, key) -> str:
    r = await client.post("/v1/deployments", json={"name": "D", "type": "saas_tool"}, headers=auth(key))
    return r.json()["data"]["deployment_id"]


async def _post(client, key, events, source=None):
    headers = auth(key)
    if source:
        headers["X-TrueFigure-Source"] = source
    return await client.post("/v1/events:batch", json={"events": events}, headers=headers)


def _act(dep, user, wi, ts, action="suggestion_accepted"):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity",
            "origin": "customer_system",
            "payload": {"user_ref": user, "timestamp": ts, "work_item_id": wi, "action_type": action}}


async def test_resolution_and_activity_aggregate(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await client.post("/v1/roster:batch",
                      json={"users": [{"user_ref": "u_1", "kind": "person",
                                       "identifiers": [{"namespace": "raw", "value": "u_1"}]}]},
                      headers=auth(key))
    # 3 activity events by u_1: two on day1, one on day2, all same work item.
    await _post(client, key, [_act(dep, "u_1", "W1", TS1), _act(dep, "u_1", "W1", TS2, "draft_generated"),
                              _act(dep, "u_1", "W1", TS3)])
    pipeline.run_pipeline()

    # events resolved+joined
    statuses = [r["pipeline_status"] for r in conn.execute(
        "SELECT pipeline_status FROM events ORDER BY occurred_at").fetchall()]
    assert statuses == ["joined", "joined", "joined"]
    uid = conn.execute("SELECT id FROM users WHERE user_ref='u_1'").fetchone()["id"]
    resolved = conn.execute("SELECT count(*) AS n FROM events WHERE resolved_user_id=%s", (uid,)).fetchone()["n"]
    assert resolved == 3

    # user_activity_daily: day1=2, day2=1
    rows = conn.execute(
        "SELECT activity_date, event_count FROM user_activity_daily ORDER BY activity_date").fetchall()
    assert [(str(r["activity_date"]), r["event_count"]) for r in rows] == [("2026-07-01", 2), ("2026-07-02", 1)]

    # work_items lazily created, one row
    assert conn.execute("SELECT count(*) AS n FROM work_items").fetchone()["n"] == 1


async def test_shared_account_excluded_br011(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await client.post("/v1/roster:batch",
                      json={"users": [{"user_ref": "shared_bot", "kind": "shared"}]}, headers=auth(key))
    await _post(client, key, [_act(dep, "shared_bot", "W1", TS1)])
    pipeline.run_pipeline()
    row = conn.execute("SELECT pipeline_status, exclusion_reason FROM events").fetchone()
    assert row["pipeline_status"] == "excluded" and row["exclusion_reason"] == "shared_account"
    # excluded activity does NOT count toward user_activity_daily
    assert conn.execute("SELECT count(*) AS n FROM user_activity_daily").fetchone()["n"] == 0
    # but it IS identity-bearing and counted accepted in ingestion stats (resolved_count stays 0)
    st = conn.execute("SELECT accepted_count, identity_bearing_count, resolved_count FROM ingestion_stats_daily").fetchone()
    assert (st["accepted_count"], st["identity_bearing_count"], st["resolved_count"]) == (1, 1, 0)


async def test_service_and_bot_exclusion_reasons(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await client.post("/v1/roster:batch", json={"users": [
        {"user_ref": "svc", "kind": "service"}, {"user_ref": "botty", "kind": "bot"}]}, headers=auth(key))
    await _post(client, key, [_act(dep, "svc", "W1", TS1), _act(dep, "botty", "W2", TS2)])
    pipeline.run_pipeline()
    rows = conn.execute(
        "SELECT u.user_ref, e.exclusion_reason FROM events e JOIN users u ON u.id=e.resolved_user_id").fetchall()
    reasons = {r["user_ref"]: r["exclusion_reason"] for r in rows}
    assert reasons == {"svc": "service_account", "botty": "bot_account"}


async def test_unresolved_stays_reresolvable(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    # No roster entry for u_ghost -> unresolved.
    await _post(client, key, [_act(dep, "u_ghost", "W1", TS1)])
    pipeline.run_pipeline()
    row = conn.execute("SELECT pipeline_status, resolved_user_id FROM events").fetchone()
    assert row["resolved_user_id"] is None
    assert row["pipeline_status"] in ("resolved", "joined")  # advanced, but unresolved identity
    assert conn.execute("SELECT count(*) AS n FROM user_activity_daily").fetchone()["n"] == 0
    st = conn.execute("SELECT identity_bearing_count, resolved_count FROM ingestion_stats_daily").fetchone()
    assert (st["identity_bearing_count"], st["resolved_count"]) == (1, 0)


async def test_meter_counter_sum_and_gauge_last(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    meters = [
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "tokens_out", "quantity": 100, "timestamp": TS1}},
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "tokens_out", "quantity": 250, "timestamp": TS2}},
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "seats_active", "quantity": 40, "timestamp": TS1}},
        {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
         "payload": {"meter": "seats_active", "quantity": 55, "timestamp": TS2}},
    ]
    await _post(client, key, meters)
    pipeline.run_pipeline()
    counter = conn.execute(
        "SELECT quantity_sum FROM meter_usage_daily WHERE meter_type='tokens_out'").fetchone()
    gauge = conn.execute(
        "SELECT gauge_last, quantity_sum FROM meter_usage_daily WHERE meter_type='seats_active'").fetchone()
    assert float(counter["quantity_sum"]) == 350.0        # COUNTER: 100 + 250
    assert float(gauge["gauge_last"]) == 55.0             # GAUGE: last reading of the day
    assert float(gauge["quantity_sum"]) == 0.0


async def test_lifecycle_join_sets_size_and_category(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    life = {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle",
            "origin": "customer_system",
            "payload": {"work_item_id": "CLM-1", "event": "created", "timestamp": TS1,
                        "category": "auto_glass", "size_band": "s"}}
    await _post(client, key, [life])
    pipeline.run_pipeline()
    wi = conn.execute("SELECT size_type, category FROM work_items WHERE work_item_ref='CLM-1'").fetchone()
    assert wi["size_type"] == "s" and wi["category"] == "auto_glass"  # size_band -> size_type


async def test_source_ref_breakdown_in_ingestion_stats(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await _post(client, key, [_act(dep, "u_x", "W1", TS1)], source="feed_a")
    await _post(client, key, [_act(dep, "u_y", "W2", TS2)], source="feed_b")
    pipeline.run_pipeline()
    rows = {r["source_ref"]: r["accepted_count"] for r in conn.execute(
        "SELECT source_ref, accepted_count FROM ingestion_stats_daily ORDER BY source_ref").fetchall()}
    assert rows == {"feed_a": 1, "feed_b": 1}


async def test_idempotent_reprocess_no_double_count(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    await client.post("/v1/roster:batch", json={"users": [{"user_ref": "u_1", "kind": "person"}]},
                      headers=auth(key))
    await _post(client, key, [_act(dep, "u_1", "W1", TS1)])
    pipeline.run_pipeline()
    pipeline.run_pipeline()  # second run: no accepted events left, no double counting
    n = conn.execute("SELECT event_count FROM user_activity_daily").fetchone()["event_count"]
    assert n == 1
