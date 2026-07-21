"""Canonical event_key computation — MUST match the reference client byte-for-byte.

spec 3.8: timestamps canonicalized to UTC RFC3339 millisecond 'Z' form BEFORE
digesting; fields pipe-joined in family/table order; absent optionals = empty
string; sha256 -> first 32 lowercase hex. Dedup scope:
  * activity, cost_meter  -> deployment-scoped (deployment_id in the key)
  * lifecycle, quality, revenue -> workspace-scoped (deployment_id NOT in the key)
The workspace is implicit (bound to the key) and never appears in the digest.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


class TimestampError(ValueError):
    """Raised when a timestamp is missing, naive, or not ISO-8601."""


def canonical_ts(ts: Any) -> str:
    """Canonicalize to 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC). Naive/invalid -> TimestampError."""
    if not isinstance(ts, str):
        raise TimestampError("timestamp must be an ISO-8601 string")
    raw = ts.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise TimestampError(f"timestamp not ISO-8601: {ts!r}") from exc
    if parsed.tzinfo is None:
        raise TimestampError("timestamp must carry a timezone (spec 1.1)")
    parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{parsed.microsecond // 1000:03d}Z"


def event_key(deployment_id: str, event_type: str, payload: dict[str, Any], scope: str | None = None) -> str:
    """Compute the 32-hex event_key. `payload['timestamp']` is canonicalized here."""
    ts = canonical_ts(payload["timestamp"])
    if event_type == "activity":
        parts = [deployment_id, payload["user_ref"], payload["work_item_id"], ts, payload["action_type"]]
    elif event_type == "cost_meter":
        parts = [deployment_id, payload["meter"], ts, payload.get("meter_ref", "")]
    elif event_type == "lifecycle":
        parts = [payload["work_item_id"], payload["event"], ts, payload.get("status_to", "")]
    elif event_type == "quality_signal":
        parts = [payload["work_item_id"], payload["signal"], ts]
    elif event_type == "revenue_signal":
        parts = [payload["work_item_id"], payload["revenue_ref"], ts]
    else:  # pragma: no cover - schema validation rejects other event types first
        raise ValueError(f"unknown event_type {event_type}")
    raw = "|".join([event_type, *parts] + ([scope] if scope else []))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


DEPLOYMENT_SCOPED = frozenset({"activity", "cost_meter"})


def id_fields(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """Return the id-bearing fields present in a payload, keyed by field name."""
    out: dict[str, str] = {}
    for field in ("user_ref", "work_item_id", "assignee_ref"):
        val = payload.get(field)
        if isinstance(val, str):
            out[field] = val
    return out
