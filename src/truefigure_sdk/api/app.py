"""FastAPI application: envelope-wrapping error handling and the whoami endpoint.

Routers for the config, ingestion, read, and webhook planes are mounted here as
each phase lands. Every response — success or failure — is the standard envelope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from truefigure_sdk.api.application_services.auth import Principal, require_principal
from truefigure_sdk.api.response_models import envelope
from truefigure_sdk.errors import TFError
from truefigure_sdk.platform.config.config import get_settings
from truefigure_sdk.platform.database import db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:  # pragma: no cover - prod lifespan
    yield
    db.close_pool()


app = FastAPI(title="TrueFigure SDK", version="1.0", lifespan=lifespan)

# routers imported after app is created (they import `app`-adjacent helpers)
from truefigure_sdk.api.routes import (  # noqa: E402
    config_plane,
    imports,
    ingest,
    read_plane,
    webhooks_routes,  # noqa: E402
)

app.include_router(config_plane.router)
app.include_router(ingest.router)
app.include_router(imports.router)
app.include_router(webhooks_routes.router)
app.include_router(read_plane.router)


@app.middleware("http")
async def attach_request_id(request: Request, call_next: Any) -> Any:
    request.state.request_id = envelope.new_request_id()
    response = await call_next(request)
    response.headers["X-TrueFigure-Request-Id"] = request.state.request_id
    return response


def _request_id(request: Request) -> str:
    rid: str = getattr(request.state, "request_id", None) or envelope.new_request_id()
    return rid


@app.exception_handler(TFError)
async def handle_tf_error(request: Request, exc: TFError) -> JSONResponse:
    body = envelope.envelope(
        None,
        request_id=_request_id(request),
        schema_version=get_settings().schema_version,
        errors=[exc.to_error_object()],
    )
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(status_code=exc.http, content=body, headers=headers)


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Never leak internals; map to the server-plane code.
    err = TFError("TF-SRV-001", detail="internal error")
    body = envelope.envelope(
        None,
        request_id=_request_id(request),
        schema_version=get_settings().schema_version,
        errors=[err.to_error_object()],
    )
    return JSONResponse(status_code=err.http, content=body)


@app.get("/v1/whoami")
async def whoami(request: Request, principal: Principal = Depends(require_principal)) -> JSONResponse:
    row = db.fetch_one(
        """
        SELECT o.org_ref, o.plan_type,
               w.workspace_ref, w.environment_type,
               w.rate_limit_rpm, w.rate_limit_epm
        FROM workspaces w
        JOIN organizations o ON o.id = w.organization_id
        WHERE w.id = %s
        """,
        (principal.workspace_id,),
    )
    if row is None:  # pragma: no cover - workspace always exists for a valid key
        raise TFError("TF-SRV-001", detail="workspace not found")

    data = {
        "org_ref": row["org_ref"],
        "plan_type": row["plan_type"],
        "workspace_ref": row["workspace_ref"],
        "environment": row["environment_type"],
        "mode": principal.mode,
        "scope": principal.scope,
        "roles": list(principal.roles),
        "rate_limit_tier": {
            "rpm": row["rate_limit_rpm"],
            "epm": row["rate_limit_epm"],
        },
    }
    return JSONResponse(
        status_code=200,
        content=envelope.envelope(
            data,
            request_id=_request_id(request),
            schema_version=get_settings().schema_version,
        ),
    )
