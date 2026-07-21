"""Post-ingestion pipeline worker (Postgres-backed, FOR UPDATE SKIP LOCKED).

For each newly-accepted event it: resolves the person identity (roster-gated,
BR-011 shared/service/bot exclusion), joins the work item (lazy-created), and
advances the whitelisted resolution columns only (pipeline_status,
resolved_user_id, work_item_id, exclusion_reason). It then folds the event into
the three live-serving daily aggregates exactly once.

Scheduled as a worker-loop tick in production (or Supabase pg_cron); invoked
directly here and via `opsctl pipeline run`.
"""

from __future__ import annotations

from typing import Any

import psycopg

from truefigure_sdk.platform.database import db

SYSTEM_USER_ID = 1

_ITEM_BEARING = {"activity", "lifecycle", "quality_signal", "revenue_signal"}
_COUNTER_METERS = {"tokens_in", "tokens_out", "api_calls", "compute_seconds"}
_GAUGE_METERS = {"seats_active", "storage_gb"}
_EXCLUSION_BY_TYPE = {
    "shared": "shared_account",
    "service": "service_account",
    "bot": "bot_account",
}


def run_pipeline(limit: int = 500) -> dict[str, int]:
    """Process up to `limit` accepted events. Returns per-outcome counts."""
    counts = {"processed": 0, "resolved": 0, "joined": 0, "excluded": 0}
    with db.connection() as conn:
        while counts["processed"] < limit:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, workspace_id, deployment_id, event_type, event_key, occurred_at,
                                  source_ref, payload
                           FROM events
                           WHERE pipeline_status = 'accepted'
                           ORDER BY occurred_at
                           FOR UPDATE SKIP LOCKED
                           LIMIT 1""",
                    )
                    ev = cur.fetchone()
                    if ev is None:
                        break
                    outcome = _process_event(cur, ev)
            counts["processed"] += 1
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _process_event(cur: psycopg.Cursor[dict[str, Any]], ev: dict[str, Any]) -> str:
    payload = ev["payload"]
    resolved_user_id, exclusion_reason = _resolve_identity(cur, ev, payload)
    work_item_id = _join_work_item(cur, ev, payload)

    if exclusion_reason is not None:
        status = "excluded"
    elif work_item_id is not None:
        status = "joined"
    elif resolved_user_id is not None:
        status = "resolved"
    else:
        status = "resolved"  # processed but unresolved identity stays re-resolvable; not 'accepted' again

    cur.execute(
        """UPDATE events SET pipeline_status=%s, resolved_user_id=%s, work_item_id=%s,
               exclusion_reason=%s, updated_by=%s
           WHERE id=%s AND occurred_at=%s""",
        (status, resolved_user_id, work_item_id, exclusion_reason, SYSTEM_USER_ID,
         ev["id"], ev["occurred_at"]),
    )
    _update_aggregates(cur, ev, payload, resolved_user_id, exclusion_reason, work_item_id)
    if status == "excluded":
        return "excluded"
    if status == "joined":
        return "joined"
    return "resolved"


def _applicable_namespace(
    cur: psycopg.Cursor[dict[str, Any]], workspace_id: int, deployment_id: int, field: str, source_ref: str
) -> str | None:
    cur.execute(
        """SELECT namespace FROM identifier_namespaces
           WHERE workspace_id=%s AND identifier_field_type=%s AND record_status='active'
             AND source_ref IN (%s, '') AND (deployment_id=%s OR deployment_id IS NULL)
           ORDER BY (deployment_id IS NOT NULL) DESC, (source_ref<>'') DESC LIMIT 1""",
        (workspace_id, field, source_ref, deployment_id),
    )
    row = cur.fetchone()
    return row["namespace"] if row else None


def _resolve_identity(
    cur: psycopg.Cursor[dict[str, Any]], ev: dict[str, Any], payload: dict[str, Any]
) -> tuple[int | None, str | None]:
    """Return (resolved_user_id, exclusion_reason). Honors roster windows + BR-011."""
    field = "user_ref" if ev["event_type"] == "activity" else None
    value = payload.get("user_ref") if field else payload.get("assignee_ref")
    if not value:
        return None, None
    field = field or "assignee_ref"

    ns = _applicable_namespace(cur, ev["workspace_id"], ev["deployment_id"], field, ev["source_ref"] or "")
    if ns is not None:
        cur.execute(
            "SELECT user_id FROM user_identifiers WHERE workspace_id=%s AND namespace=%s "
            "AND identifier_value=%s AND record_status='active'",
            (ev["workspace_id"], ns, value),
        )
    else:
        # Undeclared field: match raw against any identifier value, then user_ref.
        cur.execute(
            "SELECT user_id FROM user_identifiers WHERE workspace_id=%s AND identifier_value=%s "
            "AND record_status='active' LIMIT 1",
            (ev["workspace_id"], value),
        )
    row = cur.fetchone()
    user_id: int | None
    if row is not None:
        user_id = int(row["user_id"])
    else:
        cur.execute("SELECT id FROM users WHERE workspace_id=%s AND user_ref=%s", (ev["workspace_id"], value))
        raw = cur.fetchone()
        user_id = int(raw["id"]) if raw else None

    if user_id is None:
        return None, None  # unresolved; re-resolvable later

    cur.execute("SELECT user_type, user_status, effective_to FROM users WHERE id=%s", (user_id,))
    u = cur.fetchone()
    assert u is not None
    if u["user_status"] == "blocked":
        return user_id, "unresolved_identity"
    if u["effective_to"] is not None and ev["occurred_at"].date() > u["effective_to"]:
        return None, None  # window closed before this event; treat as unresolved
    reason = _EXCLUSION_BY_TYPE.get(u["user_type"])  # shared/service/bot -> resolve-then-exclude (BR-011)
    return user_id, reason


def _join_work_item(
    cur: psycopg.Cursor[dict[str, Any]], ev: dict[str, Any], payload: dict[str, Any]
) -> int | None:
    if ev["event_type"] not in _ITEM_BEARING:
        return None
    ref = payload.get("work_item_id")
    if not ref:
        return None
    size = payload.get("size_band") if ev["event_type"] == "lifecycle" else None
    category = payload.get("category") if ev["event_type"] == "lifecycle" else None
    cur.execute(
        """INSERT INTO work_items (workspace_id, size_type, work_item_ref, category, created_by, updated_by)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (workspace_id, work_item_ref) DO UPDATE
             SET size_type=COALESCE(EXCLUDED.size_type, work_items.size_type),
                 category=COALESCE(EXCLUDED.category, work_items.category),
                 updated_by=EXCLUDED.updated_by
           RETURNING id""",
        (ev["workspace_id"], size, ref, category, SYSTEM_USER_ID, SYSTEM_USER_ID),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row["id"])


def _update_aggregates(
    cur: psycopg.Cursor[dict[str, Any]], ev: dict[str, Any], payload: dict[str, Any],
    resolved_user_id: int | None, exclusion_reason: str | None, work_item_id: int | None,
) -> None:
    dep = ev["deployment_id"]
    day = ev["occurred_at"].date()
    source_ref = ev["source_ref"] or ""
    is_activity = ev["event_type"] == "activity"
    id_bearing = bool(payload.get("user_ref") or payload.get("assignee_ref"))
    counted_resolved = resolved_user_id is not None and exclusion_reason is None

    # ingestion_stats_daily (per deployment/source/day)
    cur.execute(
        """INSERT INTO ingestion_stats_daily (deployment_id, source_ref, stat_date, accepted_count,
               identity_bearing_count, resolved_count, activity_count, joined_count, last_event_at,
               created_by, updated_by)
           VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (deployment_id, source_ref, stat_date) DO UPDATE SET
               accepted_count = ingestion_stats_daily.accepted_count + 1,
               identity_bearing_count = ingestion_stats_daily.identity_bearing_count + EXCLUDED.identity_bearing_count,
               resolved_count = ingestion_stats_daily.resolved_count + EXCLUDED.resolved_count,
               activity_count = ingestion_stats_daily.activity_count + EXCLUDED.activity_count,
               joined_count = ingestion_stats_daily.joined_count + EXCLUDED.joined_count,
               last_event_at = GREATEST(ingestion_stats_daily.last_event_at, EXCLUDED.last_event_at),
               updated_by = EXCLUDED.updated_by""",
        (dep, source_ref, day, int(id_bearing), int(counted_resolved), int(is_activity),
         int(work_item_id is not None), ev["occurred_at"], SYSTEM_USER_ID, SYSTEM_USER_ID),
    )

    # user_activity_daily (per deployment/user/day) — only resolved, non-excluded activity
    if is_activity and counted_resolved:
        cur.execute(
            """INSERT INTO user_activity_daily (workspace_id, deployment_id, user_id, activity_date,
                   event_count, created_by, updated_by)
               VALUES (%s,%s,%s,%s,1,%s,%s)
               ON CONFLICT (deployment_id, user_id, activity_date) DO UPDATE
                 SET event_count = user_activity_daily.event_count + 1, updated_by = EXCLUDED.updated_by""",
            (ev["workspace_id"], dep, resolved_user_id, day, SYSTEM_USER_ID, SYSTEM_USER_ID),
        )

    # meter_usage_daily (per deployment/meter/ref/day)
    if ev["event_type"] == "cost_meter":
        meter = payload["meter"]
        meter_ref = payload.get("meter_ref", "") or ""
        qty = payload.get("quantity", 0)
        if meter in _GAUGE_METERS:
            cur.execute(
                """INSERT INTO meter_usage_daily (deployment_id, meter_type, meter_ref, bucket_date,
                       quantity_sum, gauge_last, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,0,%s,%s,%s)
                   ON CONFLICT (deployment_id, meter_type, meter_ref, bucket_date) DO UPDATE
                     SET gauge_last = EXCLUDED.gauge_last, updated_by = EXCLUDED.updated_by""",
                (dep, meter, meter_ref, day, qty, SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
        else:  # counter: sum deltas
            cur.execute(
                """INSERT INTO meter_usage_daily (deployment_id, meter_type, meter_ref, bucket_date,
                       quantity_sum, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (deployment_id, meter_type, meter_ref, bucket_date) DO UPDATE
                     SET quantity_sum = meter_usage_daily.quantity_sum + EXCLUDED.quantity_sum,
                         updated_by = EXCLUDED.updated_by""",
                (dep, meter, meter_ref, day, qty, SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
