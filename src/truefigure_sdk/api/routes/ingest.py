"""Write plane — POST /v1/events:batch and GET /v1/events/{event_key}/status.

Pipeline per event (partial-batch: one bad event never blocks the rest):
  no-content / value scan -> schema_version -> JSON-schema structural validation
  -> timestamp canonicalize -> backfill-horizon range -> deployment resolve
  -> id-namespace regex fail-fast -> event_key + dedup -> persist (or, for a
  test-mode key, echo without persisting).

The wire event contract is validated against contract/events.schema.json loaded
at runtime (never a re-typed copy).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request
from jsonschema import Draft202012Validator

from truefigure_sdk.api.application_services.auth import Principal, require_principal
from truefigure_sdk.api.request_models.http import ok
from truefigure_sdk.domain.entities import eventkey
from truefigure_sdk.errors import TFError
from truefigure_sdk.platform.database import db

router = APIRouter()
SYSTEM_USER_ID = 1

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contract" / "events.schema.json"
_SCHEMA_VERSION_RE = re.compile(r"^1\.[0-9]+$")
_VALUE_FIELDS = {"value", "value_usd", "savings", "roi", "amount", "price", "dollars", "revenue_usd", "cost_usd"}
_MAX_BATCH = 500
_MAX_BODY_BYTES = 1024 * 1024


@lru_cache(maxsize=1)
def _envelope_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    env = dict(schema["$defs"]["envelope"])
    env["$defs"] = schema["$defs"]  # make internal #/$defs/... refs resolvable
    return Draft202012Validator(env)


class _Reject(Exception):
    """Internal signal that an event is rejected with a registry code."""

    def __init__(self, code: str, *, field_path: str | None = None, reason: str | None = None) -> None:
        self.err = TFError(code, field_path=field_path, detail=reason)
        super().__init__(code)


def _scan_forbidden_fields(event: dict[str, Any]) -> None:
    """content field -> TF-EVT-006; value-assertion field -> TF-EVT-008.

    These are additionalProperties violations that carry dedicated codes; check
    them before the generic structural validator so the right code is produced.
    """
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    for scope_name, obj in (("", event), ("payload.", payload)):
        for key in obj:
            if key == "content":
                raise _Reject("TF-EVT-006", field_path=f"{scope_name}content", reason="content is never accepted")
            if key in _VALUE_FIELDS:
                raise _Reject("TF-EVT-008", field_path=f"{scope_name}{key}",
                              reason="the SDK witnesses facts; it never accepts asserted value")


def _validate_structure(event: dict[str, Any]) -> None:
    sv = event.get("schema_version")
    if isinstance(sv, str) and not _SCHEMA_VERSION_RE.match(sv):
        raise _Reject("TF-EVT-001", field_path="schema_version", reason=f"unsupported schema_version {sv}")
    errors = sorted(_envelope_validator().iter_errors(event), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        path = ".".join(str(p) for p in first.path) or "(root)"
        raise _Reject("TF-EVT-003", field_path=path, reason=first.message)


def _check_timestamp(payload: dict[str, Any], horizon_days: int) -> str:
    try:
        ts = eventkey.canonical_ts(payload.get("timestamp"))
    except eventkey.TimestampError as exc:
        raise _Reject("TF-EVT-003", field_path="payload.timestamp", reason=str(exc)) from exc
    when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if when > now + timedelta(hours=24):
        raise _Reject("TF-EVT-009", field_path="payload.timestamp", reason="timestamp is in the future")
    if when < now - timedelta(days=horizon_days):
        raise _Reject("TF-EVT-009", field_path="payload.timestamp",
                      reason=f"timestamp older than backfill horizon ({horizon_days}d)")
    return ts


def _check_namespaces(
    cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_pk: int,
    event_type: str, payload: dict[str, Any], source_ref: str,
) -> None:
    """Declared format_regex makes bad id values fail fast (TF-EVT-004)."""
    for field, value in eventkey.id_fields(event_type, payload).items():
        cur.execute(
            """SELECT format_regex FROM identifier_namespaces
               WHERE workspace_id=%s AND identifier_field_type=%s AND record_status='active'
                 AND source_ref IN (%s, '')
                 AND (deployment_id=%s OR deployment_id IS NULL)
               ORDER BY (deployment_id IS NOT NULL) DESC, (source_ref<>'') DESC LIMIT 1""",
            (workspace_id, field, source_ref, deployment_pk),
        )
        row = cur.fetchone()
        if row and row["format_regex"]:
            if re.search(row["format_regex"], value) is None:
                raise _Reject("TF-EVT-004", field_path=f"payload.{field}",
                              reason=f"value {value!r} fails namespace format for {field}")


def _echo(event_type: str, payload: dict[str, Any], accepted: bool, reason: str | None) -> dict[str, Any]:
    echo: dict[str, Any] = {"accepted": accepted, "reason": reason,
                            "resolved_person": None, "resolution_basis": "unresolved",
                            "matched_work_item": payload.get("work_item_id")}
    if event_type == "activity":
        echo["action_class"] = payload.get("action_type")
    else:
        echo["interpreted_as"] = payload.get("meter") or payload.get("event") or payload.get("signal")
    return echo


@router.post("/v1/events:batch")
async def ingest_events(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise TFError("TF-EVT-007", detail="request body exceeds 1 MiB")
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise TFError("TF-EVT-003", detail="body is not valid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        raise TFError("TF-EVT-003", field_path="events", detail="events[] required")
    events: list[Any] = body["events"]
    if len(events) > _MAX_BATCH:
        raise TFError("TF-EVT-007", detail=f"batch exceeds {_MAX_BATCH} events")

    # Per-tenant rate limit + plan quota (batch-level, 429).
    from truefigure_sdk.platform.billing import ratelimit
    ws = db.fetch_one("SELECT rate_limit_rpm FROM workspaces WHERE id=%s", (principal.workspace_id,))
    ratelimit.check_rate(principal.workspace_id, int(ws["rate_limit_rpm"]) if ws else 600)
    ratelimit.check_quota(principal.workspace_id)

    source_ref = request.headers.get("X-TrueFigure-Source", "") or ""
    horizon = _backfill_horizon(principal.workspace_id)

    results: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        results.append(_process_one(principal, event, index, source_ref, horizon))
    return ok(request, {"results": results})


def _backfill_horizon(workspace_id: int) -> int:
    row = db.fetch_one("SELECT backfill_horizon_days FROM workspaces WHERE id=%s", (workspace_id,))
    return int(row["backfill_horizon_days"]) if row else 400


def _process_one(
    principal: Principal, event: Any, index: int, source_ref: str, horizon: int
) -> dict[str, Any]:
    is_test = principal.is_test
    try:
        if not isinstance(event, dict):
            raise _Reject("TF-EVT-003", field_path="(root)", reason="event must be an object")
        _scan_forbidden_fields(event)
        _validate_structure(event)
        event_type = event["event_type"]
        payload = event["payload"]
        ts = _check_timestamp(payload, horizon)
        scope = event.get("idempotency_scope")

        with db.transaction() as cur:
            dep = _resolve_dep(cur, principal, event["deployment_id"])
            _check_namespaces(cur, principal.workspace_id, int(dep["id"]), event_type, payload, source_ref)
            key = eventkey.event_key(event["deployment_id"], event_type, payload, scope)

            if is_test:
                # Test-mode key: same code path, but nothing is persisted (echo-vs-persist).
                return {"index": index, "status": "accepted", "event_key": key,
                        "echo": _echo(event_type, payload, True, None)}

            # Dedup: event_key determines occurred_at, so (workspace, key) presence = duplicate.
            cur.execute(
                "SELECT 1 FROM events WHERE workspace_id=%s AND event_key=%s LIMIT 1",
                (principal.workspace_id, key),
            )
            if cur.fetchone() is not None:
                # Duplicate is an HTTP-successful no-op counted as a duplicate (TF-EVT-005).
                return {"index": index, "status": "duplicate", "event_key": key, "code": "TF-EVT-005"}
            cur.execute(
                """INSERT INTO events (workspace_id, deployment_id, event_type, origin_type,
                       pipeline_status, event_key, schema_version, occurred_at, source_ref, payload,
                       created_by, updated_by)
                   VALUES (%s,%s,%s,%s,'accepted',%s,%s,%s,%s,%s,%s,%s)""",
                (principal.workspace_id, dep["id"], event_type, event["origin"], key,
                 event["schema_version"], ts, source_ref or None, json.dumps(payload),
                 SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
        return {"index": index, "status": "accepted", "event_key": key}

    except _Reject as rej:
        rkey = _safe_key(event)
        if not is_test:
            _quarantine(principal, event, rej, source_ref, rkey)
        res: dict[str, Any] = {"index": index, "status": "rejected", "error": rej.err.to_error_object()}
        if rkey:
            res["event_key"] = rkey
        if is_test and isinstance(event, dict) and isinstance(event.get("payload"), dict):
            res["echo"] = _echo(event.get("event_type", ""), event["payload"], False, rej.err.message)
        return res


def _resolve_dep(cur: psycopg.Cursor[dict[str, Any]], principal: Principal, wire_id: Any) -> dict[str, Any]:
    cur.execute(
        "SELECT id, workspace_id, deployment_status FROM deployments WHERE deployment_ref=%s AND workspace_id=%s",
        (wire_id, principal.workspace_id),
    )
    row = cur.fetchone()
    if row is None:
        raise _Reject("TF-EVT-002", field_path="deployment_id", reason=f"unknown deployment {wire_id}")
    principal.require_deployment(int(row["id"]))
    if row["deployment_status"] == "paused":
        # Ingestion is temporarily paused for this deployment (resume intended).
        raise TFError("TF-SRV-002", detail=f"ingestion paused for {wire_id}")
    return row


def _safe_key(event: Any) -> str | None:
    if not isinstance(event, dict):
        return None
    try:
        return eventkey.event_key(
            str(event.get("deployment_id", "")), str(event.get("event_type", "")),
            event.get("payload", {}) if isinstance(event.get("payload"), dict) else {},
            event.get("idempotency_scope"),
        )
    except (KeyError, eventkey.TimestampError, ValueError):
        return None


def _quarantine(principal: Principal, event: Any, rej: _Reject, source_ref: str, key: str | None) -> None:
    """Rejected events are quarantine, not data: no FK to config; deployment_ref as-sent."""
    ev = event if isinstance(event, dict) else {}
    request_id = _rid()
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO rejected_events (workspace_id, event_type, deployment_ref, event_key, error_code,
                   field_path, source_ref, request_id, created_by, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (principal.workspace_id, _maybe_enum(ev.get("event_type")), ev.get("deployment_id"),
             key, rej.err.code, rej.err.field_path, source_ref or None, request_id,
             SYSTEM_USER_ID, SYSTEM_USER_ID),
        )


def _maybe_enum(event_type: Any) -> str | None:
    valid = {"activity", "lifecycle", "cost_meter", "quality_signal", "revenue_signal"}
    return event_type if event_type in valid else None


def _rid() -> str:
    import uuid
    return "rq_" + uuid.uuid4().hex


@router.get("/v1/events/{event_key}/status")
async def event_status(event_key: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    row = db.fetch_one(
        """SELECT e.pipeline_status, e.exclusion_reason, u.user_ref AS resolved_ref, w.work_item_ref
           FROM events e
           LEFT JOIN users u ON u.id = e.resolved_user_id
           LEFT JOIN work_items w ON w.id = e.work_item_id
           WHERE e.workspace_id=%s AND e.event_key=%s
           ORDER BY e.occurred_at DESC LIMIT 1""",
        (principal.workspace_id, event_key),
    )
    if row is not None:
        status = row["pipeline_status"]
        return ok(request, {
            "event_key": event_key, "status": status,
            "reason": row["exclusion_reason"],
            "resolved_person": row["resolved_ref"],
            "joined_work_item": row["work_item_ref"],
        })
    rej = db.fetch_one(
        "SELECT error_code FROM rejected_events WHERE workspace_id=%s AND event_key=%s "
        "ORDER BY received_at DESC LIMIT 1",
        (principal.workspace_id, event_key),
    )
    if rej is not None:
        return ok(request, {"event_key": event_key, "status": "rejected", "reason": rej["error_code"],
                            "resolved_person": None, "joined_work_item": None})
    raise TFError("TF-READ-001", detail=f"event {event_key} not found")
