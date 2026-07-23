"""P3 — SERVER-SIDE event_key correctness (no client involvement).

event_key computation is proprietary and lives ONLY on the server
(`truefigure_server.eventkey`). The shipped SDKs never compute it. These tests lock
the server algorithm to frozen golden digests and exercise family scoping,
idempotency scope, and timestamp canonicalization. No database needed.
"""

from __future__ import annotations

import pytest

from truefigure_server.domain.entities import eventkey

DEP = "dep_x"
TS = "2026-07-01T09:14:00+01:00"  # offset form; canonicalizes to 08:14:00.000Z

# Golden vectors: freeze the wire format so any accidental change to the digest
# (field order, separator, canonicalization, truncation) is caught immediately.
GOLDEN = {
    "activity": ("activity",
                 {"user_ref": "u", "timestamp": TS, "work_item_id": "W", "action_type": "draft_generated"},
                 "b68a42f7b50e50c2fa6b668bc4de3687"),
    "cost_meter": ("cost_meter",
                   {"meter": "tokens_out", "timestamp": TS, "meter_ref": "hourly"},
                   "327ca620d6f22fdb338b3b6fdf28fbfc"),
    "lifecycle": ("lifecycle",
                  {"work_item_id": "W", "event": "status_change", "timestamp": TS, "status_to": "APPROVED"},
                  "8b22ca7bc1243c656e5759fb52ab76f5"),
    "quality_signal": ("quality_signal",
                       {"work_item_id": "W", "signal": "error_found", "timestamp": TS},
                       "2b8b7633def8d5f35bf7bcc1f347b4aa"),
    "revenue_signal": ("revenue_signal",
                       {"work_item_id": "W", "revenue_ref": "DEAL-1", "timestamp": TS},
                       "1f5e39e2862ac21386a59e072d867f12"),
}


@pytest.mark.parametrize("name", list(GOLDEN))
def test_event_key_matches_golden_and_is_deterministic(name: str) -> None:
    event_type, payload, expected = GOLDEN[name]
    k1 = eventkey.event_key(DEP, event_type, payload)
    k2 = eventkey.event_key(DEP, event_type, dict(payload))
    assert k1 == expected
    assert k1 == k2  # deterministic
    assert len(k1) == 32 and all(c in "0123456789abcdef" for c in k1)


def test_deployment_scoped_families_include_deployment_id() -> None:
    for et in ("activity", "cost_meter"):
        _, payload, _ = GOLDEN[et]
        assert eventkey.event_key(DEP, et, payload) != eventkey.event_key("dep_other", et, payload)
    assert eventkey.DEPLOYMENT_SCOPED == frozenset({"activity", "cost_meter"})


def test_workspace_scoped_families_ignore_deployment_id() -> None:
    for et in ("lifecycle", "quality_signal", "revenue_signal"):
        _, payload, _ = GOLDEN[et]
        assert eventkey.event_key(DEP, et, payload) == eventkey.event_key("dep_other", et, payload)


def test_idempotency_scope_changes_key() -> None:
    p = {"user_ref": "u", "timestamp": TS, "work_item_id": "W", "action_type": "draft_generated"}
    assert eventkey.event_key(DEP, "activity", p) != eventkey.event_key(DEP, "activity", p, "scopeA")


def test_offset_and_z_canonicalize_equal() -> None:
    base = {"user_ref": "u", "work_item_id": "W", "action_type": "draft_generated"}
    p1 = {**base, "timestamp": "2026-07-01T09:14:00+01:00"}
    p2 = {**base, "timestamp": "2026-07-01T08:14:00Z"}
    assert eventkey.event_key(DEP, "activity", p1) == eventkey.event_key(DEP, "activity", p2)


@pytest.mark.parametrize("bad", [None, 12345, "not-a-date", "2026-07-01T00:00:00"])
def test_canonical_ts_rejects_bad(bad) -> None:
    with pytest.raises(eventkey.TimestampError):
        eventkey.canonical_ts(bad)


def test_id_fields_extraction() -> None:
    p = {"user_ref": "u", "assignee_ref": "a", "work_item_id": "W", "action_type": "x"}
    assert eventkey.id_fields("activity", p) == {"user_ref": "u", "assignee_ref": "a", "work_item_id": "W"}
