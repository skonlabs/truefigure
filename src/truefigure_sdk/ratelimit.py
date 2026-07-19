"""Per-tenant rate limiting and plan quota.

Fixed-window request limiter (rpm) keyed by (workspace, minute); breach -> 429
TF-RATE-001 with retry_after. Plan-level event quota -> 429 TF-RATE-002.

The limiter state is in-process (a single API worker); a multi-worker deployment
would back this with Redis or a Postgres counter. reset() clears state (tests).
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import db
from .errors import TFError

# Per-plan monthly event quota (defaults; [A] tunable per workspace in production).
PLAN_QUOTA = {
    "free": 20,
    "team": 100_000,
    "organization": 1_000_000,
    "enterprise": 10_000_000,
    "vendor": 10_000_000,
}

_window_hits: dict[tuple[int, int], int] = {}


def reset() -> None:
    _window_hits.clear()


def _minute(now: datetime) -> int:
    return int(now.timestamp()) // 60


def check_rate(workspace_id: int, rpm: int, *, now: datetime | None = None) -> None:
    """Fixed-window rpm limiter. Raises TF-RATE-001 on breach."""
    now = now or datetime.now(UTC)
    window = _minute(now)
    key = (workspace_id, window)
    count = _window_hits.get(key, 0) + 1
    _window_hits[key] = count
    if count > rpm:
        retry_after = 60 - (int(now.timestamp()) % 60)
        raise TFError("TF-RATE-001", retry_after=retry_after,
                      detail=f"per-tenant rate limit {rpm}/min exceeded")


def check_quota(workspace_id: int) -> None:
    """Plan-level monthly event quota. Raises TF-RATE-002 when exhausted."""
    row = db.fetch_one(
        """SELECT o.plan_type,
                  (SELECT count(*) FROM events e WHERE e.workspace_id=w.id
                   AND date_trunc('month', e.occurred_at) = date_trunc('month', now())) AS used
           FROM workspaces w JOIN organizations o ON o.id=w.organization_id WHERE w.id=%s""",
        (workspace_id,),
    )
    if row is None:
        return
    quota = PLAN_QUOTA.get(row["plan_type"])
    if quota is not None and int(row["used"]) >= quota:
        raise TFError("TF-RATE-002", detail=f"plan '{row['plan_type']}' event quota ({quota}) exhausted")
