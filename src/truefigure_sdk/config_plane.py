"""Config plane — deployments, roster, licenses, id-namespaces, parameters,
qa-label schemas, webhooks, change-events, mapping-contracts.

The API speaks WIRE names; every write stores COLUMN names via the wire map.
Idempotency, immutability, historization, and role gates follow the spec.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from . import db, refs, wire
from .auth import Principal, require_principal
from .errors import TFError
from .http import ok, parse_body

router = APIRouter()
SYSTEM_USER_ID = 1


# ---- helpers ----------------------------------------------------------------
def _forbid() -> ConfigDict:
    return ConfigDict(extra="forbid")


def _resolve_deployment(cur: psycopg.Cursor[dict[str, Any]], principal: Principal, wire_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT id, workspace_id, deployment_type FROM deployments "
        "WHERE deployment_ref=%s AND workspace_id=%s",
        (wire_id, principal.workspace_id),
    )
    row = cur.fetchone()
    if row is None:
        raise TFError("TF-EVT-002", detail=f"unknown deployment {wire_id}")
    principal.require_deployment(int(row["id"]))
    return row


def _audit() -> tuple[int, int]:
    return SYSTEM_USER_ID, SYSTEM_USER_ID


# ============================================================================
# deployments
# ============================================================================
class DeploymentCreate(BaseModel):
    model_config = _forbid()
    name: str
    type: str = Field(pattern="^(saas_tool|in_house|agent|decision_ai)$")
    external_ref: str | None = None
    planned_rollout_at: str | None = None
    service_user_refs: list[str] = Field(default_factory=list)


@router.post("/v1/deployments")
async def register_deployment(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(DeploymentCreate, await request.json())
    mode = "planned" if body.planned_rollout_at else "measured"
    with db.transaction() as cur:
        # Idempotency by external_ref (TF-CFG-003 = already applied -> return existing).
        if body.external_ref:
            cur.execute(
                "SELECT deployment_ref FROM deployments WHERE workspace_id=%s AND external_ref=%s",
                (principal.workspace_id, body.external_ref),
            )
            existing = cur.fetchone()
            if existing:
                return ok(request, {"deployment_id": existing["deployment_ref"], "idempotent": True}, status=200)

        dep_ref = refs.new_ref("deployment")
        cur.execute(
            """INSERT INTO deployments (workspace_id, deployment_type, deployment_mode, deployment_status,
                   deployment_ref, name, external_ref, planned_rollout_at, created_by, updated_by)
               VALUES (%s,%s,%s,'active',%s,%s,%s,%s,%s,%s) RETURNING id, deployment_ref""",
            (principal.workspace_id, body.type, mode, dep_ref, body.name, body.external_ref,
             body.planned_rollout_at, *_audit()),
        )
        dep = cur.fetchone()
        assert dep is not None
        # G6: link declared service accounts (must be roster kind=service in this workspace).
        for sref in body.service_user_refs:
            cur.execute(
                "SELECT id FROM users WHERE workspace_id=%s AND user_ref=%s AND user_type='service'",
                (principal.workspace_id, sref),
            )
            u = cur.fetchone()
            if u is None:
                raise TFError("TF-CFG-007", field_path="service_user_refs",
                              detail=f"{sref} is not a roster service account")
            cur.execute(
                """INSERT INTO deployment_service_users (workspace_id, deployment_id, user_id, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s)""",
                (principal.workspace_id, dep["id"], u["id"], *_audit()),
            )
    return ok(request, {"deployment_id": dep["deployment_ref"], "type": body.type, "mode": mode}, status=201)


@router.get("/v1/deployments")
async def list_deployments(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT deployment_ref, deployment_type, deployment_mode, deployment_status, name, external_ref "
        "FROM deployments WHERE workspace_id=%s ORDER BY id",
        (principal.workspace_id,),
    )
    items = [
        {"deployment_id": r["deployment_ref"], "type": r["deployment_type"], "mode": r["deployment_mode"],
         "status": r["deployment_status"], "name": r["name"], "external_ref": r["external_ref"]}
        for r in rows
    ]
    return ok(request, {"results": items})


@router.get("/v1/deployments/{deployment_id}")
async def get_deployment(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _resolve_deployment(cur, principal, deployment_id)
        cur.execute(
            "SELECT deployment_ref, deployment_type, deployment_mode, deployment_status, name, external_ref, "
            "planned_rollout_at FROM deployments WHERE id=%s", (dep["id"],))
        r = cur.fetchone()
        assert r is not None
    return ok(request, {
        "deployment_id": r["deployment_ref"], "type": r["deployment_type"], "mode": r["deployment_mode"],
        "status": r["deployment_status"], "name": r["name"], "external_ref": r["external_ref"],
        "planned_rollout_at": r["planned_rollout_at"].isoformat() if r["planned_rollout_at"] else None,
    }, deployment_id=r["deployment_ref"])


class DeploymentUpdate(BaseModel):
    model_config = _forbid()
    name: str | None = None
    planned_rollout_at: str | None = None
    type: str | None = None  # present only to reject it (TF-CFG-006)


@router.patch("/v1/deployments/{deployment_id}")
async def update_deployment(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(DeploymentUpdate, await request.json())
    with db.transaction() as cur:
        dep = _resolve_deployment(cur, principal, deployment_id)
        if body.type is not None and body.type != dep["deployment_type"]:
            # type is immutable once events exist.
            cur.execute("SELECT 1 FROM events WHERE deployment_id=%s LIMIT 1", (dep["id"],))
            if cur.fetchone() is not None:
                raise TFError("TF-CFG-006", field_path="type", detail="deployment_type immutable once events exist")
        sets: list[str] = []
        params: list[Any] = []
        if body.name is not None:
            sets.append("name=%s")
            params.append(body.name)
        if body.planned_rollout_at is not None:
            sets.append("planned_rollout_at=%s")
            params.append(body.planned_rollout_at)
        if sets:
            sets.append("updated_by=%s")
            params.append(SYSTEM_USER_ID)
            params.append(dep["id"])
            cur.execute(f"UPDATE deployments SET {', '.join(sets)} WHERE id=%s", params)
    return ok(request, {"deployment_id": deployment_id, "updated": bool(body.name or body.planned_rollout_at)})


# ============================================================================
# roster
# ============================================================================
class RosterIdentifier(BaseModel):
    model_config = _forbid()
    namespace: str
    value: str


class RosterUser(BaseModel):
    model_config = _forbid()
    user_ref: str
    kind: str = "person"
    role: str | None = None
    team: str | None = None
    display_name: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    identifiers: list[RosterIdentifier] = Field(default_factory=list)


class RosterBatch(BaseModel):
    model_config = _forbid()
    users: list[RosterUser]


@router.post("/v1/roster:batch")
async def upsert_roster(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(RosterBatch, await request.json())
    results: list[dict[str, Any]] = []
    for item in body.users:
        try:
            with db.transaction() as cur:
                user_type = wire.kind_to_user_type(item.kind)  # person -> user
                cur.execute(
                    """INSERT INTO users (workspace_id, user_type, user_status, user_ref, display_name,
                           job_role, team, effective_from, effective_to, created_by, updated_by)
                       VALUES (%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (workspace_id, user_ref) WHERE workspace_id IS NOT NULL
                       DO UPDATE SET user_type=EXCLUDED.user_type, display_name=EXCLUDED.display_name,
                           job_role=EXCLUDED.job_role, team=EXCLUDED.team,
                           effective_from=EXCLUDED.effective_from, effective_to=EXCLUDED.effective_to,
                           updated_by=EXCLUDED.updated_by
                       RETURNING id""",
                    (principal.workspace_id, user_type, item.user_ref, item.display_name, item.role,
                     item.team, item.effective_from, item.effective_to, *_audit()),
                )
                urow = cur.fetchone()
                assert urow is not None
                for ident in item.identifiers:
                    # An identifier value belongs to at most one active person per namespace (TF-CFG-004).
                    cur.execute(
                        "SELECT user_id FROM user_identifiers WHERE workspace_id=%s AND namespace=%s "
                        "AND identifier_value=%s AND record_status='active'",
                        (principal.workspace_id, ident.namespace, ident.value),
                    )
                    owner = cur.fetchone()
                    if owner and int(owner["user_id"]) != int(urow["id"]):
                        raise TFError("TF-CFG-004", field_path="identifiers",
                                      detail=f"{ident.namespace}:{ident.value} owned by another user")
                    cur.execute(
                        """INSERT INTO user_identifiers (user_id, workspace_id, record_status, namespace,
                               identifier_value, created_by, updated_by)
                           VALUES (%s,%s,'active',%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (urow["id"], principal.workspace_id, ident.namespace, ident.value, *_audit()),
                    )
            results.append({"user_ref": item.user_ref, "status": "upserted"})
        except TFError as exc:
            results.append({"user_ref": item.user_ref, "status": "rejected",
                            "error": exc.to_error_object()})
    return ok(request, {"results": results})


@router.get("/v1/roster")
async def list_roster(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    status = request.query_params.get("status")
    kind = request.query_params.get("kind")
    sql = ("SELECT u.user_ref, u.user_type, u.user_status, u.job_role, u.team, u.effective_from, u.effective_to "
           "FROM users u WHERE u.workspace_id=%s")
    params: list[Any] = [principal.workspace_id]
    if status:
        sql += " AND u.user_status=%s"
        params.append(status)
    if kind:
        sql += " AND u.user_type=%s"
        params.append(wire.kind_to_user_type(kind))
    sql += " ORDER BY u.id"
    rows = db.fetch_all(sql, params)
    items = [
        {"user_ref": r["user_ref"], "kind": wire.user_type_to_kind(r["user_type"]), "status": r["user_status"],
         "role": r["job_role"], "team": r["team"],
         "effective_from": r["effective_from"].isoformat() if r["effective_from"] else None,
         "effective_to": r["effective_to"].isoformat() if r["effective_to"] else None}
        for r in rows
    ]
    return ok(request, {"results": items})


# ============================================================================
# licenses (historized)
# ============================================================================
class LicenseDeclare(BaseModel):
    model_config = _forbid()
    seats_paid: int = Field(ge=0)
    price_per_seat: float | None = None
    currency: str = "USD"
    period_unit: str = Field(default="year", pattern="^(month|year)$")
    valid_from: str
    licensed_user_refs: list[str] = Field(default_factory=list)


@router.put("/v1/deployments/{deployment_id}/license")
async def declare_license(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(LicenseDeclare, await request.json())
    with db.transaction() as cur:
        dep = _resolve_deployment(cur, principal, deployment_id)
        # Close the current row (valid_to IS NULL) at the new window's start.
        cur.execute(
            "UPDATE deployment_licenses SET valid_to=%s, updated_by=%s "
            "WHERE deployment_id=%s AND valid_to IS NULL",
            (body.valid_from, SYSTEM_USER_ID, dep["id"]),
        )
        cur.execute(
            """INSERT INTO deployment_licenses (deployment_id, period_unit_type, seats_paid, price_per_seat,
                   currency, valid_from, created_by, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (dep["id"], body.period_unit, body.seats_paid, body.price_per_seat, body.currency,
             body.valid_from, *_audit()),
        )
        lic = cur.fetchone()
        assert lic is not None
        for ref in body.licensed_user_refs:
            cur.execute(
                """INSERT INTO deployment_license_identifiers (deployment_license_id, licensed_identifier,
                       created_by, updated_by) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (lic["id"], ref, *_audit()),
            )
    return ok(request, {"license_id": refs.encode_id("license", int(lic["id"])),
                        "deployment_id": deployment_id, "seats_paid": body.seats_paid}, status=200)


@router.get("/v1/deployments/{deployment_id}/license")
async def get_license(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _resolve_deployment(cur, principal, deployment_id)
        cur.execute(
            "SELECT id, period_unit_type, seats_paid, price_per_seat, currency, valid_from "
            "FROM deployment_licenses WHERE deployment_id=%s AND valid_to IS NULL", (dep["id"],))
        r = cur.fetchone()
    if r is None:
        return ok(request, None, deployment_id=deployment_id)
    return ok(request, {
        "license_id": refs.encode_id("license", int(r["id"])), "seats_paid": r["seats_paid"],
        "price_per_seat": float(r["price_per_seat"]) if r["price_per_seat"] is not None else None,
        "currency": r["currency"], "period_unit": r["period_unit_type"],
        "valid_from": r["valid_from"].isoformat(),
    }, deployment_id=deployment_id)


# ============================================================================
# id-namespaces
# ============================================================================
class IdNamespaceCreate(BaseModel):
    model_config = _forbid()
    field: str = Field(pattern="^(user_ref|work_item_id|assignee_ref)$")
    namespace: str
    deployment_id: str | None = None
    source_ref: str = ""
    format_regex: str | None = None
    description: str | None = None


@router.post("/v1/id-namespaces")
async def register_id_namespace(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(IdNamespaceCreate, await request.json())
    with db.transaction() as cur:
        dep_id = None
        if body.deployment_id:
            dep_id = int(_resolve_deployment(cur, principal, body.deployment_id)["id"])
        # One active declaration per (scope, field, source) -> TF-CFG-004.
        cur.execute(
            "SELECT 1 FROM identifier_namespaces WHERE workspace_id=%s AND identifier_field_type=%s "
            "AND source_ref=%s AND record_status='active' AND deployment_id IS NOT DISTINCT FROM %s",
            (principal.workspace_id, body.field, body.source_ref, dep_id),
        )
        if cur.fetchone() is not None:
            raise TFError("TF-CFG-004", field_path="field", detail="active namespace already declared for scope")
        cur.execute(
            """INSERT INTO identifier_namespaces (workspace_id, deployment_id, identifier_field_type,
                   record_status, source_ref, namespace, format_regex, description, created_by, updated_by)
               VALUES (%s,%s,%s,'active',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (principal.workspace_id, dep_id, body.field, body.source_ref, body.namespace,
             body.format_regex, body.description, *_audit()),
        )
        ns = cur.fetchone()
        assert ns is not None
    return ok(request, {"namespace_id": refs.encode_id("namespace", int(ns["id"])),
                        "field": body.field, "namespace": body.namespace,
                        "deployment_id": body.deployment_id}, status=201)


@router.get("/v1/id-namespaces")
async def list_id_namespaces(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT n.id, n.identifier_field_type, n.namespace, n.source_ref, n.record_status, d.deployment_ref "
        "FROM identifier_namespaces n LEFT JOIN deployments d ON d.id=n.deployment_id "
        "WHERE n.workspace_id=%s ORDER BY n.id", (principal.workspace_id,))
    items = [
        {"namespace_id": refs.encode_id("namespace", int(r["id"])), "field": r["identifier_field_type"],
         "namespace": r["namespace"], "source_ref": r["source_ref"], "status": r["record_status"],
         "deployment_id": r["deployment_ref"]}
        for r in rows
    ]
    return ok(request, {"results": items})


# ============================================================================
# parameters (immutable versions, finance_params role, overlap-free)
# ============================================================================
class ParameterCreate(BaseModel):
    model_config = _forbid()
    effective_from: str
    currency: str = "USD"
    payload: dict[str, Any]


@router.post("/v1/parameters")
async def create_parameter_version(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    if "finance_params" not in principal.roles:
        raise TFError("TF-CFG-002", detail="finance_params role required (BR-016)")
    body = parse_body(ParameterCreate, await request.json())
    with db.transaction() as cur:
        cur.execute(
            "SELECT 1 FROM parameter_sets WHERE workspace_id=%s AND effective_from=%s",
            (principal.workspace_id, body.effective_from))
        if cur.fetchone() is not None:
            raise TFError("TF-CFG-001", field_path="effective_from", detail="effective window overlaps existing version")
        cur.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM parameter_sets WHERE workspace_id=%s",
                    (principal.workspace_id,))
        vrow = cur.fetchone()
        assert vrow is not None
        version = int(vrow["v"])
        import json as _json
        cur.execute(
            """INSERT INTO parameter_sets (workspace_id, version, effective_from, currency, payload,
                   created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING version""",
            (principal.workspace_id, version, body.effective_from, body.currency,
             _json.dumps(body.payload), *_audit()),
        )
    return ok(request, {"version": version, "effective_from": body.effective_from,
                        "currency": body.currency}, status=201)


@router.get("/v1/parameters")
async def list_parameter_versions(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT version, effective_from, currency FROM parameter_sets WHERE workspace_id=%s ORDER BY version",
        (principal.workspace_id,))
    items = [{"version": r["version"], "effective_from": r["effective_from"].isoformat(),
              "currency": r["currency"]} for r in rows]
    return ok(request, {"results": items})


@router.get("/v1/parameters/{version}")
async def get_parameter_version(version: int, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    r = db.fetch_one(
        "SELECT version, effective_from, currency, payload FROM parameter_sets WHERE workspace_id=%s AND version=%s",
        (principal.workspace_id, version))
    if r is None:
        raise TFError("TF-READ-001", detail=f"parameter version {version} not found")
    return ok(request, {"version": r["version"], "effective_from": r["effective_from"].isoformat(),
                        "currency": r["currency"], "payload": r["payload"]})


# ============================================================================
# qa-label schemas
# ============================================================================
class LabelSchemaCreate(BaseModel):
    model_config = _forbid()
    label_schema_ref: str
    name: str
    label_values: list[str] = Field(min_length=2, max_length=20)
    applies_to_category: str | None = None
    min_samples_for_calibration: int = 200


@router.post("/v1/qa-labels/schemas")
async def register_label_schema(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(LabelSchemaCreate, await request.json())
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO qa_label_definitions (workspace_id, record_status, grader_status, label_schema_ref,
                   name, label_values, applies_to_category, min_samples_for_calibration, created_by, updated_by)
               VALUES (%s,'active','uncalibrated',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (principal.workspace_id, body.label_schema_ref, body.name, body.label_values,
             body.applies_to_category, body.min_samples_for_calibration, *_audit()),
        )
    return ok(request, {"label_schema_ref": body.label_schema_ref, "grader_status": "uncalibrated"}, status=201)


@router.get("/v1/qa-labels/schemas")
async def list_label_schemas(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT label_schema_ref, name, label_values, grader_status, applies_to_category "
        "FROM qa_label_definitions WHERE workspace_id=%s ORDER BY id", (principal.workspace_id,))
    items = [{"label_schema_ref": r["label_schema_ref"], "name": r["name"], "label_values": r["label_values"],
              "grader_status": r["grader_status"], "applies_to_category": r["applies_to_category"]} for r in rows]
    return ok(request, {"results": items})


# ============================================================================
# webhooks (challenge verification state machine)
# ============================================================================
class WebhookCreate(BaseModel):
    model_config = _forbid()
    url: str
    events: list[str] = Field(min_length=1)
    secret: str


@router.post("/v1/webhooks")
async def create_webhook(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(WebhookCreate, await request.json())
    valid = {"figure.updated", "refusal.lifted", "alert.raised", "report.issued"}
    for ev in body.events:
        if ev not in valid:
            raise TFError("TF-CFG-007", field_path="events", detail=f"unknown event type {ev}")
    from . import secretbox
    with db.transaction() as cur:
        # Idempotent by (workspace, url, event set): return existing if present.
        cur.execute(
            "SELECT webhook_ref FROM webhooks WHERE workspace_id=%s AND url=%s AND webhook_event_type=%s::webhook_event_type[]",
            (principal.workspace_id, body.url, body.events),
        )
        existing = cur.fetchone()
        if existing:
            return ok(request, {"webhook_id": existing["webhook_ref"], "idempotent": True}, status=200)
        wh_ref = refs.new_ref("webhook")
        cur.execute(
            """INSERT INTO webhooks (workspace_id, webhook_status, webhook_event_type, webhook_ref, url,
                   secret_hash, created_by, updated_by)
               VALUES (%s,'unverified',%s::webhook_event_type[],%s,%s,%s,%s,%s) RETURNING webhook_ref""",
            (principal.workspace_id, body.events, wh_ref, body.url, secretbox.encrypt(body.secret), *_audit()),
        )
        w = cur.fetchone()
        assert w is not None
    return ok(request, {"webhook_id": w["webhook_ref"], "status": "unverified"}, status=201)


@router.get("/v1/webhooks")
async def list_webhooks(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT webhook_ref, url, webhook_event_type::text[] AS webhook_event_type, webhook_status FROM webhooks "
        "WHERE workspace_id=%s AND webhook_status<>'deleted' ORDER BY id", (principal.workspace_id,))
    items = [{"webhook_id": r["webhook_ref"], "url": r["url"], "events": r["webhook_event_type"],
              "status": r["webhook_status"]} for r in rows]
    return ok(request, {"results": items})


@router.delete("/v1/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        cur.execute(
            "UPDATE webhooks SET webhook_status='deleted', updated_by=%s "
            "WHERE webhook_ref=%s AND workspace_id=%s AND webhook_status<>'deleted' RETURNING id",
            (SYSTEM_USER_ID, webhook_id, principal.workspace_id))
        if cur.fetchone() is None:
            raise TFError("TF-READ-001", detail="webhook not found")
    return ok(request, {"webhook_id": webhook_id, "status": "deleted"})


# ============================================================================
# change-events (supersedes sets superseded_by_id ON THE OLD ROW)
# ============================================================================
class ChangeEventCreate(BaseModel):
    model_config = _forbid()
    type: str = Field(pattern="^(system_cutover|process_change|seasonal|staffing|regulatory|tool_change|other)$")
    sidedness: str = Field(pattern="^(symmetric|one_sided)$")
    occurred_at: str
    description: str
    deployment_refs: list[str] = Field(default_factory=list)
    supersedes: str | None = None


@router.post("/v1/change-events")
async def declare_change_event(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(ChangeEventCreate, await request.json())
    with db.transaction() as cur:
        ce_ref = refs.new_ref("change_event")
        cur.execute(
            """INSERT INTO change_log (workspace_id, change_type, sided_type, change_event_ref, occurred_at,
                   description, created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (principal.workspace_id, body.type, body.sidedness, ce_ref, body.occurred_at,
             body.description, *_audit()),
        )
        new_row = cur.fetchone()
        assert new_row is not None
        # supersedes: set superseded_by_id ON THE OLD ROW to this new correction.
        if body.supersedes:
            cur.execute(
                "UPDATE change_log SET superseded_by_id=%s, updated_by=%s "
                "WHERE change_event_ref=%s AND workspace_id=%s RETURNING id",
                (new_row["id"], SYSTEM_USER_ID, body.supersedes, principal.workspace_id))
            if cur.fetchone() is None:
                raise TFError("TF-CFG-007", field_path="supersedes", detail="superseded change-event not found")
        for dref in body.deployment_refs:
            dep = _resolve_deployment(cur, principal, dref)
            cur.execute(
                """INSERT INTO change_log_deployments (workspace_id, change_log_id, deployment_id, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s)""",
                (principal.workspace_id, new_row["id"], dep["id"], *_audit()),
            )
    return ok(request, {"change_event_ref": ce_ref, "type": body.type, "sidedness": body.sidedness}, status=201)


@router.get("/v1/change-events")
async def list_change_events(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT change_event_ref, change_type, sided_type, occurred_at, description, superseded_by_id "
        "FROM change_log WHERE workspace_id=%s ORDER BY id", (principal.workspace_id,))
    items = [{"change_event_ref": r["change_event_ref"], "type": r["change_type"], "sidedness": r["sided_type"],
              "occurred_at": r["occurred_at"].isoformat(), "description": r["description"],
              "superseded": r["superseded_by_id"] is not None} for r in rows]
    return ok(request, {"results": items})


# ============================================================================
# mapping-contracts (immutable per (workspace, source_ref, version))
# ============================================================================
class MappingContractCreate(BaseModel):
    model_config = _forbid()
    source_ref: str
    event_type: str = Field(pattern="^(activity|lifecycle|cost_meter|quality_signal|revenue_signal)$")
    version: int = Field(ge=1)
    field_map: dict[str, Any]
    notes: str | None = None


@router.post("/v1/mapping-contracts")
async def create_mapping_contract(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(MappingContractCreate, await request.json())
    import json as _json
    with db.transaction() as cur:
        cur.execute(
            "SELECT 1 FROM mapping_contracts WHERE workspace_id=%s AND source_ref=%s AND version=%s",
            (principal.workspace_id, body.source_ref, body.version))
        if cur.fetchone() is not None:
            raise TFError("TF-CFG-006", field_path="version", detail="mapping contract version is immutable")
        cur.execute(
            """INSERT INTO mapping_contracts (workspace_id, event_type, source_ref, version, field_map, notes,
                   created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (principal.workspace_id, body.event_type, body.source_ref, body.version,
             _json.dumps(body.field_map), body.notes, *_audit()),
        )
        mc = cur.fetchone()
        assert mc is not None
    return ok(request, {"mapping_contract_id": refs.encode_id("mapping_contract", int(mc["id"])),
                        "source_ref": body.source_ref, "version": body.version}, status=201)


@router.get("/v1/mapping-contracts")
async def list_mapping_contracts(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    rows = db.fetch_all(
        "SELECT id, source_ref, event_type, version, field_map FROM mapping_contracts "
        "WHERE workspace_id=%s ORDER BY id", (principal.workspace_id,))
    items = [{"mapping_contract_id": refs.encode_id("mapping_contract", int(r["id"])),
              "source_ref": r["source_ref"], "event_type": r["event_type"], "version": r["version"],
              "field_map": r["field_map"]} for r in rows]
    return ok(request, {"results": items})
