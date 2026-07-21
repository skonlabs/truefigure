"""Bulk NDJSON import lane — POST /v1/imports, GET /v1/imports/{import_id}, worker.

Same envelope contract, same validators, same dedup as /v1/events:batch (the
worker replays each line through ingest._process_one), but a separate lane so
imports never starve live traffic. Uploads and the per-line reject report live
in Supabase Storage (single-use signed upload; NDJSON error report on rejects).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from truefigure_sdk.api.application_services.auth import Principal, require_principal
from truefigure_sdk.api.request_models.http import ok, parse_body
from truefigure_sdk.api.routes import ingest
from truefigure_sdk.domain.entities import refs
from truefigure_sdk.errors import TFError
from truefigure_sdk.platform.database import db
from truefigure_sdk.platform.storage import storage

router = APIRouter()
SYSTEM_USER_ID = 1
_UPLOAD_BUCKET = "import-uploads"
_ERROR_BUCKET = "import-error-reports"


class ImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(backfill|publication|migration|other)$")
    expected_events: int = Field(default=0, ge=0)
    source_ref: str | None = None


@router.post("/v1/imports")
async def create_import(request: Request, principal: Principal = Depends(require_principal)) -> Any:
    body = parse_body(ImportCreate, await request.json())
    import_ref = refs.new_ref("import_job")
    upload_path = f"{principal.workspace_id}/{import_ref}.ndjson"
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO import_jobs (workspace_id, import_type, import_status, import_ref, source_ref,
                   expected_events, upload_path, upload_url_expires_at, created_by, updated_by)
               VALUES (%s,%s,'queued',%s,%s,%s,%s, now() + interval '24 hours', %s,%s) RETURNING id""",
            (principal.workspace_id, body.kind, import_ref, body.source_ref, body.expected_events,
             upload_path, SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
    upload_url = storage.get_storage().signed_upload_url(_UPLOAD_BUCKET, upload_path)
    return ok(request, {"import_id": import_ref, "status": "queued", "upload_url": upload_url}, status=201)


@router.get("/v1/imports/{import_id}")
async def import_status(import_id: str, request: Request, principal: Principal = Depends(require_principal)) -> Any:
    row = db.fetch_one(
        """SELECT import_status, received_count, accepted_count, duplicate_count, rejected_count,
                  error_report_path
           FROM import_jobs WHERE import_ref=%s AND workspace_id=%s""",
        (import_id, principal.workspace_id),
    )
    if row is None:
        raise TFError("TF-READ-001", detail=f"import {import_id} not found")
    error_url = None
    if row["error_report_path"]:
        error_url = storage.get_storage().signed_download_url(_ERROR_BUCKET, row["error_report_path"])
    return ok(request, {
        "import_id": import_id, "status": row["import_status"],
        "counts": {"received": row["received_count"], "accepted": row["accepted_count"],
                   "duplicates": row["duplicate_count"], "rejected": row["rejected_count"]},
        "error_report_url": error_url,
    })


def _job_principal(workspace_id: int) -> Principal:
    """A synthetic workspace-scoped principal so the worker reuses ingest exactly."""
    return Principal(
        api_key_id=0, api_key_ref="import_worker", workspace_id=workspace_id,
        owner_user_id=SYSTEM_USER_ID, mode="production", scope="workspace",
    )


def run_import(import_ref: str) -> dict[str, int]:
    """Process one uploaded import job end-to-end. Returns the final counts."""
    job = db.fetch_one(
        "SELECT id, workspace_id, source_ref, import_ref, upload_path FROM import_jobs WHERE import_ref=%s",
        (import_ref,),
    )
    if job is None:
        raise TFError("TF-READ-001", detail=f"import {import_ref} not found")

    store = storage.get_storage()
    if not store.exists(_UPLOAD_BUCKET, job["upload_path"]):
        _set_status(job["id"], "failed", None)
        raise TFError("TF-SRV-001", detail="upload not found for import job")

    _set_processing(job["id"])
    principal = _job_principal(int(job["workspace_id"]))
    source_ref = job["source_ref"] or ""
    horizon = ingest._backfill_horizon(principal.workspace_id)

    raw = store.get(_UPLOAD_BUCKET, job["upload_path"]).decode("utf-8")
    counts = {"received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}
    error_lines: list[str] = []
    for line_no, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        counts["received"] += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            counts["rejected"] += 1
            error_lines.append(json.dumps({"line": line_no, "error": {"code": "TF-EVT-003",
                                                                      "message": "line is not valid JSON"}}))
            continue
        result = ingest._process_one(principal, event, line_no, source_ref, horizon)
        status = result["status"]
        if status == "accepted":
            counts["accepted"] += 1
        elif status == "duplicate":
            counts["duplicate"] += 1
        else:
            counts["rejected"] += 1
            error_lines.append(json.dumps({"line": line_no, "event_key": result.get("event_key"),
                                           "error": result.get("error")}))

    error_report_path = None
    if error_lines:
        error_report_path = f"{job['workspace_id']}/{import_ref}.errors.ndjson"
        store.put(_ERROR_BUCKET, error_report_path, ("\n".join(error_lines) + "\n").encode("utf-8"))

    final = "completed_with_rejects" if counts["rejected"] else "completed"
    _finalize(job["id"], final, counts, error_report_path)
    return counts


def _set_processing(job_id: int) -> None:
    with db.transaction() as cur:
        cur.execute("UPDATE import_jobs SET import_status='processing', updated_by=%s WHERE id=%s",
                    (SYSTEM_USER_ID, job_id))


def _set_status(job_id: int, status: str, _err: str | None) -> None:
    with db.transaction() as cur:
        cur.execute("UPDATE import_jobs SET import_status=%s, updated_by=%s WHERE id=%s",
                    (status, SYSTEM_USER_ID, job_id))


def _finalize(job_id: int, status: str, counts: dict[str, int], error_report_path: str | None) -> None:
    with db.transaction() as cur:
        cur.execute(
            """UPDATE import_jobs SET import_status=%s, received_count=%s, accepted_count=%s,
                   duplicate_count=%s, rejected_count=%s, error_report_path=%s, updated_by=%s
               WHERE id=%s""",
            (status, counts["received"], counts["accepted"], counts["duplicate"], counts["rejected"],
             error_report_path, SYSTEM_USER_ID, job_id),
        )
