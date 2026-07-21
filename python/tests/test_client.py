"""Tests for the TrueFigure reference client. Run: python -m pytest tests/ -q"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from truefigure import TrueFigureClient, TrueFigureError, events


def make_transport(script):
    """script: list of (status, headers, env) responses, consumed in order; records calls."""
    calls = []
    def transport(method, url, headers, body):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        resp = script.pop(0) if script else (200, {}, ok_env())
        return resp
    transport.calls = calls
    return transport


def ok_env(results=None, request_id="req-1"):
    return {"data": {"results": results or []}, "meta": {"schema_version": "1.0", "request_id": request_id, "as_of": "2026-07-15T00:00:00Z"}, "errors": []}


def accepted(n):
    return [{"index": i, "status": "accepted", "event_key": f"k{i}"} for i in range(n)]


# ---------- builders: structural guarantees ----------

def test_builders_reject_unknown_fields():
    with pytest.raises(TypeError):
        events.activity("dep-1", user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                        work_item_id="w1", action_type="draft_generated",
                        prompt_text="THIS MUST NOT EXIST")  # no content channel exists


def test_builders_reject_value_assertions():
    with pytest.raises(TypeError):
        events.cost_meter("dep-1", meter="api_calls", quantity=10,
                          timestamp="2026-07-01T00:00:00Z", savings_usd=5000)


def test_enum_validation():
    with pytest.raises(ValueError):
        events.activity("dep-1", user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                        work_item_id="w1", action_type="NOT_A_REAL_ACTION")


def test_qa_label_requires_schema():
    with pytest.raises(ValueError):
        events.quality_signal("dep-1", work_item_id="w1", signal="qa_label",
                              timestamp="2026-07-01T00:00:00Z")  # no label schema/value


def test_natural_key_deterministic():
    kw = dict(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
    e1 = events.activity("dep-1", **kw)
    e2 = events.activity("dep-1", **kw)
    assert e1["_event_key"] == e2["_event_key"]  # duplicate send -> same key -> server no-op


# ---------- client: batching, retry, buffer, echo ----------

def test_flush_sends_batch_and_maps_results():
    t = make_transport([(200, {}, ok_env(accepted(2)))])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    c.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
    c.track_cost(meter="api_calls", quantity=3, timestamp="2026-07-01T00:00:00Z")
    br = c.flush()
    assert len(br.accepted) == 2 and not br.rejected
    sent = t.calls[0]["body"]["events"]
    assert all("_event_key" not in e for e in sent)  # internal key stripped from wire
    assert sent[0]["schema_version"] == "1.0" and sent[0]["origin"] == "customer_system"


def test_retry_on_429_honors_retry_after_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    rate_env = {"data": None, "meta": {"request_id": "r"}, "errors": [
        {"code": "TF-RATE-001", "message": "slow down", "retryable": True, "retry_after": 2, "request_id": "r"}]}
    t = make_transport([(429, {}, rate_env), (200, {}, ok_env(accepted(1)))])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    c.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
    br = c.flush()
    assert len(br.accepted) == 1 and sleeps == [2]


def test_nonretryable_error_raises_with_code():
    err_env = {"data": None, "meta": {"request_id": "r9"}, "errors": [
        {"code": "TF-EVT-002", "message": "unknown deployment", "retryable": False, "request_id": "r9"}]}
    t = make_transport([(404, {}, err_env)])
    c = TrueFigureClient("key", transport=t, deployment_id="ghost")
    with pytest.raises(TrueFigureError) as e:
        c.figures()
    assert "TF-EVT-002" in str(e.value) and e.value.errors[0].fix_owner == "config_owner"


def test_offline_buffer_spools_and_replays():
    with tempfile.TemporaryDirectory() as d:
        buf = os.path.join(d, "spool.jsonl")
        fail = {"data": None, "meta": {"request_id": "r"}, "errors": [
            {"code": "TF-SRV-002", "message": "paused", "retryable": True, "request_id": "r"}]}
        # all attempts fail -> spool
        t1 = make_transport([(503, {}, fail)] * 6)
        c1 = TrueFigureClient("key", transport=t1, deployment_id="dep-1", buffer_path=buf, max_retries=1)
        c1.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
        import time as _t; orig = _t.sleep; _t.sleep = lambda s: None
        try:
            br = c1.flush()
        finally:
            _t.sleep = orig
        assert br.results == [] and os.path.getsize(buf) > 0
        # next client drains the spool and delivers (idempotency makes replay safe)
        t2 = make_transport([(200, {}, ok_env(accepted(1)))])
        c2 = TrueFigureClient("key", transport=t2, deployment_id="dep-1", buffer_path=buf)
        br2 = c2.flush()
        assert len(br2.accepted) == 1 and os.path.getsize(buf) == 0


def test_echo_uses_test_mode_header():
    echo_results = [{"index": 0, "status": "accepted", "event_key": "k0",
                     "echo": {"resolved_person": "p-77", "resolution_basis": "deterministic",
                              "matched_work_item": "wi-9", "action_class": "assist", "accepted": True, "reason": None}}]
    t = make_transport([(200, {}, ok_env(echo_results))])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    c.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="wi-9", action_type="draft_generated")
    br = c.echo()
    assert t.calls[0]["headers"]["X-TrueFigure-Mode"] == "test"
    assert br.results[0]["echo"]["resolved_person"] == "p-77"


def test_refused_figures_are_data_not_errors():
    fig = {"figures": [{"figure_id": "f1", "status": "refused", "reason": "insufficient_evidence",
                        "margin_exceeds_effect": True, "expected_verdict_date": "2026-09-01"}]}
    t = make_transport([(200, {}, {"data": fig, "meta": {"schema_version": "1.0", "request_id": "r", "as_of": "now"}, "errors": []})])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    data = c.figures(period="2026-Q2")
    assert data["figures"][0]["status"] == "refused"  # BR-005: a refusal is a successful response


def test_lifecycle_key_is_workspace_scoped_deployment_independent():
    kw = dict(work_item_id="CLM-1", event="completed", timestamp="2026-07-01T10:05:00Z")
    e1 = events.lifecycle("dep_A", **kw)
    e2 = events.lifecycle("dep_B", **kw)  # second deployment measuring the same queue
    assert e1["_event_key"] == e2["_event_key"]  # same fact -> same identity -> server duplicate


def test_activity_key_is_deployment_scoped():
    kw = dict(user_ref="u1", timestamp="2026-07-01T00:00:00Z", work_item_id="w1", action_type="draft_generated")
    e1 = events.activity("dep_A", **kw)
    e2 = events.activity("dep_B", **kw)
    assert e1["_event_key"] != e2["_event_key"]  # AI touches belong to their deployment


def test_status_change_key_includes_status_to():
    base = dict(work_item_id="CLM-1", event="status_change", timestamp="2026-07-01T10:00:00Z", status_from="A")
    e1 = events.lifecycle("dep_A", status_to="B", **base)
    e2 = events.lifecycle("dep_A", status_to="C", **base)
    assert e1["_event_key"] != e2["_event_key"]  # distinct transitions at one timestamp stay distinct


def test_timestamp_canonicalization_equal_instants_equal_keys():
    kw = dict(work_item_id="CLM-1", event="completed")
    e1 = events.lifecycle("dep_A", timestamp="2026-07-15T14:02:11Z", **kw)
    e2 = events.lifecycle("dep_A", timestamp="2026-07-15T15:02:11+01:00", **kw)
    e3 = events.lifecycle("dep_A", timestamp="2026-07-15T14:02:11.000+00:00", **kw)
    assert e1["_event_key"] == e2["_event_key"] == e3["_event_key"]
    assert e1["payload"]["timestamp"] == "2026-07-15T14:02:11.000Z"  # canonical wire form


def test_naive_timestamp_rejected():
    from datetime import datetime as dt
    with pytest.raises(ValueError):
        events.cost_meter("dep_A", meter="api_calls", quantity=1, timestamp=dt(2026, 7, 1, 12, 0, 0))


def test_source_ref_header_attached():
    t = make_transport([(200, {}, ok_env(accepted(1)))])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1", source_ref="aims_duty_export")
    c.track_cost(meter="api_calls", quantity=1, timestamp="2026-07-01T00:00:00Z")
    c.flush()
    assert t.calls[0]["headers"]["X-TrueFigure-Source"] == "aims_duty_export"


def test_whoami_and_change_event_paths():
    t = make_transport([
        (200, {}, {"data": {"workspace_ref": "ws_1", "mode": "production"}, "meta": {"request_id": "r"}, "errors": []}),
        (201, {}, {"data": {"change_event_id": "chg_1"}, "meta": {"request_id": "r"}, "errors": []}),
    ])
    c = TrueFigureClient("key", transport=t, deployment_id="dep-1")
    assert c.whoami()["workspace_ref"] == "ws_1"
    ce = c.declare_change_event("system_cutover", "2026-10-26T00:00:00Z", "one_sided", "AIMS cutover")
    assert ce["change_event_id"] == "chg_1"
    assert t.calls[1]["url"].endswith("/v1/change-events")


def test_register_deployment_carries_service_user_refs(monkeypatch):
    from truefigure.client import TrueFigureClient
    captured = {}
    cl = TrueFigureClient("tf_test_x", base_url="https://x")
    def fake(method, path, body=None, **kw):
        captured.update({"method": method, "path": path, "body": body})
        return {"data": {"deployment_id": "dep_1"}}
    monkeypatch.setattr(cl, "_request", fake)
    cl.register_deployment("D", "saas_tool", external_ref="deal-1", service_user_refs=["svc_mv_incumbent"])
    assert captured["body"]["service_user_refs"] == ["svc_mv_incumbent"]
    assert captured["path"] == "/v1/deployments"
