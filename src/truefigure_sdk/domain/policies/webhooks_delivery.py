"""Webhook delivery — HMAC v1 signing, verification challenge, retry worker.

DOMAIN layer: reusable delivery/signing rules with no HTTP-framework coupling.
The read-only delivery-log endpoint lives in api/routes/webhooks_routes.py.

Contract (docs/semantics.md):
  headers: X-TrueFigure-Timestamp (unix seconds),
           X-TrueFigure-Signature: v1=hex(hmac_sha256(secret, f"{ts}.{raw_body}"))
  retries: 1m, 5m, 30m, 2h, 6h, 24h; dedupe by delivery_ref; 7 days of total
           failure -> the webhook is suspended.
Payloads are reference-style only (ids, never figure/report bodies).

HTTP egress is injected as a `sender` callable so the worker is testable without
network. In production the default sender uses httpx.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from truefigure_sdk.errors import TFError
from truefigure_sdk.platform.database import db
from truefigure_sdk.platform.security import secretbox

SYSTEM_USER_ID = 1

# Cumulative retry offsets from first attempt (seconds): 1m, 5m, 30m, 2h, 6h, 24h.
RETRY_SCHEDULE = (60, 300, 1800, 7200, 21600, 86400)
MAX_ATTEMPTS = len(RETRY_SCHEDULE)

# sender(method, url, headers, body) -> http status code
Sender = Callable[[str, str, dict[str, str], bytes], int]


def _default_sender(method: str, url: str, headers: dict[str, str], body: bytes) -> int:  # pragma: no cover
    import httpx

    r = httpx.request(method, url, headers=headers, content=body, timeout=10.0)
    return r.status_code


def sign(secret: str, timestamp: int, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def verify_signature(secret: str, timestamp: int, raw_body: bytes, header: str) -> bool:
    """Consumer-side check (used by tests to prove a delivery is verifiable)."""
    return hmac.compare_digest(sign(secret, timestamp, raw_body), header)


def emit(workspace_id: int, event_type: str, payload_ref: dict[str, Any]) -> int:
    """Enqueue a reference-style delivery for every VERIFIED subscriber. Returns count."""
    enqueued = 0
    with db.transaction() as cur:
        cur.execute(
            "SELECT id FROM webhooks WHERE workspace_id=%s AND webhook_status='verified' "
            "AND %s = ANY(webhook_event_type)",
            (workspace_id, event_type),
        )
        hooks = cur.fetchall()
        for h in hooks:
            cur.execute(
                """INSERT INTO webhook_deliveries (webhook_id, webhook_event_type, delivery_status, payload,
                       attempt_count, next_retry_at, created_by, updated_by)
                   VALUES (%s,%s,'retrying',%s,0, now(), %s,%s)""",
                (h["id"], event_type, json.dumps(payload_ref), SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
            enqueued += 1
    return enqueued


def deliver_once(sender: Sender, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Attempt all due deliveries once. Returns {delivered, retried, dropped}."""
    now = now or datetime.now(UTC)
    counts = {"delivered": 0, "retried": 0, "dropped": 0}
    with db.connection() as conn:
        while counts["delivered"] + counts["retried"] + counts["dropped"] < limit:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT d.id, d.webhook_id, d.webhook_event_type, d.delivery_ref, d.payload,
                                  d.attempt_count, w.url, w.secret_hash
                           FROM webhook_deliveries d JOIN webhooks w ON w.id = d.webhook_id
                           WHERE d.delivery_status='retrying' AND d.next_retry_at <= %s
                           ORDER BY d.next_retry_at
                           FOR UPDATE OF d SKIP LOCKED LIMIT 1""",
                        (now,),
                    )
                    dv = cur.fetchone()
                    if dv is None:
                        break
                    outcome = _attempt(cur, dv, sender, now)
            counts[outcome] += 1
    return counts


def _attempt(cur: Any, dv: dict[str, Any], sender: Sender, now: datetime) -> str:
    ts = int(now.timestamp())
    raw = json.dumps(dv["payload"], separators=(",", ":"), sort_keys=True).encode()
    secret = secretbox.decrypt(dv["secret_hash"])
    headers = {
        "Content-Type": "application/json",
        "X-TrueFigure-Timestamp": str(ts),
        "X-TrueFigure-Signature": sign(secret, ts, raw),
        "X-TrueFigure-Delivery": dv["delivery_ref"],
    }
    try:
        status = sender("POST", dv["url"], headers, raw)
    except Exception:  # noqa: BLE001 - any transport failure is a retryable failure
        status = 0
    attempt = int(dv["attempt_count"]) + 1

    if 200 <= status < 300:
        cur.execute(
            "UPDATE webhook_deliveries SET delivery_status='delivered', attempt_count=%s, "
            "last_http_status=%s, next_retry_at=NULL, updated_by=%s WHERE id=%s",
            (attempt, status, SYSTEM_USER_ID, dv["id"]),
        )
        return "delivered"

    if attempt >= MAX_ATTEMPTS:
        cur.execute(
            "UPDATE webhook_deliveries SET delivery_status='dropped', attempt_count=%s, "
            "last_http_status=%s, next_retry_at=NULL, updated_by=%s WHERE id=%s",
            (attempt, status or None, SYSTEM_USER_ID, dv["id"]),
        )
        # 7 days of total failure -> suspend the webhook (re-verify to resume).
        cur.execute(
            "UPDATE webhooks SET webhook_status='suspended', updated_by=%s WHERE id=%s AND webhook_status='verified'",
            (SYSTEM_USER_ID, dv["webhook_id"]),
        )
        return "dropped"

    next_at = now + timedelta(seconds=RETRY_SCHEDULE[attempt])
    cur.execute(
        "UPDATE webhook_deliveries SET attempt_count=%s, last_http_status=%s, next_retry_at=%s, "
        "updated_by=%s WHERE id=%s",
        (attempt, status or None, next_at, SYSTEM_USER_ID, dv["id"]),
    )
    return "retried"


def verify_webhook(webhook_ref: str, workspace_id: int, sender: Sender) -> bool:
    """Run the challenge: GET the url with a challenge token; 2xx -> verified."""
    row = db.fetch_one(
        "SELECT id, url FROM webhooks WHERE webhook_ref=%s AND workspace_id=%s AND webhook_status='unverified'",
        (webhook_ref, workspace_id),
    )
    if row is None:
        return False
    challenge = hashlib.sha256(webhook_ref.encode()).hexdigest()[:16]
    status = sender("GET", f"{row['url']}?challenge={challenge}", {}, b"")
    if 200 <= status < 300:
        with db.transaction() as cur:
            cur.execute("UPDATE webhooks SET webhook_status='verified', updated_by=%s WHERE id=%s",
                        (SYSTEM_USER_ID, row["id"]))
        return True
    # Challenge failed: the endpoint is unreachable / did not echo (TF-CFG-005).
    raise TFError("TF-CFG-005", detail=f"webhook {webhook_ref} failed verification challenge (status {status})")
