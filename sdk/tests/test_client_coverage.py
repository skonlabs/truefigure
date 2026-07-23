"""Exhaustive coverage of the SDK: every endpoint binding + optional branch, the
JSON logger, the real urllib transport, pagination, upload/provision variants,
and the remaining error/builder branches. Target: 100% of truefigure/*."""
import email.message
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from truefigure import (
    BatchResult,
    ErrorObject,
    RateLimited,
    TrueFigureClient,
    TrueFigureError,
    configure_logging,
    events,
    webhooks,
)
from truefigure.client import _JsonFormatter


def router(default=None):
    """Records calls; returns a generic ok envelope (or per-path scripted responses)."""
    calls = []
    scripts = {}

    def transport(method, url, headers, body):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        calls.append({"method": method, "path": path, "url": url, "headers": headers, "body": body})
        seq = scripts.get(f"{method} {path}")
        if seq:
            st, env = seq.pop(0)
            return st, {}, env
        data = default if default is not None else {"ok": True}
        return 200, {}, {"data": data, "meta": {"request_id": "r"}, "errors": []}

    transport.calls = calls
    transport.scripts = scripts
    return transport


def paths(t):
    return [f"{c['method']} {c['path']}" for c in t.calls]


# ---------------- every read/config endpoint binding + optional branches ------

def test_every_endpoint_binding():
    t = router()
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1", source_ref="src")
    # read plane
    c.event_status("ek1")
    c.live_usage(); c.live_cost(); c.integration_health()
    c.figures(period="2026-Q2"); c.figures()
    c.get_figure("fig1"); c.figure_lineage("fig1")
    c.reports(period="2026-Q2"); c.reports(); c.get_report("rep1")
    c.alerts(deployment_id="dep-1", status="open"); c.alerts(); c.ack_alert("al1")
    # config plane — with all optionals set (truthy branches)
    c.register_deployment("D", "saas_tool", external_ref="x", planned_rollout_at="2026-01-01", service_user_refs=["s"])
    c.register_deployment("D2", "saas_tool")  # no optionals
    c.create_parameter_version({"labor_rates": {"default": 1}}, "2026-01-01")
    c.whoami()
    c.declare_change_event("system_cutover", "2026-10-26T00:00:00Z", "one_sided", "d", deployment_refs=["dep-1"], supersedes="chg0")
    c.declare_change_event("process_change", "2026-10-26T00:00:00Z", "two_sided", "d")
    c.create_import("backfill", expected_events=5); c.import_status("imp1")
    c.list_deployments(); c.get_deployment(); c.update_deployment(name="N", planned_rollout_at="2026-02-01")
    c.upsert_roster([{"user_ref": "u"}])
    c.list_roster(status="active", kind="person"); c.list_roster()
    c.declare_license(10, "2026-01-01", price_per_seat=5.0, licensed_user_refs=["u"])
    c.declare_license(10, "2026-01-01")
    c.get_license()
    c.register_id_namespace("user_ref", "ns", deployment_id="dep-1", format_regex="^x$", description="d")
    c.register_id_namespace("user_ref", "ns")
    c.list_id_namespaces(); c.list_parameters(); c.get_parameter_version(1)
    c.register_label_schema("ls", "n", ["a", "b"], applies_to_category="c"); c.register_label_schema("ls2", "n", ["a"])
    c.list_label_schemas()
    c.create_webhook("https://h", ["figure.updated"], "sec"); c.list_webhooks()
    c.delete_webhook("wh1"); c.webhook_deliveries("wh1")
    c.create_mapping_contract("src", "activity", 1, {"a": "b"}, notes="n")
    c.create_mapping_contract("src", "activity", 1, {"a": "b"})
    c.list_mapping_contracts(); c.list_change_events()
    ps = paths(t)
    # spot-check representative verbs/paths
    assert "GET /v1/live/usage/dep-1" in ps
    assert "PATCH /v1/deployments/dep-1" in ps
    assert "PUT /v1/deployments/dep-1/license" in ps
    assert "DELETE /v1/webhooks/wh1" in ps
    assert "POST /v1/alerts/al1:ack" in ps
    # source_ref header attached on every call
    assert t.calls[0]["headers"]["X-TrueFigure-Source"] == "src"


def test_create_import_includes_source_ref():
    t = router({"import_id": "imp1", "status": "queued", "upload_url": "u"})
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1", source_ref="aims")
    c.create_import("backfill")
    assert t.calls[0]["body"]["source_ref"] == "aims"


# ---------------- pagination single page + iterators --------------------------

def test_paginate_single_page_and_iterators():
    t = router()
    t.scripts["GET /v1/deployments"] = [(200, {"data": {"results": [{"id": 1}]}, "meta": {"request_id": "r"}, "errors": []})]
    t.scripts["GET /v1/change-events"] = [(200, {"data": {"results": []}, "meta": {"request_id": "r"}, "errors": []})]
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    assert [d["id"] for d in c.iter_deployments()] == [1]
    assert list(c.iter_change_events()) == []


def test_paginate_handles_list_data_shape():
    t = router()
    t.scripts["GET /v1/things"] = [(200, {"data": [{"id": 9}], "meta": {}, "errors": []})]
    c = TrueFigureClient("key", transport=t)
    assert [x["id"] for x in c.paginate("/v1/things")] == [9]


# ---------------- async import: no-wait + success poll ------------------------

def test_upload_import_no_wait_returns_created():
    up = {}
    c = TrueFigureClient("key", transport=router({"import_id": "imp1", "status": "queued", "upload_url": "https://u"}),
                         deployment_id="dep-1", uploader=lambda url, data: up.setdefault("n", len(data)) or 200)
    created = c.upload_import([events.activity("dep-1", user_ref="u", timestamp="2026-07-01T00:00:00Z",
                                               work_item_id="w", action_type="draft_generated")])
    assert created["import_id"] == "imp1" and up["n"] > 0


def test_wait_for_import_success(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    t = router()
    t.scripts["GET /v1/imports/imp1"] = [
        (200, {"data": {"import_id": "imp1", "status": "processing"}, "meta": {"request_id": "r"}, "errors": []}),
        (200, {"data": {"import_id": "imp1", "status": "completed", "accepted": 1}, "meta": {"request_id": "r"}, "errors": []}),
    ]
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    assert c.wait_for_import("imp1", poll_interval=0)["status"] == "completed"


# ---------------- provision without license/params ----------------------------

def test_provision_deployment_only():
    t = router({"deployment_id": "dep_9"})
    c = TrueFigureClient("key", transport=t)
    dep = c.provision(name="A", type="saas_tool")
    assert dep["deployment_id"] == "dep_9" and paths(t) == ["POST /v1/deployments"]


# ---------------- real urllib transport (_http) -------------------------------

def test_http_success(monkeypatch):
    class FakeResp:
        status = 200
        headers = {"x-request-id": "r"}
        def read(self): return b'{"data":{"ok":true},"meta":{"request_id":"r"},"errors":[]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    c = TrueFigureClient("key", base_url="https://api.example")
    status, headers, env = c._http("POST", "https://api.example/v1/whoami", {}, {"a": 1})
    assert status == 200 and env["data"]["ok"] is True


def test_http_error_with_json_body(monkeypatch):
    def boom(req, timeout=None):
        body = io.BytesIO(b'{"data":null,"meta":{"request_id":"r9"},"errors":[{"code":"TF-EVT-001","message":"x","retryable":false}]}')
        raise urllib.error.HTTPError("https://x", 400, "bad", email.message.Message(), body)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    c = TrueFigureClient("key")
    status, _, env = c._http("GET", "https://x", {}, {})
    assert status == 400 and env["errors"][0]["code"] == "TF-EVT-001"


def test_http_error_non_json_body(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("https://x", 502, "bad gw", email.message.Message(), io.BytesIO(b"not json"))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    c = TrueFigureClient("key")
    status, _, env = c._http("GET", "https://x", {}, {})
    assert status == 502 and env["errors"][0]["code"] == "TF-SRV-001" and env["errors"][0]["retryable"] is True


def test_default_http_transport_is_wired():
    # constructing without a transport wires self._http (covers the default branch)
    c = TrueFigureClient("key")
    assert c._transport == c._http


# ---------------- 429 -> RateLimited, and empty-error fallback ----------------

def test_rate_limited_raised_on_429():
    env = {"data": None, "meta": {"request_id": "r"}, "errors": [
        {"code": "TF-RATE-001", "message": "slow", "retryable": False, "request_id": "r"}]}
    t = lambda m, u, h, b: (429, {}, env)
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1", max_retries=0)
    with pytest.raises(RateLimited):
        c.whoami()


def test_error_fallback_when_no_error_objects():
    t = lambda m, u, h, b: (500, {}, {"data": None, "meta": {}, "errors": []})
    c = TrueFigureClient("key", transport=t, max_retries=0)
    with pytest.raises(TrueFigureError) as e:
        c.whoami()
    assert e.value.errors[0].code == "TF-SRV-001"


def test_retry_after_from_response_header(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    seq = [(503, {"retry-after": "3"}, {"data": None, "meta": {}, "errors": []}),
           (200, {}, {"data": {"ok": 1}, "meta": {"request_id": "r"}, "errors": []})]
    t = lambda m, u, h, b: seq.pop(0)
    c = TrueFigureClient("key", transport=t)
    assert c.whoami()["ok"] == 1 and sleeps == [3]


# ---------------- logging, BatchResult repr ----------------------------------

def test_json_formatter_and_configure_logging():
    configure_logging(logging.INFO)  # covers configure_logging
    rec = logging.LogRecord("truefigure", logging.INFO, __file__, 1, "hello", None, None)
    rec.request_id = "r1"; rec.code = "TF-X"; rec.batch_size = 3
    out = json.loads(_JsonFormatter().format(rec))
    assert out["msg"] == "hello" and out["request_id"] == "r1" and out["code"] == "TF-X" and out["batch_size"] == 3


def test_batchresult_repr():
    br = BatchResult([{"status": "accepted", "event_key": "k"},
                      {"status": "duplicate"}, {"status": "rejected", "error": {"code": "X"}}], "req")
    r = repr(br)
    assert "accepted=1" in r and "duplicates=1" in r and "rejected=1" in r


def test_flush_logs_rejected(caplog):
    t = router()
    t.scripts["POST /v1/events:batch"] = [(200, {"data": {"results": [
        {"index": 0, "status": "rejected", "event_key": "k0", "error": {"code": "TF-EVT-006"}}]},
        "meta": {"request_id": "r"}, "errors": []})]
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    c.track_activity(user_ref="u", timestamp="2026-07-01T00:00:00Z", work_item_id="w", action_type="draft_generated")
    br = c.flush()
    assert len(br.rejected) == 1


# ---------------- events builders: all five families --------------------------

def test_all_event_builders_shape_correctly():
    a = events.activity("d", user_ref="u", timestamp="t", work_item_id="w", action_type="x")
    ll = events.lifecycle("d", work_item_id="w", event="completed", timestamp="t", status_to="DONE")
    cm = events.cost_meter("d", meter="api_calls", quantity=2, timestamp="t", meter_ref="m")
    q = events.quality_signal("d", work_item_id="w", signal="error_found", timestamp="t", error_class="billing")
    rv = events.revenue_signal("d", work_item_id="w", revenue_ref="DEAL", timestamp="t")
    assert a["event_type"] == "activity" and ll["payload"]["status_to"] == "DONE"
    assert cm["payload"]["meter_ref"] == "m" and q["payload"]["error_class"] == "billing"
    assert rv["payload"]["revenue_ref"] == "DEAL"


# ---------------- errors.py: RateLimited.retry_after --------------------------

def test_rate_limited_retry_after_property():
    with_ra = RateLimited([ErrorObject("TF-RATE-001", "x", True, "r", retry_after=7)], "r")
    without = RateLimited([ErrorObject("TF-RATE-001", "x", True, "r")], "r")
    assert with_ra.retry_after == 7 and without.retry_after == 1


# ---------------- webhooks.py: bad timestamp branch ---------------------------

def test_verify_signature_bad_timestamp_returns_false():
    assert webhooks.verify_signature("s", "not-an-int", b"body", "v1=deadbeef") is False


# ---------------- remaining track_* variants + enqueue branches ---------------

def test_all_track_methods_and_generic_track():
    t = router()
    t.scripts["POST /v1/events:batch"] = [(200, {"data": {"results": []}, "meta": {"request_id": "r"}, "errors": []})]
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    assert c.track_lifecycle(work_item_id="w", event="completed", timestamp="2026-07-01T00:00:00Z") == 1
    assert c.track_quality(work_item_id="w", signal="error_found", timestamp="2026-07-01T00:00:00Z") == 2
    assert c.track_revenue(work_item_id="w", revenue_ref="DEAL", timestamp="2026-07-01T00:00:00Z") == 3
    assert c.track({"schema_version": "1.0", "deployment_id": "dep-1", "event_type": "activity",
                    "origin": "customer_system", "payload": {}}) == 4


def test_dep_required_raises_without_default():
    c = TrueFigureClient("key")  # no deployment_id
    with pytest.raises(ValueError):
        c.live_usage()


def test_enqueue_autoflushes_at_max_batch():
    t = router()
    t.scripts["POST /v1/events:batch"] = [(200, {"data": {"results": []}, "meta": {"request_id": "r"}, "errors": []})]
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    c.MAX_BATCH = 2  # instance override to trigger the auto-flush branch
    c.track_activity(user_ref="u", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
    c.track_activity(user_ref="u", timestamp="2026-07-01T00:00:00Z", work_item_id="w2", action_type="draft_generated")
    assert any(p == "POST /v1/events:batch" for p in paths(t))  # auto-flushed


def test_flush_empty_batch_returns_empty_result():
    c = TrueFigureClient("key", transport=router(), deployment_id="dep-1")
    br = c.flush()
    assert br.results == [] and br.request_id == ""


def test_flush_without_buffer_reraises_and_restores_pending():
    err = {"data": None, "meta": {"request_id": "r"}, "errors": [
        {"code": "TF-EVT-002", "message": "x", "retryable": False, "request_id": "r"}]}
    c = TrueFigureClient("key", transport=lambda m, u, h, b: (404, {}, err), deployment_id="dep-1", max_retries=0)
    c.track_activity(user_ref="u", timestamp="2026-07-01T00:00:00Z", work_item_id="w", action_type="draft_generated")
    with pytest.raises(TrueFigureError):
        c.flush()
    assert len(c._pending) == 1  # chunk restored to pending for a later retry


def test_upload_uses_real_put_when_no_uploader(monkeypatch):
    seen = {}
    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        seen["method"] = req.get_method(); seen["data"] = req.data; seen["ctype"] = req.headers.get("Content-type")
        return FakeResp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = TrueFigureClient("key")
    assert c._upload("https://store/put", b"a\nb") == 200
    assert seen["method"] == "PUT" and seen["data"] == b"a\nb"
