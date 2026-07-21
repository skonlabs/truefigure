"""Read plane — serves, never computes (docs/semantics.md).

figures/reports serve the latest finalized engine output; live/* are direct reads
and deterministic arithmetic over the aggregates; alerts are the durable monitor
queue with :ack. Seat classification uses the published thresholds (spec defaults).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Request

from truefigure_sdk.api.application_services.auth import Principal, require_principal
from truefigure_sdk.api.request_models.http import ok
from truefigure_sdk.errors import TFError
from truefigure_sdk.platform.database import db
from truefigure_sdk.platform.storage import storage

router = APIRouter()
SYSTEM_USER_ID = 1


def _dep(cur: psycopg.Cursor[dict[str, Any]], principal: Principal, wire_id: str) -> dict[str, Any]:
    cur.execute("SELECT id, deployment_ref FROM deployments WHERE deployment_ref=%s AND workspace_id=%s",
                (wire_id, principal.workspace_id))
    row = cur.fetchone()
    if row is None:
        raise TFError("TF-READ-001", detail=f"deployment {wire_id} not found")
    principal.require_deployment(int(row["id"]))
    return row


# ============================================================================
# figures
# ============================================================================
def _figure_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "figure_id": r["figure_ref"], "version": r["version"], "period": r["period"],
        "status": r["figure_status"], "grade": r["grade_type"], "value_class": r["value_class_type"],
        "claim": r["claim"], "value": float(r["value"]) if r["value"] is not None else None,
        "parameter_set_version": r["parameter_set_version"], "change_treatment": r["change_treatment"],
        "refusal_reason": r["refusal_reason"], "margin_exceeds_effect": r["margin_exceeds_effect"],
        "expected_verdict_date": r["expected_verdict_date"].isoformat() if r["expected_verdict_date"] else None,
        "missing_parameter": r["missing_parameter"],
        "method_id": r["method_id"], "method_version": r["method_version"],
        "computed_at": r["computed_at"].isoformat(),
    }


@router.get("/v1/figures/{deployment_id}")
async def list_figures(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        period = request.query_params.get("period")
        sql = ("SELECT DISTINCT ON (figure_ref) * FROM figures WHERE deployment_id=%s")
        params: list[Any] = [dep["id"]]
        if period:
            sql += " AND period=%s"
            params.append(period)
        sql += " ORDER BY figure_ref, version DESC"
        cur.execute(sql, params)
        rows = cur.fetchall()
    if period and not rows:
        # Period requested but nothing computed yet — first-class pre-engine state (TF-READ-002).
        return ok(request, {"results": [], "status": "period_not_computed", "code": "TF-READ-002"},
                  deployment_id=deployment_id)
    return ok(request, {"results": [_figure_dict(r) for r in rows]}, deployment_id=deployment_id)


@router.get("/v1/figures/{deployment_id}/{figure_id}")
async def get_figure(deployment_id: str, figure_id: str, request: Request,
                     principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        cur.execute("SELECT * FROM figures WHERE deployment_id=%s AND figure_ref=%s "
                    "ORDER BY version DESC LIMIT 1", (dep["id"], figure_id))
        row = cur.fetchone()
    if row is None:
        raise TFError("TF-READ-001", detail=f"figure {figure_id} not found")
    return ok(request, _figure_dict(row), deployment_id=deployment_id)


@router.get("/v1/figures/{deployment_id}/{figure_id}/lineage")
async def figure_lineage(deployment_id: str, figure_id: str, request: Request,
                         principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        cur.execute("SELECT figure_ref, version, method_id, method_version, parameter_set_version, "
                    "change_treatment, lineage FROM figures WHERE deployment_id=%s AND figure_ref=%s "
                    "ORDER BY version DESC LIMIT 1", (dep["id"], figure_id))
        row = cur.fetchone()
    if row is None:
        raise TFError("TF-READ-001", detail=f"figure {figure_id} not found")
    return ok(request, {"figure_id": row["figure_ref"], "version": row["version"],
                        "method_id": row["method_id"], "method_version": row["method_version"],
                        "parameter_set_version": row["parameter_set_version"],
                        "change_treatment": row["change_treatment"], "lineage": row["lineage"]},
              deployment_id=deployment_id)


# ============================================================================
# reports
# ============================================================================
@router.get("/v1/reports/{deployment_id}")
async def list_reports(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        cur.execute("SELECT report_ref, version, period, issued_at, grade_profile, supersedes_version "
                    "FROM reports WHERE deployment_id=%s ORDER BY issued_at DESC, version DESC", (dep["id"],))
        rows = cur.fetchall()
    items = [{"report_id": r["report_ref"], "version": r["version"], "period": r["period"],
              "issued_at": r["issued_at"].isoformat(), "grade_profile": r["grade_profile"],
              "supersedes_version": r["supersedes_version"]} for r in rows]
    return ok(request, {"results": items}, deployment_id=deployment_id)


@router.get("/v1/reports/{deployment_id}/{report_id}")
async def get_report(deployment_id: str, report_id: str, request: Request,
                     principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        cur.execute("SELECT id, report_ref, version, period, issued_at, grade_profile, artifact_path, "
                    "supersedes_version FROM reports WHERE deployment_id=%s AND report_ref=%s "
                    "ORDER BY version DESC LIMIT 1", (dep["id"], report_id))
        rep = cur.fetchone()
        if rep is None:
            raise TFError("TF-READ-001", detail=f"report {report_id} not found")
        cur.execute("SELECT f.figure_ref, f.version FROM report_figures rf "
                    "JOIN figures f ON f.id=rf.figure_id WHERE rf.report_id=%s", (rep["id"],))
        bound = [{"figure_id": r["figure_ref"], "version": r["version"]} for r in cur.fetchall()]
    artifact_url = storage.get_storage().signed_download_url("report-artifacts", rep["artifact_path"])
    return ok(request, {"report_id": rep["report_ref"], "version": rep["version"], "period": rep["period"],
                        "issued_at": rep["issued_at"].isoformat(), "grade_profile": rep["grade_profile"],
                        "supersedes_version": rep["supersedes_version"], "artifact_url": artifact_url,
                        "figures": bound}, deployment_id=deployment_id)


# ============================================================================
# live/usage — seat classification (published thresholds)
# ============================================================================
@router.get("/v1/live/usage/{deployment_id}")
async def live_usage(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        ref = datetime.now(UTC).date()
        window_start = ref - timedelta(days=29)
        cur.execute(
            """SELECT user_id, COUNT(DISTINCT activity_date) AS days, SUM(event_count) AS events
               FROM user_activity_daily WHERE deployment_id=%s AND activity_date BETWEEN %s AND %s
               GROUP BY user_id""",
            (dep["id"], window_start, ref),
        )
        active = cur.fetchall()
        near_zero = moderate = heavy = 0
        for u in active:
            days, events = int(u["days"]), int(u["events"])
            if days < 3 or events < 5:
                near_zero += 1
            elif days >= 15 and events >= 100:
                heavy += 1
            else:
                moderate += 1
        activated = len(active)
        cur.execute("SELECT seats_paid, price_per_seat, period_unit_type FROM deployment_licenses "
                    "WHERE deployment_id=%s AND valid_to IS NULL", (dep["id"],))
        lic = cur.fetchone()

    # k-anonymity floor (BR-012): a non-empty cohort below k_report keeps its aggregate
    # totals but its near_zero/moderate/heavy SPLIT is suppressed (a <k split could
    # identify individuals) -> cohort_status not_disclosable (TF-READ-003).
    if 0 < activated < 5:
        seats_floor: dict[str, Any] = {"activated": activated}
        if lic is not None:
            seats_floor["paid"] = int(lic["seats_paid"])
            seats_floor["never_activated"] = max(int(lic["seats_paid"]) - activated, 0)
        return ok(request, {"grade": "measured", "seats": seats_floor,
                            "cohort_status": "not_disclosable", "code": "TF-READ-003"},
                  deployment_id=deployment_id)

    data: dict[str, Any] = {"grade": "measured",
                            "seats": {"activated": activated, "near_zero": near_zero,
                                      "moderate": moderate, "heavy": heavy}}
    if lic is None:
        data["seats"]["paid"] = None
        data["seats"]["never_activated"] = None
        data["waste"] = {"seats": None, "annual_usd": None, "status": "awaiting_license_declaration"}
        return ok(request, data, deployment_id=deployment_id)
    paid = int(lic["seats_paid"])
    never = max(paid - activated, 0)
    data["seats"]["paid"] = paid
    data["seats"]["never_activated"] = never
    price = lic["price_per_seat"]
    if price is not None:
        annual = float(price) * never * (12 if lic["period_unit_type"] == "month" else 1)
        data["waste"] = {"seats": never, "annual_usd": annual, "price_provenance": "license_data"}
    else:
        data["waste"] = {"seats": never, "annual_usd": None, "status": "awaiting_parameters"}
    return ok(request, data, deployment_id=deployment_id)


# ============================================================================
# live/cost — per-meter priced consumption
# ============================================================================
@router.get("/v1/live/cost/{deployment_id}")
async def live_cost(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    period = request.query_params.get("period") or datetime.now(UTC).strftime("%Y-%m")
    from truefigure_sdk.domain.entities import periods

    start, end = periods.period_bounds(period)
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        pv, payload = _param_for(cur, principal.workspace_id, start)
        unit_prices = payload.get("unit_prices", {})
        cur.execute(
            """SELECT meter_type,
                      CASE WHEN meter_type IN ('seats_active','storage_gb') THEN AVG(gauge_last)
                           ELSE SUM(quantity_sum) END AS qty
               FROM meter_usage_daily WHERE deployment_id=%s AND bucket_date BETWEEN %s AND %s
               GROUP BY meter_type ORDER BY meter_type""",
            (dep["id"], start, end),
        )
        meters = []
        ptd = 0.0
        for r in cur.fetchall():
            meter = r["meter_type"]
            qty = float(r["qty"] or 0)
            price = unit_prices.get(meter)
            if price is None:
                meters.append({"meter": meter, "quantity": qty, "usd": None,
                               "status": "awaiting_parameters", "missing": f"unit_prices.{meter}"})
            else:
                usd = qty * float(price)
                ptd += usd
                meters.append({"meter": meter, "quantity": qty, "usd": round(usd, 6)})
    return ok(request, {"period": period, "parameter_set_version": pv, "meters": meters,
                        "period_to_date_usd": round(ptd, 6),
                        "projection": {"end_of_cycle_usd": round(ptd, 6), "basis": "period-to-date"}},
              deployment_id=deployment_id)


def _param_for(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, start: Any) -> tuple[int | None, dict[str, Any]]:
    cur.execute("SELECT version, payload FROM parameter_sets WHERE workspace_id=%s AND effective_from<=%s "
                "ORDER BY effective_from DESC, version DESC LIMIT 1", (workspace_id, start))
    row = cur.fetchone()
    return (int(row["version"]), row["payload"]) if row else (None, {})


# ============================================================================
# live/health — integration counters
# ============================================================================
@router.get("/v1/live/health/{deployment_id}")
async def live_health(deployment_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        dep = _dep(cur, principal, deployment_id)
        cur.execute(
            """SELECT COALESCE(SUM(accepted_count),0) AS accepted,
                      COALESCE(SUM(identity_bearing_count),0) AS id_bearing,
                      COALESCE(SUM(resolved_count),0) AS resolved,
                      COALESCE(SUM(activity_count),0) AS activity,
                      COALESCE(SUM(joined_count),0) AS joined,
                      MAX(last_event_at) AS last_event
               FROM ingestion_stats_daily WHERE deployment_id=%s""",
            (dep["id"],),
        )
        s = cur.fetchone() or {}
        cur.execute(
            "SELECT alert_code_type, detail FROM alerts WHERE deployment_id=%s AND alert_status='open'",
            (dep["id"],))
        warnings = [{"code": a["alert_code_type"], "detail": a["detail"]} for a in cur.fetchall()]

    id_bearing = int(s.get("id_bearing", 0) or 0)
    activity = int(s.get("activity", 0) or 0)
    resolution_rate = (int(s["resolved"]) / id_bearing) if id_bearing else None
    join_rate = (int(s["joined"]) / activity) if activity else None
    last_event = s.get("last_event")
    return ok(request, {
        "accepted_count": int(s.get("accepted", 0) or 0),
        "identity_resolution_rate": round(resolution_rate, 4) if resolution_rate is not None else None,
        "work_item_join_rate": round(join_rate, 4) if join_rate is not None else None,
        "last_event_at": last_event.isoformat() if last_event else None,
        "drift_warnings": warnings,
    }, deployment_id=deployment_id)


# ============================================================================
# alerts
# ============================================================================
@router.get("/v1/alerts")
async def list_alerts(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    status = request.query_params.get("status")
    sql = ("SELECT a.alert_ref, a.alert_code_type, a.alert_status, a.detail, a.first_seen, a.last_seen, "
           "a.acked_at, d.deployment_ref FROM alerts a LEFT JOIN deployments d ON d.id=a.deployment_id "
           "WHERE a.workspace_id=%s")
    params: list[Any] = [principal.workspace_id]
    if status:
        sql += " AND a.alert_status=%s"
        params.append(status)
    sql += " ORDER BY a.last_seen DESC"
    rows = db.fetch_all(sql, params)
    items = [{"alert_id": r["alert_ref"], "code": r["alert_code_type"], "status": r["alert_status"],
              "detail": r["detail"], "deployment_id": r["deployment_ref"],
              "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat(),
              "acked_at": r["acked_at"].isoformat() if r["acked_at"] else None} for r in rows]
    return ok(request, {"results": items})


@router.post("/v1/alerts/{alert_id}:ack")
async def ack_alert(alert_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    with db.transaction() as cur:
        cur.execute(
            "UPDATE alerts SET alert_status='acked', acked_by=%s, acked_at=now(), updated_by=%s "
            "WHERE alert_ref=%s AND workspace_id=%s AND alert_status='open' RETURNING alert_ref",
            (principal.owner_user_id, SYSTEM_USER_ID, alert_id, principal.workspace_id),
        )
        row = cur.fetchone()
    if row is None:
        raise TFError("TF-READ-001", detail=f"open alert {alert_id} not found")
    return ok(request, {"alert_id": alert_id, "status": "acked"})
