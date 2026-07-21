"""Event envelope builders — pure shaping of the PUBLIC wire contract.

THIN BY CONSTRUCTION. This module contains **no** proprietary or core logic:
  * no event_key / natural-key digest (the server computes and returns it),
  * no timestamp canonicalization (the server canonicalizes before digesting),
  * no dedup-scope rules, no enum whitelists, no business-rule validation,
  * no value/grade/margin/figure math of any kind.

Each builder only assembles the request envelope described by the published
`openapi.yaml` + `schemas/events.schema.json` — shape the caller could write by
hand. The server is the single source of truth for validation, identity,
deduplication, canonicalization, and measurement. Unknown/invalid input is not
rejected here; it is surfaced as a server error object after send.

Passing extra payload fields is allowed via **extra: they are forwarded verbatim.
"""
from __future__ import annotations

from typing import Any, Optional

SCHEMA_VERSION = "1.0"


def build_event(deployment_id: str, event_type: str, payload: dict[str, Any], *,
                origin: str = "customer_system", idempotency_scope: Optional[str] = None) -> dict[str, Any]:
    """Assemble the raw ingestion envelope. No validation, no key, no canonicalization."""
    env: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment_id,
        "event_type": event_type,
        "origin": origin,
        "payload": dict(payload),
    }
    if idempotency_scope is not None:
        env["idempotency_scope"] = idempotency_scope
    return env


def _payload(fixed: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Merge fixed named fields with forwarded extras, dropping only None named fields."""
    out = {k: v for k, v in fixed.items() if v is not None}
    out.update(extra)
    return out


def activity(deployment_id: str, *, user_ref: str, timestamp: Any, work_item_id: str,
             action_type: str, origin: str = "customer_system",
             idempotency_scope: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    p = _payload({"user_ref": user_ref, "timestamp": timestamp,
                  "work_item_id": work_item_id, "action_type": action_type}, extra)
    return build_event(deployment_id, "activity", p, origin=origin, idempotency_scope=idempotency_scope)


def lifecycle(deployment_id: str, *, work_item_id: str, event: str, timestamp: Any,
              origin: str = "customer_system", idempotency_scope: Optional[str] = None,
              **extra: Any) -> dict[str, Any]:
    p = _payload({"work_item_id": work_item_id, "event": event, "timestamp": timestamp}, extra)
    return build_event(deployment_id, "lifecycle", p, origin=origin, idempotency_scope=idempotency_scope)


def cost_meter(deployment_id: str, *, meter: str, quantity: float, timestamp: Any,
               origin: str = "customer_system", idempotency_scope: Optional[str] = None,
               **extra: Any) -> dict[str, Any]:
    p = _payload({"meter": meter, "quantity": quantity, "timestamp": timestamp}, extra)
    return build_event(deployment_id, "cost_meter", p, origin=origin, idempotency_scope=idempotency_scope)


def quality_signal(deployment_id: str, *, work_item_id: str, signal: str, timestamp: Any,
                   origin: str = "customer_system", idempotency_scope: Optional[str] = None,
                   **extra: Any) -> dict[str, Any]:
    p = _payload({"work_item_id": work_item_id, "signal": signal, "timestamp": timestamp}, extra)
    return build_event(deployment_id, "quality_signal", p, origin=origin, idempotency_scope=idempotency_scope)


def revenue_signal(deployment_id: str, *, work_item_id: str, revenue_ref: str, timestamp: Any,
                   origin: str = "customer_system", idempotency_scope: Optional[str] = None,
                   **extra: Any) -> dict[str, Any]:
    p = _payload({"work_item_id": work_item_id, "revenue_ref": revenue_ref, "timestamp": timestamp}, extra)
    return build_event(deployment_id, "revenue_signal", p, origin=origin, idempotency_scope=idempotency_scope)
