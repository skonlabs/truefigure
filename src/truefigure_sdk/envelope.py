"""The universal response envelope.

Every response, success or failure:
  { data: object|null,
    meta: { schema_version, request_id, as_of, deployment_id?, next_cursor? },
    errors: ErrorObject[] }
data is null iff errors is non-empty and no partial result exists (§1.1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def meta(
    request_id: str,
    schema_version: str,
    *,
    deployment_id: str | None = None,
    next_cursor: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    m: dict[str, Any] = {
        "schema_version": schema_version,
        "request_id": request_id,
        "as_of": as_of or _now_iso(),
    }
    if deployment_id is not None:
        m["deployment_id"] = deployment_id
    if next_cursor is not None:
        m["next_cursor"] = next_cursor
    return m


def envelope(
    data: Any,
    *,
    request_id: str,
    schema_version: str,
    errors: list[dict[str, Any]] | None = None,
    deployment_id: str | None = None,
    next_cursor: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    errs = errors or []
    # data must be null iff there is an error and no partial result.
    return {
        "data": data,
        "meta": meta(
            request_id,
            schema_version,
            deployment_id=deployment_id,
            next_cursor=next_cursor,
            as_of=as_of,
        ),
        "errors": errs,
    }
