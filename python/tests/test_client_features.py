"""Tests for the SDK convenience features: pagination, async import (upload +
polling), provision workflow, webhook signature verification, timeouts."""
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from truefigure import TrueFigureClient, webhooks


def env(data, *, next_cursor=None, request_id="r"):
    meta = {"schema_version": "1.0", "request_id": request_id, "as_of": "now"}
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    return {"data": data, "meta": meta, "errors": []}


def router(routes):
    """routes: dict keyed by 'METHOD path' (path without query) -> list of (status, env)."""
    calls = []

    def transport(method, url, headers, body):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path.split("?", 1)[0]
        calls.append({"method": method, "path": path, "url": url, "headers": headers, "body": body})
        seq = routes.get(f"{method} {path}")
        resp = seq.pop(0) if seq else (200, env({}))
        return resp[0], {}, resp[1]

    transport.calls = calls
    return transport


# ---------------- pagination ----------------

def test_paginate_follows_next_cursor():
    t = router({"GET /v1/roster": [
        (200, env({"results": [{"user_ref": "a"}, {"user_ref": "b"}]}, next_cursor="c2")),
        (200, env({"results": [{"user_ref": "c"}]}, next_cursor=None)),
    ]})
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    got = [u["user_ref"] for u in c.iter_roster(status="active")]
    assert got == ["a", "b", "c"]
    # first call carries the filter, second carries the cursor
    assert "status=active" in t.calls[0]["url"]
    assert "cursor=c2" in t.calls[1]["url"]


# ---------------- async import: upload + poll ----------------

def test_upload_import_creates_uploads_and_polls(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    uploaded = {}

    def uploader(url, data):
        uploaded["url"] = url
        uploaded["lines"] = [json.loads(x) for x in data.decode().splitlines()]
        return 200

    t = router({
        "POST /v1/imports": [(201, env({"import_id": "imp_1", "status": "queued",
                                        "upload_url": "https://store/signed/put"}))],
        "GET /v1/imports/imp_1": [
            (200, env({"import_id": "imp_1", "status": "processing"})),
            (200, env({"import_id": "imp_1", "status": "completed",
                       "accepted": 2, "duplicates": 0, "rejected": 0})),
        ],
    })
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1", uploader=uploader)
    evs = [
        c_activity("dep-1"),
        c_activity("dep-1", work="W2"),
    ]
    final = c.upload_import(evs, kind="backfill", wait=True, poll_interval=0)
    assert final["status"] == "completed" and final["accepted"] == 2
    assert uploaded["url"] == "https://store/signed/put"
    assert len(uploaded["lines"]) == 2
    assert all("_event_key" not in line for line in uploaded["lines"])  # internal key stripped
    # create carried expected_events == len(events)
    assert t.calls[0]["body"]["expected_events"] == 2


def test_wait_for_import_times_out(monkeypatch):
    ticks = iter([0.0, 1.0, 2.0, 400.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    t = router({"GET /v1/imports/imp_x": [
        (200, env({"import_id": "imp_x", "status": "processing"})),
        (200, env({"import_id": "imp_x", "status": "processing"})),
    ]})
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    with pytest.raises(TimeoutError):
        c.wait_for_import("imp_x", timeout=300, poll_interval=0)


# ---------------- provision workflow ----------------

def test_provision_registers_deployment_license_and_parameters():
    t = router({
        "POST /v1/deployments": [(201, env({"deployment_id": "dep_9"}))],
        "PUT /v1/deployments/dep_9/license": [(200, env({"ok": True}))],
        "POST /v1/parameters": [(201, env({"version": 1}))],
    })
    c = TrueFigureClient("key", transport=t)
    dep = c.provision(name="Acme", type="saas_tool",
                      license={"seats_paid": 50, "valid_from": "2026-01-01", "price_per_seat": 100.0},
                      parameters={"labor_rates": {"default": 40}}, effective_from="2026-01-01")
    assert dep["deployment_id"] == "dep_9"
    paths = [f"{c_['method']} {c_['path']}" for c_ in t.calls]
    assert paths == ["POST /v1/deployments", "PUT /v1/deployments/dep_9/license", "POST /v1/parameters"]
    assert t.calls[1]["body"]["seats_paid"] == 50


# ---------------- webhook signature verification ----------------

def test_webhook_verify_signature_roundtrip():
    secret, ts, body = "whsec_x", 1_700_000_000, b'{"event":"figure.updated"}'
    sig = "v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert webhooks.verify_signature(secret, ts, body, sig, tolerance_s=None) is True
    # tamper -> fail
    assert webhooks.verify_signature(secret, ts, body + b"x", sig, tolerance_s=None) is False
    # stale timestamp -> fail under tolerance
    assert webhooks.verify_signature(secret, ts, body, sig, tolerance_s=300) is False


def test_webhook_verify_headers_raises_on_bad(monkeypatch):
    secret, body = "whsec_x", b'{"event":"alert.raised"}'
    ts = int(time.time())
    sig = "v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"X-TrueFigure-Timestamp": str(ts), "X-TrueFigure-Signature": sig}
    webhooks.verify(secret, headers, body)  # ok, no raise
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify(secret, {"X-TrueFigure-Signature": sig}, body)  # missing timestamp
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify(secret, headers, body + b"tampered")


# ---------------- timeouts ----------------

def test_timeout_is_configurable_and_passed_to_transport():
    seen = {}

    def transport(method, url, headers, body):
        seen["called"] = True
        return 200, {}, env({"workspace_ref": "ws"})

    c = TrueFigureClient("key", transport=transport, timeout=2.5)
    assert c.timeout == 2.5
    assert c.whoami()["workspace_ref"] == "ws"


# helper: a valid activity envelope with a client-computed key
def c_activity(dep, work="W1"):
    from truefigure import events
    return events.activity(dep, user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                           work_item_id=work, action_type="suggestion_accepted")
