"""Reference measurement engine — writes Figure records the read plane serves.

Deterministic core (no AI model in any final figure's computation path, BR-007).
Every figure carries grade + method id/version + parameter_set_version stamped
for the PERIOD + change_treatment + lineage references (BR-004). Refusal and
awaiting-parameters are first-class PERSISTED states, never errors (BR-005/016).

Methods implemented: priced cost, seat utilization/waste, containment (agents,
with G6 corroboration), time-savings vs baseline with change treatment. Report
issuance binds exact figure versions and renders an artifact to Storage.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from . import db, periods, refs, storage, webhooks_delivery

SYSTEM_USER_ID = 1
METHOD_VERSION = "1.0.0"


# ---- parameter resolution (stamped for the period) --------------------------
def resolve_parameter_version(
    cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, period: str
) -> tuple[int | None, dict[str, Any]]:
    start, _ = periods.period_bounds(period)
    cur.execute(
        "SELECT version, payload FROM parameter_sets WHERE workspace_id=%s AND effective_from <= %s "
        "ORDER BY effective_from DESC, version DESC LIMIT 1",
        (workspace_id, start),
    )
    row = cur.fetchone()
    if row is None:
        return None, {}
    return int(row["version"]), row["payload"]


# ---- figure writer (append-only versions) -----------------------------------
def _write_figure(
    cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int, *,
    figure_ref: str, period: str, claim: str, status: str,
    grade: str | None = None, value_class: str | None = None,
    value: Decimal | float | None = None, ci_low: float | None = None, ci_high: float | None = None,
    parameter_set_version: int | None = None, change_treatment: str | None = None,
    refusal_reason: str | None = None, margin_exceeds_effect: bool | None = None,
    expected_verdict_date: date | None = None, missing_parameter: str | None = None,
    lineage: dict[str, Any] | None = None,
) -> str:
    cur.execute(
        "SELECT COALESCE(MAX(version),0)+1 AS v FROM figures WHERE deployment_id=%s AND figure_ref=%s",
        (deployment_id, figure_ref),
    )
    vrow = cur.fetchone()
    assert vrow is not None
    version = int(vrow["v"])
    cur.execute(
        """INSERT INTO figures (workspace_id, deployment_id, figure_status, grade_type, value_class_type,
               figure_ref, version, period, claim, value, ci_low, ci_high, method_id, method_version,
               parameter_set_version, change_treatment, refusal_reason, margin_exceeds_effect,
               expected_verdict_date, missing_parameter, computed_at, next_compute_at, lineage,
               created_by, updated_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), NULL, %s,%s,%s)""",
        (workspace_id, deployment_id, status, grade, value_class, figure_ref, version, period, claim,
         value, ci_low, ci_high, "tf.method." + claim, METHOD_VERSION, parameter_set_version,
         change_treatment, refusal_reason, margin_exceeds_effect, expected_verdict_date, missing_parameter,
         json.dumps(lineage or {}), SYSTEM_USER_ID, SYSTEM_USER_ID),
    )
    return figure_ref


# ---- change treatment -------------------------------------------------------
def _change_treatment(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                      start: date, end: date) -> str | None:
    """One-sided change in-window -> adjust/exclude; symmetric -> cancel (BR-015).
    incumbent tool_change is never credited to the measured vendor."""
    cur.execute(
        """SELECT c.change_type, c.sided_type FROM change_log c
           LEFT JOIN change_log_deployments cd ON cd.change_log_id=c.id
           WHERE c.workspace_id=%s AND c.superseded_by_id IS NULL
             AND c.occurred_at::date BETWEEN %s AND %s
             AND (cd.deployment_id=%s OR cd.deployment_id IS NULL)
           ORDER BY c.occurred_at LIMIT 1""",
        (workspace_id, start, end, deployment_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if row["sided_type"] == "symmetric":
        return "symmetric_cancel"
    if row["change_type"] == "tool_change":
        return "incumbent_tool_change_excluded"
    return "one_sided_adjusted"


# ---- cost (dollarized) ------------------------------------------------------
def compute_cost(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                 period: str) -> str:
    start, end = periods.period_bounds(period)
    figure_ref = f"fig_cost_{period}"
    pv, payload = resolve_parameter_version(cur, workspace_id, period)
    if pv is None:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="priced_cost", status="awaiting_parameters",
                             missing_parameter="parameter_set", lineage={"reason": "no parameter version"})
    unit_prices = payload.get("unit_prices", {})
    cur.execute(
        """SELECT meter_type,
                  CASE WHEN meter_type IN ('seats_active','storage_gb') THEN AVG(gauge_last)
                       ELSE SUM(quantity_sum) END AS qty
           FROM meter_usage_daily WHERE deployment_id=%s AND bucket_date BETWEEN %s AND %s
           GROUP BY meter_type""",
        (deployment_id, start, end),
    )
    total = Decimal("0")
    priced, unpriced = [], []
    for r in cur.fetchall():
        meter, qty = r["meter_type"], Decimal(str(r["qty"] or 0))
        price = unit_prices.get(meter)
        if price is None:
            unpriced.append(meter)
            continue
        total += qty * Decimal(str(price))
        priced.append(meter)
    if not priced and unpriced:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="priced_cost", status="awaiting_parameters", parameter_set_version=pv,
                             missing_parameter=f"unit_prices.{unpriced[0]}",
                             lineage={"unpriced_meters": unpriced})
    treatment = _change_treatment(cur, workspace_id, deployment_id, start, end)
    return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                         claim="priced_cost", status="computed", grade="measured", value_class="realized",
                         value=total, parameter_set_version=pv, change_treatment=treatment,
                         lineage={"priced_meters": priced, "unpriced_meters": unpriced,
                                  "parameter_set_version": pv})


# ---- seat utilization / waste ----------------------------------------------
def compute_seats(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                  period: str) -> str:
    start, end = periods.period_bounds(period)
    figure_ref = f"fig_seats_{period}"
    cur.execute(
        "SELECT seats_paid, price_per_seat FROM deployment_licenses "
        "WHERE deployment_id=%s AND valid_to IS NULL", (deployment_id,))
    lic = cur.fetchone()
    if lic is None:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="seat_utilization", status="awaiting_parameters",
                             missing_parameter="license", lineage={"reason": "no license declared"})
    cur.execute(
        "SELECT COUNT(DISTINCT user_id) AS activated FROM user_activity_daily "
        "WHERE deployment_id=%s AND activity_date BETWEEN %s AND %s", (deployment_id, start, end))
    activated = int((cur.fetchone() or {}).get("activated", 0))
    seats_paid = int(lic["seats_paid"])
    waste_seats = max(seats_paid - activated, 0)
    pv, payload = resolve_parameter_version(cur, workspace_id, period)
    price = lic["price_per_seat"]
    provenance: str | None = "license_data"
    if price is None:
        price = payload.get("unit_prices", {}).get("seats_active") if payload else None
        provenance = "customer_entered" if price is not None else None
    value = Decimal(str(price)) * waste_seats if price is not None else None
    return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                         claim="seat_utilization", status="computed", grade="measured",
                         value_class="realized", value=value, parameter_set_version=pv,
                         lineage={"seats_paid": seats_paid, "activated": activated,
                                  "waste_seats": waste_seats, "price_provenance": provenance})


# ---- containment (agents; G6 corroboration) ---------------------------------
def compute_containment(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                        period: str) -> str | None:
    start, end = periods.period_bounds(period)
    figure_ref = f"fig_containment_{period}"
    cur.execute(
        """SELECT payload->>'action_type' AS action, count(*) AS n FROM events
           WHERE deployment_id=%s AND event_type='activity'
             AND occurred_at::date BETWEEN %s AND %s
             AND payload->>'action_type' IN ('case_handled_autonomous','escalated_to_human')
           GROUP BY payload->>'action_type'""",
        (deployment_id, start, end),
    )
    counts = {r["action"]: int(r["n"]) for r in cur.fetchall()}
    autonomous = counts.get("case_handled_autonomous", 0)
    escalated = counts.get("escalated_to_human", 0)
    if autonomous + escalated == 0:
        return None
    rate = autonomous / (autonomous + escalated)
    # G6: customer-origin events acted by declared service accounts corroborate.
    cur.execute(
        """SELECT count(*) AS n FROM events e
           JOIN deployment_service_users dsu ON dsu.deployment_id=e.deployment_id
              AND dsu.user_id=e.resolved_user_id
           WHERE e.deployment_id=%s AND e.origin_type='customer_system'
             AND e.occurred_at::date BETWEEN %s AND %s""",
        (deployment_id, start, end),
    )
    corroborating = int((cur.fetchone() or {}).get("n", 0))
    return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                         claim="containment_rate", status="computed",
                         grade="verified" if corroborating > 0 else "measured", value_class="realized",
                         value=Decimal(str(round(rate, 6))),
                         lineage={"autonomous": autonomous, "escalated": escalated,
                                  "g6_corroborating_events": corroborating})


# ---- time savings vs baseline (refusal honesty) -----------------------------
def compute_time_savings(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                         period: str) -> str | None:
    start, end = periods.period_bounds(period)
    figure_ref = f"fig_time_{period}"
    # Handling time per work item = completed.ts - created.ts; assisted = item had an activity event.
    cur.execute(
        """WITH life AS (
             SELECT w.id AS wi,
                    MIN(CASE WHEN e.payload->>'event'='created' THEN e.occurred_at END) AS created,
                    MAX(CASE WHEN e.payload->>'event'='completed' THEN e.occurred_at END) AS completed
             FROM events e JOIN work_items w ON w.id=e.work_item_id
             WHERE e.deployment_id=%s AND e.event_type='lifecycle'
               AND e.occurred_at::date BETWEEN %s AND %s
             GROUP BY w.id),
           assisted AS (
             SELECT DISTINCT work_item_id AS wi FROM events
             WHERE deployment_id=%s AND event_type='activity' AND work_item_id IS NOT NULL)
         SELECT (a.wi IS NOT NULL) AS is_assisted,
                EXTRACT(EPOCH FROM (l.completed - l.created))/3600.0 AS hours
         FROM life l LEFT JOIN assisted a ON a.wi=l.wi
         WHERE l.created IS NOT NULL AND l.completed IS NOT NULL""",
        (deployment_id, start, end, deployment_id),
    )
    assisted: list[float] = []
    unassisted: list[float] = []
    for r in cur.fetchall():
        (assisted if r["is_assisted"] else unassisted).append(float(r["hours"]))
    if not assisted or not unassisted:
        return None
    import math

    a_mean = sum(assisted) / len(assisted)
    u_mean = sum(unassisted) / len(unassisted)
    effect = u_mean - a_mean  # hours saved per item
    # CI-like half-width: range shrunk by sqrt(n). More consistent samples tighten
    # the margin, so a REFUSED claim can later become answerable (refusal.lifted).
    n = len(assisted) + len(unassisted)
    margin = (max(assisted + unassisted) - min(assisted + unassisted)) / (2.0 * math.sqrt(n))
    pv, payload = resolve_parameter_version(cur, workspace_id, period)
    if abs(effect) < margin:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="time_savings", status="refused", margin_exceeds_effect=True,
                             refusal_reason="margin of error exceeds the measured effect",
                             expected_verdict_date=end, parameter_set_version=pv,
                             lineage={"assisted_n": len(assisted), "unassisted_n": len(unassisted),
                                      "effect_hours": round(effect, 4), "margin_hours": round(margin, 4)})
    if pv is None:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="time_savings", status="awaiting_parameters",
                             missing_parameter="labor_rates", value_class="capacity",
                             lineage={"effect_hours": round(effect, 4)})
    labor = payload.get("labor_rates", {})
    rate = labor.get("default") or (next(iter(labor.values())) if labor else None)
    treatment = _change_treatment(cur, workspace_id, deployment_id, start, end)
    if rate is None:
        return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                             claim="time_savings", status="awaiting_parameters",
                             missing_parameter="labor_rates.default", parameter_set_version=pv,
                             value_class="capacity", lineage={"effect_hours": round(effect, 4)})
    value = Decimal(str(round(effect * len(assisted) * float(rate), 6)))
    # Thin data supports only a directional ESTIMATE; enough samples -> MEASURED.
    grade = "measured" if (len(assisted) + len(unassisted)) >= 4 else "estimate"
    return _write_figure(cur, workspace_id, deployment_id, figure_ref=figure_ref, period=period,
                         claim="time_savings", status="computed", grade=grade,
                         value_class="capacity", value=value, parameter_set_version=pv,
                         change_treatment=treatment,
                         lineage={"assisted_n": len(assisted), "unassisted_n": len(unassisted),
                                  "effect_hours": round(effect, 4), "labor_rate": rate,
                                  "parameter_set_version": pv})


# ---- orchestration ----------------------------------------------------------
def compute_deployment(workspace_id: int, deployment_id: int, period: str) -> list[str]:
    """Run all applicable methods for a deployment/period. Returns figure_refs written."""
    written: list[str] = []
    with db.transaction() as cur:
        cur.execute("SELECT deployment_type FROM deployments WHERE id=%s", (deployment_id,))
        drow = cur.fetchone()
        dtype = drow["deployment_type"] if drow else None
        written.append(compute_cost(cur, workspace_id, deployment_id, period))
        written.append(compute_seats(cur, workspace_id, deployment_id, period))
        ts = compute_time_savings(cur, workspace_id, deployment_id, period)
        if ts:
            written.append(ts)
        if dtype == "agent":
            c = compute_containment(cur, workspace_id, deployment_id, period)
            if c:
                written.append(c)
    _fire_figure_webhooks(workspace_id, deployment_id, written, period)
    return written


def _fire_figure_webhooks(workspace_id: int, deployment_id: int, refs_written: list[str], period: str) -> None:
    dep_ref = db.fetch_one("SELECT deployment_ref FROM deployments WHERE id=%s", (deployment_id,))
    ref = dep_ref["deployment_ref"] if dep_ref else None
    for fr in refs_written:
        webhooks_delivery.emit(workspace_id, "figure.updated",
                               {"figure_id": fr, "deployment_id": ref, "period": period})
        # refusal.lifted: a previously-refused figure that is now computed became answerable.
        rows = db.fetch_all(
            "SELECT figure_status FROM figures WHERE deployment_id=%s AND figure_ref=%s "
            "ORDER BY version DESC LIMIT 2", (deployment_id, fr))
        if len(rows) >= 2 and rows[0]["figure_status"] == "computed" and rows[1]["figure_status"] == "refused":
            webhooks_delivery.emit(workspace_id, "refusal.lifted",
                                   {"figure_id": fr, "deployment_id": ref, "period": period})


# ---- report issuance --------------------------------------------------------
def issue_report(workspace_id: int, deployment_id: int, period: str) -> str:
    """Bind the latest version of each figure in the period into an immutable report."""
    with db.transaction() as cur:
        cur.execute(
            """SELECT DISTINCT ON (figure_ref) id, figure_ref, version, figure_status, grade_type, claim, value
               FROM figures WHERE deployment_id=%s AND period=%s
               ORDER BY figure_ref, version DESC""",
            (deployment_id, period),
        )
        figs = cur.fetchall()
        grade_profile: dict[str, int] = {}
        for f in figs:
            g = f["grade_type"] or f["figure_status"]
            grade_profile[g] = grade_profile.get(g, 0) + 1

        cur.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM reports WHERE deployment_id=%s AND report_ref=%s",
                    (deployment_id, f"rep_{period}"))
        vrow = cur.fetchone()
        assert vrow is not None
        version = int(vrow["v"])
        report_ref = f"rep_{period}"
        artifact_path = f"{workspace_id}/{report_ref}.v{version}.json"
        supersedes = version - 1 if version > 1 else None
        cur.execute(
            """INSERT INTO reports (deployment_id, report_ref, version, period, issued_at, grade_profile,
                   artifact_path, supersedes_version, created_by, updated_by)
               VALUES (%s,%s,%s,%s, now(), %s,%s,%s,%s,%s) RETURNING id""",
            (deployment_id, report_ref, version, period, json.dumps(grade_profile), artifact_path,
             supersedes, SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        rid = cur.fetchone()
        assert rid is not None
        for f in figs:
            cur.execute(
                """INSERT INTO report_figures (deployment_id, report_id, figure_id, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s)""",
                (deployment_id, rid["id"], f["id"], SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
        artifact = {"report_ref": report_ref, "version": version, "period": period,
                    "grade_profile": grade_profile,
                    "figures": [{"figure_ref": f["figure_ref"], "version": f["version"],
                                 "claim": f["claim"], "status": f["figure_status"],
                                 "value": float(f["value"]) if f["value"] is not None else None}
                                for f in figs]}
    storage.get_storage().put("report-artifacts", artifact_path,
                              json.dumps(artifact, indent=2, sort_keys=True).encode())
    _issue_report_webhook(workspace_id, deployment_id, report_ref, version, period)
    return report_ref


def _issue_report_webhook(workspace_id: int, deployment_id: int, report_ref: str, version: int, period: str) -> None:
    dep = db.fetch_one("SELECT deployment_ref FROM deployments WHERE id=%s", (deployment_id,))
    webhooks_delivery.emit(workspace_id, "report.issued",
                           {"report_id": report_ref, "version": version, "period": period,
                            "deployment_id": dep["deployment_ref"] if dep else None})


# ---- alert monitors ---------------------------------------------------------
def run_monitors(workspace_id: int) -> list[str]:
    """Raise durable alerts for detected conditions. Returns alert_refs raised."""
    raised: list[str] = []
    with db.transaction() as cur:
        cur.execute(
            """SELECT d.id, d.deployment_ref,
                      MAX(s.last_event_at) AS last_event,
                      SUM(s.activity_count) AS activity, SUM(s.joined_count) AS joined
               FROM deployments d LEFT JOIN ingestion_stats_daily s ON s.deployment_id=d.id
               WHERE d.workspace_id=%s GROUP BY d.id, d.deployment_ref""",
            (workspace_id,),
        )
        for r in cur.fetchall():
            last = r["last_event"]
            if last is not None and (datetime.now(UTC) - last).days >= 2:
                raised.append(_raise_alert(cur, workspace_id, int(r["id"]), "event_silence",
                                          f"no events for {r['deployment_ref']} in >= 2 days"))
            activity = int(r["activity"] or 0)
            joined = int(r["joined"] or 0)
            if activity >= 10 and joined == 0:
                raised.append(_raise_alert(cur, workspace_id, int(r["id"]), "join_rate_drop",
                                          f"{r['deployment_ref']} join rate is zero over {activity} activity events"))
    return [x for x in raised if x]


def _raise_alert(cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int,
                 code: str, detail: str) -> str:
    alert_ref = refs.new_ref("alert")
    cur.execute(
        """INSERT INTO alerts (workspace_id, deployment_id, alert_code_type, alert_status, alert_ref,
               detail, first_seen, last_seen, created_by, updated_by)
           VALUES (%s,%s,%s,'open',%s,%s, now(), now(), %s,%s)""",
        (workspace_id, deployment_id, code, alert_ref, detail, SYSTEM_USER_ID, SYSTEM_USER_ID),
    )
    webhooks_delivery.emit(workspace_id, "alert.raised", {"alert_id": alert_ref, "code": code})
    return alert_ref
