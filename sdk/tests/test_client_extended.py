"""Extended client coverage: every remaining endpoint has a typed binding that
sends the right method/URL/body. Uses an injected transport (no network)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from truefigure import TrueFigureClient


def make_client(capture, data=None):
    def transport(method, url, headers, body):
        capture.append({"method": method, "url": url, "headers": headers, "body": body})
        return 200, {}, {"data": data if data is not None else {"ok": True},
                         "meta": {"schema_version": "1.0", "request_id": "req-x", "as_of": "2026-07-15T00:00:00Z"},
                         "errors": []}
    return TrueFigureClient("tf_test_x", base_url="https://x", deployment_id="dep_1", transport=transport)


def last(cap):
    return cap[-1]


def test_deployment_bindings():
    cap = []
    c = make_client(cap)
    c.list_deployments()
    assert last(cap)["method"] == "GET" and last(cap)["url"].endswith("/v1/deployments")
    c.get_deployment()
    assert last(cap)["url"].endswith("/v1/deployments/dep_1")
    c.update_deployment(name="New")
    assert last(cap)["method"] == "PATCH" and last(cap)["body"] == {"name": "New"}


def test_roster_bindings():
    cap = []
    c = make_client(cap)
    c.upsert_roster([{"user_ref": "u", "kind": "person"}])
    assert last(cap)["method"] == "POST" and last(cap)["url"].endswith("/v1/roster:batch")
    assert last(cap)["body"] == {"users": [{"user_ref": "u", "kind": "person"}]}
    c.list_roster(status="active", kind="person")
    assert "status=active" in last(cap)["url"] and "kind=person" in last(cap)["url"]


def test_license_bindings():
    cap = []
    c = make_client(cap)
    c.declare_license(10, "2026-01-01", price_per_seat=20.0, licensed_user_refs=["a"])
    b = last(cap)["body"]
    assert last(cap)["method"] == "PUT" and b["seats_paid"] == 10 and b["price_per_seat"] == 20.0
    assert b["licensed_user_refs"] == ["a"]
    c.get_license()
    assert last(cap)["url"].endswith("/v1/deployments/dep_1/license")


def test_namespace_and_parameter_bindings():
    cap = []
    c = make_client(cap)
    c.register_id_namespace("user_ref", "okta", format_regex="^x$")
    assert last(cap)["url"].endswith("/v1/id-namespaces") and last(cap)["body"]["field"] == "user_ref"
    c.list_id_namespaces()
    assert last(cap)["method"] == "GET"
    c.list_parameters()
    assert last(cap)["url"].endswith("/v1/parameters")
    c.get_parameter_version(3)
    assert last(cap)["url"].endswith("/v1/parameters/3")


def test_qa_and_webhook_and_mapping_bindings():
    cap = []
    c = make_client(cap)
    c.register_label_schema("qa", "Q", ["p", "f"])
    assert last(cap)["url"].endswith("/v1/qa-labels/schemas") and last(cap)["body"]["label_values"] == ["p", "f"]
    c.list_label_schemas()
    c.create_webhook("https://h", ["figure.updated"], "s")
    assert last(cap)["body"] == {"url": "https://h", "events": ["figure.updated"], "secret": "s"}
    c.list_webhooks()
    c.delete_webhook("wh_1")
    assert last(cap)["method"] == "DELETE" and last(cap)["url"].endswith("/v1/webhooks/wh_1")
    c.webhook_deliveries("wh_1")
    assert last(cap)["url"].endswith("/v1/webhooks/wh_1/deliveries")
    c.create_mapping_contract("s", "activity", 1, {"a": 1})
    assert last(cap)["body"]["version"] == 1
    c.list_mapping_contracts()
    c.list_change_events()
    assert last(cap)["url"].endswith("/v1/change-events")


def test_figure_and_report_detail_bindings():
    cap = []
    c = make_client(cap)
    c.get_figure("fig_1")
    assert last(cap)["url"].endswith("/v1/figures/dep_1/fig_1")
    c.get_report("rep_1")
    assert last(cap)["url"].endswith("/v1/reports/dep_1/rep_1")


def test_full_endpoint_method_count():
    # Every one of the 29 endpoints has at least one client method.
    methods = [m for m in dir(TrueFigureClient) if not m.startswith("_")]
    for name in ("register_deployment", "list_deployments", "get_deployment", "update_deployment",
                 "upsert_roster", "list_roster", "declare_license", "get_license",
                 "register_id_namespace", "list_id_namespaces", "create_parameter_version",
                 "list_parameters", "get_parameter_version", "register_label_schema", "list_label_schemas",
                 "create_webhook", "list_webhooks", "delete_webhook", "webhook_deliveries",
                 "create_mapping_contract", "list_mapping_contracts", "declare_change_event",
                 "list_change_events", "whoami", "create_import", "import_status",
                 "figures", "get_figure", "figure_lineage", "reports", "get_report",
                 "live_usage", "live_cost", "integration_health", "alerts", "ack_alert",
                 "event_status", "flush", "echo"):
        assert name in methods, f"client missing binding {name}"
