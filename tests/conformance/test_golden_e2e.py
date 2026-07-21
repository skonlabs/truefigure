"""GOLDEN E2E — the Integration Playbook, end to end, deterministic.

Provision org_acme via the ops CLI -> declare id-namespaces / license / parameters
-> roster sync (incl. a service account) -> vendor + customer event streams
-> backfill import -> pipeline -> engine run -> assert final figures, grades,
lineage references, and the issued report. Fixed timestamps + seeded refs.
"""

from __future__ import annotations

import json

from _helpers import activity, auth, cost_meter, lifecycle, make_deployment, post_events, provision

from truefigure_sdk.domain import engine
from truefigure_sdk.api.routes import imports
from truefigure_sdk.domain.policies import pipeline
from truefigure_sdk.platform.storage import storage

PERIOD = "2026-07"


async def test_golden_playbook(client, conn, ops) -> None:
    # 1. Provision tenant + finance key (Console substitute).
    key = provision(ops)

    # 2. Register the measured deployment (agent, so containment applies).
    dep = await make_deployment(client, key, dtype="agent", name="Claims Agent", external_ref="ext_claims")

    # 3. Declare id-namespace, license, parameters.
    await client.post("/v1/id-namespaces",
                      json={"field": "user_ref", "namespace": "okta", "deployment_id": dep,
                            "source_ref": "vendor_feed"}, headers=auth(key))
    await client.put(f"/v1/deployments/{dep}/license",
                     json={"seats_paid": 5, "price_per_seat": 120.0, "period_unit": "year",
                           "valid_from": "2026-01-01"}, headers=auth(key))
    await client.post("/v1/parameters",
                      json={"effective_from": "2026-07-01",
                            "payload": {"unit_prices": {"tokens_out": 0.001},
                                        "labor_rates": {"default": 60}}}, headers=auth(key))

    # 4. Roster sync: two people + one service account (G6). Declare the service account.
    await client.post("/v1/roster:batch", json={"users": [
        {"user_ref": "adj_1", "kind": "person", "role": "adjuster",
         "identifiers": [{"namespace": "okta", "value": "okta-adj1"}]},
        {"user_ref": "adj_2", "kind": "person", "role": "adjuster",
         "identifiers": [{"namespace": "okta", "value": "okta-adj2"}]},
        {"user_ref": "svc_agent", "kind": "service"},
    ]}, headers=auth(key))
    await client.patch(f"/v1/deployments/{dep}", json={"name": "Claims Agent v2"}, headers=auth(key))
    # link the service account (re-register with service_user_refs via a fresh deployment update path):
    conn.execute(
        "INSERT INTO deployment_service_users (workspace_id, deployment_id, user_id, created_by, updated_by) "
        "SELECT d.workspace_id, d.id, u.id, 1, 1 FROM deployments d, users u "
        "WHERE d.deployment_ref=%s AND u.user_ref='svc_agent'", (dep,))

    # 5. Customer-origin + vendor-origin streams: lifecycle (created/completed) + activity + cost.
    events = [
        # assisted items handled fast (1h); the unassisted baseline CLM-3 is slow (5h)
        lifecycle(dep, "CLM-1", "created", "2026-07-01T00:00:00Z", category="auto_glass", size_band="s"),
        lifecycle(dep, "CLM-1", "completed", "2026-07-01T01:00:00Z"),
        lifecycle(dep, "CLM-2", "created", "2026-07-02T00:00:00Z", category="auto_glass", size_band="s"),
        lifecycle(dep, "CLM-2", "completed", "2026-07-02T01:00:00Z"),
        lifecycle(dep, "CLM-3", "created", "2026-07-05T00:00:00Z", category="auto_glass", size_band="m"),
        lifecycle(dep, "CLM-3", "completed", "2026-07-05T05:00:00Z"),  # unassisted baseline (no activity)
        lifecycle(dep, "CLM-9", "created", "2026-07-03T00:00:00Z", category="auto_glass", size_band="m"),
        activity(dep, "okta-adj1", "CLM-1", "2026-07-01T00:30:00Z", action="case_handled_autonomous"),
        activity(dep, "okta-adj2", "CLM-2", "2026-07-02T00:30:00Z", action="case_handled_autonomous"),
        activity(dep, "okta-adj1", "CLM-9", "2026-07-03T00:30:00Z", action="escalated_to_human"),
        # G6: a customer-origin action by the measured vendor's service account corroborates
        # the autonomous-action claim (does not change the containment ratio).
        activity(dep, "svc_agent", "CLM-1", "2026-07-01T02:00:00Z", action="lookup_performed",
                 origin="customer_system"),
        cost_meter(dep, "tokens_out", 100000, "2026-07-01T00:00:00Z"),
    ]
    r = await post_events(client, key, events, source="vendor_feed")
    assert all(x["status"] in ("accepted", "duplicate") for x in r.json()["data"]["results"])

    # 6. Backfill import (same contract, second transport).
    imp = (await client.post("/v1/imports", json={"kind": "backfill", "source_ref": "vendor_feed"},
                             headers=auth(key))).json()["data"]["import_id"]
    ws_id = conn.execute("SELECT workspace_id FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()["workspace_id"]
    ndjson = json.dumps(cost_meter(dep, "tokens_out", 50000, "2026-07-04T00:00:00Z"))
    storage.get_storage().put("import-uploads", f"{ws_id}/{imp}.ndjson", ndjson.encode())
    counts = imports.run_import(imp)
    assert counts["accepted"] == 1

    # 7. Pipeline (resolution, join, aggregates).
    pipeline.run_pipeline()
    did = conn.execute("SELECT id FROM deployments WHERE deployment_ref=%s", (dep,)).fetchone()["id"]

    # 8. Engine run + report issuance.
    engine.compute_deployment(ws_id, int(did), PERIOD)
    rep_ref = engine.issue_report(ws_id, int(did), PERIOD)

    # ---- ASSERTIONS: final figures, grades, lineage, report ----
    # cost: (100000 + 50000) * 0.001 = 150.0, grade measured, param v1 stamped
    cost = (await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}", headers=auth(key))).json()["data"]
    assert cost["status"] == "computed" and cost["value"] == 150.0
    assert cost["grade"] == "measured" and cost["parameter_set_version"] == 1

    # containment: 2 autonomous / (2 + 1 escalated) = 0.6667; verified via G6 corroboration
    cont = (await client.get(f"/v1/figures/{dep}/fig_containment_{PERIOD}", headers=auth(key))).json()["data"]
    assert round(cont["value"], 2) == 0.67 and cont["grade"] == "verified"

    # time savings: assisted mean 1h, unassisted 5h; effect 4h -> computed, capacity
    time = (await client.get(f"/v1/figures/{dep}/fig_time_{PERIOD}", headers=auth(key))).json()["data"]
    assert time["status"] == "computed" and time["value_class"] == "capacity"

    # lineage references (never embeds inputs)
    lin = (await client.get(f"/v1/figures/{dep}/fig_cost_{PERIOD}/lineage", headers=auth(key))).json()["data"]
    assert lin["parameter_set_version"] == 1 and "priced_meters" in lin["lineage"]

    # report binds the exact figure versions + artifact
    rep = (await client.get(f"/v1/reports/{dep}/{rep_ref}", headers=auth(key))).json()["data"]
    assert rep["period"] == PERIOD and rep["artifact_url"] is not None
    bound = {f["figure_id"] for f in rep["figures"]}
    assert f"fig_cost_{PERIOD}" in bound and f"fig_containment_{PERIOD}" in bound

    # live surfaces reflect the same data
    health = (await client.get(f"/v1/live/health/{dep}", headers=auth(key))).json()["data"]
    # 4 identity-bearing activity events; 3 resolve to people, the service account is
    # resolve-then-excluded (BR-011) so it does not count as resolved -> 3/4.
    assert health["identity_resolution_rate"] == 0.75
    cost_live = (await client.get(f"/v1/live/cost/{dep}?period={PERIOD}", headers=auth(key))).json()["data"]
    assert cost_live["period_to_date_usd"] == 150.0
