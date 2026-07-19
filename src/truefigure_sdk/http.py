"""Shared HTTP helpers: envelope responses and request-body parsing.

Pydantic models use extra='forbid' so unknown fields raise a validation error,
which we translate to the config-plane malformed-field code TF-CFG-007 (or the
event code TF-EVT-003 for the write plane) with a field_path.
"""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from . import envelope
from .config import get_settings
from .errors import TFError

M = TypeVar("M", bound=BaseModel)


def request_id(request: Request) -> str:
    rid: str = getattr(request.state, "request_id", None) or envelope.new_request_id()
    return rid


def ok(
    request: Request,
    data: Any,
    *,
    status: int = 200,
    deployment_id: str | None = None,
    next_cursor: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=envelope.envelope(
            data,
            request_id=request_id(request),
            schema_version=get_settings().schema_version,
            deployment_id=deployment_id,
            next_cursor=next_cursor,
        ),
    )


def parse_body(model: type[M], body: Any, *, code: str = "TF-CFG-007") -> M:
    """Validate a raw JSON body into a model, mapping errors to a registry code."""
    if not isinstance(body, dict):
        raise TFError(code, detail="request body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        field_path = ".".join(str(p) for p in first.get("loc", ()))
        raise TFError(code, field_path=field_path, detail=first.get("msg")) from exc
