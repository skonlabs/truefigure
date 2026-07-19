"""Event builders.

The structural guarantees live here, in the type system, not in documentation:
- There is no parameter through which prompt/response content could be passed (BR-001).
- There is no savings/roi/value field anywhere (the SDK witnesses; it never values).
- user_ref / assignee_ref are opaque pseudonymous keys (server-side roster resolution).
Builders accept fixed keyword arguments only; unknown kwargs raise immediately.
Natural keys are deterministic functions of payload fields (BR-009 idempotency).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

SCHEMA_VERSION = "1.0"

ACTION_TYPES = {
    "suggestion_shown", "suggestion_accepted", "suggestion_rejected",
    "draft_generated", "autocomplete_used", "case_handled_autonomous",
    "escalated_to_human", "lookup_performed", "classification_applied", "other_assist",
}
LIFECYCLE_EVENTS = {"created", "status_change", "assigned", "completed", "reopened", "escalated", "transferred"}
METERS = {"tokens_in", "tokens_out", "api_calls", "compute_seconds", "seats_active", "storage_gb"}
QUALITY_SIGNALS = {"error_found", "rework_required", "qa_label", "customer_bounce", "sla_breach"}
TOUCHPOINTS = {"created", "influenced", "closed_adjacent"}
ORIGINS = {"customer_system", "vendor_product"}


def _iso(ts) -> str:
    """Canonicalize per spec 3.8: UTC RFC3339, exactly millisecond precision, 'Z' suffix.
    2026-07-15T15:02:11+01:00 and 2026-07-15T14:02:11Z both canonicalize to
    2026-07-15T14:02:11.000Z, so equal instants digest to equal event_keys (C4)."""
    if isinstance(ts, str):
        raw = ts.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError as e:
            raise ValueError(f"timestamp must be ISO-8601 with timezone: {ts!r}") from e
    if not isinstance(ts, datetime):
        raise ValueError("timestamp must be datetime or ISO-8601 string")
    if ts.tzinfo is None:
        raise ValueError("timestamp must carry a timezone (spec 1.1); naive datetimes are rejected")
    ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def _require(cond: bool, msg: str):
    if not cond:
        raise ValueError(msg)


def natural_key(deployment_id: str, event_type: str, payload: dict, scope: Optional[str] = None) -> str:
    """event_key = SHA-256 digest of the family DEDUP key (spec 3.7).

    activity / cost_meter are deployment-scoped: deployment_id is part of identity.
    lifecycle / quality_signal / revenue_signal are workspace-scoped: deployment_id
    authorizes and routes but is NOT part of identity - the same work-item fact sent
    under two deployments yields the SAME key (the second send is a duplicate no-op).
    The workspace is implicit (bound to the API key), so it never appears in the digest.
    """
    if event_type == "activity":
        parts = [deployment_id, payload["user_ref"], payload["work_item_id"],
                 payload["timestamp"], payload["action_type"]]
    elif event_type == "cost_meter":
        parts = [deployment_id, payload["meter"], payload["timestamp"], payload.get("meter_ref", "")]
    elif event_type == "lifecycle":
        parts = [payload["work_item_id"], payload["event"], payload["timestamp"],
                 payload.get("status_to", "")]
    elif event_type == "quality_signal":
        parts = [payload["work_item_id"], payload["signal"], payload["timestamp"]]
    elif event_type == "revenue_signal":
        parts = [payload["work_item_id"], payload["revenue_ref"], payload["timestamp"]]
    else:
        raise ValueError(f"unknown event_type {event_type}")
    raw = "|".join([event_type] + parts + ([scope] if scope else []))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _envelope(deployment_id: str, event_type: str, origin: str, payload: dict, scope: Optional[str]) -> dict:
    _require(origin in ORIGINS, f"origin must be one of {sorted(ORIGINS)}")
    env = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment_id,
        "event_type": event_type,
        "origin": origin,
        "payload": payload,
    }
    if scope:
        env["idempotency_scope"] = scope
    env["_event_key"] = natural_key(deployment_id, event_type, payload, scope)  # stripped before send
    return env


def activity(deployment_id: str, *, user_ref: str, timestamp, work_item_id: str,
             action_type: str, origin: str = "customer_system",
             model_version_ref: Optional[str] = None, session_ref: Optional[str] = None,
             idempotency_scope: Optional[str] = None) -> dict:
    _require(action_type in ACTION_TYPES, f"action_type must be one of {sorted(ACTION_TYPES)}")
    p = {"user_ref": user_ref, "timestamp": _iso(timestamp),
         "work_item_id": work_item_id, "action_type": action_type}
    if model_version_ref: p["model_version_ref"] = model_version_ref
    if session_ref: p["session_ref"] = session_ref
    return _envelope(deployment_id, "activity", origin, p, idempotency_scope)


def lifecycle(deployment_id: str, *, work_item_id: str, event: str, timestamp,
              origin: str = "customer_system", status_from: Optional[str] = None,
              status_to: Optional[str] = None, assignee_ref: Optional[str] = None,
              category: Optional[str] = None, size_band: Optional[str] = None,
              idempotency_scope: Optional[str] = None) -> dict:
    _require(event in LIFECYCLE_EVENTS, f"event must be one of {sorted(LIFECYCLE_EVENTS)}")
    p = {"work_item_id": work_item_id, "event": event, "timestamp": _iso(timestamp)}
    for k, v in (("status_from", status_from), ("status_to", status_to),
                 ("assignee_ref", assignee_ref), ("category", category), ("size_band", size_band)):
        if v is not None:
            p[k] = v
    return _envelope(deployment_id, "lifecycle", origin, p, idempotency_scope)


def cost_meter(deployment_id: str, *, meter: str, quantity: float, timestamp,
               origin: str = "customer_system", meter_ref: Optional[str] = None,
               idempotency_scope: Optional[str] = None) -> dict:
    _require(meter in METERS, f"meter must be one of {sorted(METERS)}")
    _require(quantity >= 0, "quantity must be >= 0")
    p = {"meter": meter, "quantity": quantity, "timestamp": _iso(timestamp)}
    if meter_ref: p["meter_ref"] = meter_ref
    return _envelope(deployment_id, "cost_meter", origin, p, idempotency_scope)


def quality_signal(deployment_id: str, *, work_item_id: str, signal: str, timestamp,
                   origin: str = "customer_system", label_schema_ref: Optional[str] = None,
                   label_value: Optional[str] = None, error_class: Optional[str] = None,
                   idempotency_scope: Optional[str] = None) -> dict:
    _require(signal in QUALITY_SIGNALS, f"signal must be one of {sorted(QUALITY_SIGNALS)}")
    if signal == "qa_label":
        _require(bool(label_schema_ref and label_value),
                 "qa_label requires label_schema_ref and label_value (categorical; register the schema first)")
    p = {"work_item_id": work_item_id, "signal": signal, "timestamp": _iso(timestamp)}
    for k, v in (("label_schema_ref", label_schema_ref), ("label_value", label_value), ("error_class", error_class)):
        if v is not None:
            p[k] = v
    return _envelope(deployment_id, "quality_signal", origin, p, idempotency_scope)


def revenue_signal(deployment_id: str, *, work_item_id: str, revenue_ref: str, timestamp,
                   origin: str = "customer_system", touchpoint: str = "influenced",
                   idempotency_scope: Optional[str] = None) -> dict:
    _require(touchpoint in TOUCHPOINTS, f"touchpoint must be one of {sorted(TOUCHPOINTS)}")
    p = {"work_item_id": work_item_id, "revenue_ref": revenue_ref,
         "timestamp": _iso(timestamp), "touchpoint": touchpoint}
    return _envelope(deployment_id, "revenue_signal", origin, p, idempotency_scope)
