"""P5 — imports: job lifecycle, upload -> worker (identical validators/dedup),
NDJSON error report, backfill-horizon enforcement, downstream indistinguishability.
"""

from __future__ import annotations

import json

from conftest import auth
from truefigure_server.api.routes import imports
from truefigure_server.platform.storage import storage

TS = "2026-07-01T10:00:00Z"


async def _dep(client, key) -> str:
    r = await client.post("/v1/deployments", json={"name": "D", "type": "saas_tool"}, headers=auth(key))
    return r.json()["data"]["deployment_id"]


def _line(dep, wi, user="u_1", ts=TS, extra=None):
    p = {"user_ref": user, "timestamp": ts, "work_item_id": wi, "action_type": "suggestion_accepted"}
    if extra:
        p.update(extra)
    return json.dumps({"schema_version": "1.0", "deployment_id": dep, "event_type": "activity",
                       "origin": "customer_system", "payload": p})


async def _create_import(client, key, kind="publication", expected=0, source=None) -> tuple[str, str]:
    body = {"kind": kind, "expected_events": expected}
    if source:
        body["source_ref"] = source
    r = await client.post("/v1/imports", json=body, headers=auth(key))
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    return data["import_id"], data["upload_url"]


def _upload(import_ref: str, workspace_id: int, ndjson: str) -> None:
    # Simulate the client PUTting NDJSON to the signed upload URL.
    storage.get_storage().put("import-uploads", f"{workspace_id}/{import_ref}.ndjson", ndjson.encode())


def _ws_id(conn, import_ref: str) -> int:
    return conn.execute("SELECT workspace_id FROM import_jobs WHERE import_ref=%s", (import_ref,)).fetchone()["workspace_id"]


async def test_import_lifecycle_and_counts(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    imp, upload_url = await _create_import(client, key, kind="backfill", expected=3, source="pub")
    assert upload_url and imp.startswith("imp_")
    # queued until upload+worker
    st0 = await client.get(f"/v1/imports/{imp}", headers=auth(key))
    assert st0.json()["data"]["status"] == "queued"

    ndjson = "\n".join([_line(dep, "W1"), _line(dep, "W1"),  # dup of first
                        _line(dep, "W2")])
    _upload(imp, _ws_id(conn, imp), ndjson)
    imports.run_import(imp)

    st = await client.get(f"/v1/imports/{imp}", headers=auth(key))
    d = st.json()["data"]
    assert d["status"] == "completed"
    assert d["counts"] == {"received": 3, "accepted": 2, "duplicates": 1, "rejected": 0}
    assert d["error_report_url"] is None
    # import_type stored (wire kind->import_type)
    row = conn.execute("SELECT import_type FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()
    assert row["import_type"] == "backfill"


async def test_import_rejects_produce_error_report(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    imp, _ = await _create_import(client, key)
    ndjson = "\n".join([
        _line(dep, "W1"),                                   # ok
        _line(dep, "W2", extra={"content": "secret"}),      # TF-EVT-006
        "{not valid json",                                  # parse error
        _line("dep_ghost", "W3"),                           # TF-EVT-002
    ])
    _upload(imp, _ws_id(conn, imp), ndjson)
    imports.run_import(imp)
    st = await client.get(f"/v1/imports/{imp}", headers=auth(key))
    d = st.json()["data"]
    assert d["status"] == "completed_with_rejects"
    assert d["counts"] == {"received": 4, "accepted": 1, "duplicates": 0, "rejected": 3}
    assert d["error_report_url"] is not None
    # error report NDJSON is retrievable and structured
    path = conn.execute("SELECT error_report_path FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()["error_report_path"]
    report = storage.get_storage().get("import-error-reports", path).decode()
    lines = [json.loads(x) for x in report.splitlines()]
    codes = {ln["error"]["code"] for ln in lines}
    assert codes == {"TF-EVT-006", "TF-EVT-003", "TF-EVT-002"}


async def test_import_backfill_horizon_evt009(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    imp, _ = await _create_import(client, key)
    ndjson = _line(dep, "W1", ts="2000-01-01T00:00:00Z")  # older than 400d horizon
    _upload(imp, _ws_id(conn, imp), ndjson)
    imports.run_import(imp)
    path = conn.execute("SELECT error_report_path FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()["error_report_path"]
    report = storage.get_storage().get("import-error-reports", path).decode()
    assert json.loads(report.splitlines()[0])["error"]["code"] == "TF-EVT-009"


async def test_import_events_indistinguishable_from_sync(client, conn, tenant) -> None:
    key = tenant["key"]
    dep = await _dep(client, key)
    imp, _ = await _create_import(client, key)
    _upload(imp, _ws_id(conn, imp), _line(dep, "W1"))
    imports.run_import(imp)
    # The imported event is a normal accepted event, inspectable via /status with the same key.
    ev = conn.execute("SELECT event_key, pipeline_status FROM events").fetchone()
    st = await client.get(f"/v1/events/{ev['event_key']}/status", headers=auth(key))
    assert st.json()["data"]["status"] == "accepted"


async def test_import_status_not_found(client, tenant) -> None:
    r = await client.get("/v1/imports/imp_nope", headers=auth(tenant["key"]))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"


async def test_import_unknown_kind_rejected(client, tenant) -> None:
    r = await client.post("/v1/imports", json={"kind": "not_a_kind"}, headers=auth(tenant["key"]))
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "TF-CFG-007"


async def test_run_import_missing_upload_fails(client, conn, tenant) -> None:
    import pytest

    from truefigure_server.errors import TFError
    imp, _ = await _create_import(client, tenant["key"])
    # No upload performed -> worker marks job failed and raises.
    with pytest.raises(TFError):
        imports.run_import(imp)
    row = conn.execute("SELECT import_status FROM import_jobs WHERE import_ref=%s", (imp,)).fetchone()
    assert row["import_status"] == "failed"
