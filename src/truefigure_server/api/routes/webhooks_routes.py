"""API route for the webhook delivery log (GET /v1/webhooks/{id}/deliveries).

The delivery/signing/worker logic is in domain/webhooks_delivery.py; this module
is the thin controller that authenticates and serves the log envelope.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from truefigure_server.api.application_services.auth import Principal, require_principal
from truefigure_server.api.request_models.http import ok
from truefigure_server.errors import TFError
from truefigure_server.platform.database import db

router = APIRouter()


@router.get("/v1/webhooks/{webhook_id}/deliveries")
async def webhook_deliveries(webhook_id: str, request: Request,
                             principal: Principal = Depends(require_principal)) -> Any:
    wh = db.fetch_one(
        "SELECT id FROM webhooks WHERE webhook_ref=%s AND workspace_id=%s",
        (webhook_id, principal.workspace_id),
    )
    if wh is None:
        raise TFError("TF-READ-001", detail="webhook not found")
    rows = db.fetch_all(
        """SELECT delivery_ref, webhook_event_type, delivery_status, attempt_count, last_http_status,
                  next_retry_at, created_dt
           FROM webhook_deliveries WHERE webhook_id=%s ORDER BY created_dt DESC LIMIT 200""",
        (wh["id"],),
    )
    items = [
        {"delivery_id": r["delivery_ref"], "event_type": r["webhook_event_type"],
         "status": r["delivery_status"], "attempt_count": r["attempt_count"],
         "last_http_status": r["last_http_status"],
         "next_retry_at": r["next_retry_at"].isoformat() if r["next_retry_at"] else None}
        for r in rows
    ]
    return ok(request, {"results": items})
