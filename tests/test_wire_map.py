"""The wire-name map: all 13 renames, tested in both directions."""

from __future__ import annotations

import pytest

from truefigure_sdk import wire

EXPECTED = [
    ("roster", "kind", "user_type"),
    ("roster", "role", "job_role"),
    ("imports", "kind", "import_type"),
    ("deployments", "type", "deployment_type"),
    ("deployments", "mode", "deployment_mode"),
    ("change_events", "type", "change_type"),
    ("change_events", "sidedness", "sided_type"),
    ("change_events", "supersedes", "superseded_by_id"),
    ("license", "period_unit", "period_unit_type"),
    ("webhooks", "events", "webhook_event_type"),
    ("lifecycle", "size_band", "size_type"),
    ("envelope", "origin", "origin_type"),
    ("api_key", "prefix", "api_key_mode"),
]


def test_thirteen_renames_exist() -> None:
    assert len(wire.RENAMES) == 13
    assert set(wire.RENAMES) == set(EXPECTED)


@pytest.mark.parametrize(("resource", "wire_name", "column"), EXPECTED)
def test_to_column(resource: str, wire_name: str, column: str) -> None:
    assert wire.to_column(resource, wire_name) == column


@pytest.mark.parametrize(("resource", "wire_name", "column"), EXPECTED)
def test_to_wire(resource: str, wire_name: str, column: str) -> None:
    assert wire.to_wire(resource, column) == wire_name


def test_roundtrip_identity_for_unmapped() -> None:
    assert wire.to_column("roster", "user_ref") == "user_ref"
    assert wire.to_wire("roster", "team") == "team"


def test_key_prefix_value_transform() -> None:
    assert wire.key_prefix_to_mode("tf_live_" + "0" * 32) == "production"
    assert wire.key_prefix_to_mode("tf_test_" + "0" * 32) == "test"
    assert wire.key_prefix_to_mode("bogus_x") is None
    assert wire.mode_to_key_prefix("production") == "tf_live_"
    assert wire.mode_to_key_prefix("test") == "tf_test_"
