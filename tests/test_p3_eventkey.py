"""P3 — event_key parity with the reference client across all five families,
plus canonicalization edge cases. No database needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from truefigure_sdk import eventkey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from truefigure import events as ce  # noqa: E402

DEP = "dep_x"
TS = "2026-07-01T09:14:00+01:00"  # offset form; canonicalizes to 08:14:00.000Z


def _key(built: dict) -> str:
    return built["_event_key"]


def test_activity_key_matches_client() -> None:
    built = ce.activity(DEP, user_ref="u", timestamp=TS, work_item_id="W", action_type="draft_generated")
    got = eventkey.event_key(DEP, "activity", built["payload"])
    assert got == _key(built)


def test_cost_meter_key_matches_client() -> None:
    built = ce.cost_meter(DEP, meter="tokens_out", quantity=10, timestamp=TS, meter_ref="hourly")
    got = eventkey.event_key(DEP, "cost_meter", built["payload"])
    assert got == _key(built)


def test_lifecycle_key_matches_client_and_is_workspace_scoped() -> None:
    built = ce.lifecycle(DEP, work_item_id="W", event="status_change", timestamp=TS, status_to="APPROVED")
    got = eventkey.event_key(DEP, "lifecycle", built["payload"])
    other = eventkey.event_key("dep_other", "lifecycle", built["payload"])
    assert got == _key(built)
    assert got == other  # deployment_id is NOT part of a workspace-scoped key


def test_quality_key_matches_client() -> None:
    built = ce.quality_signal(DEP, work_item_id="W", signal="error_found", timestamp=TS, error_class="billing")
    got = eventkey.event_key(DEP, "quality_signal", built["payload"])
    assert got == _key(built)


def test_revenue_key_matches_client() -> None:
    built = ce.revenue_signal(DEP, work_item_id="W", revenue_ref="DEAL-1", timestamp=TS)
    got = eventkey.event_key(DEP, "revenue_signal", built["payload"])
    assert got == _key(built)


def test_idempotency_scope_changes_key() -> None:
    p = {"user_ref": "u", "timestamp": TS, "work_item_id": "W", "action_type": "draft_generated"}
    assert eventkey.event_key(DEP, "activity", p) != eventkey.event_key(DEP, "activity", p, "scopeA")


def test_offset_and_z_canonicalize_equal() -> None:
    p1 = {"user_ref": "u", "timestamp": "2026-07-01T09:14:00+01:00", "work_item_id": "W", "action_type": "draft_generated"}
    p2 = {"user_ref": "u", "timestamp": "2026-07-01T08:14:00Z", "work_item_id": "W", "action_type": "draft_generated"}
    assert eventkey.event_key(DEP, "activity", p1) == eventkey.event_key(DEP, "activity", p2)


@pytest.mark.parametrize("bad", [None, 12345, "not-a-date", "2026-07-01T00:00:00"])
def test_canonical_ts_rejects_bad(bad) -> None:
    with pytest.raises(eventkey.TimestampError):
        eventkey.canonical_ts(bad)


def test_id_fields_extraction() -> None:
    p = {"user_ref": "u", "assignee_ref": "a", "work_item_id": "W", "action_type": "x"}
    assert eventkey.id_fields("activity", p) == {"user_ref": "u", "assignee_ref": "a", "work_item_id": "W"}
