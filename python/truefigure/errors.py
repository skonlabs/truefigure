"""TrueFigure SDK error model.

The error-code registry is a closed, versioned enum (registry/error-codes.json).
Codes are never reused; client switch statements are safe for years.
Refusals are NOT errors: a REFUSED figure is a successful response about evidence.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "error_codes.json")
with open(_REGISTRY_PATH, "r", encoding="utf-8") as _f:
    REGISTRY = json.load(_f)["codes"]


@dataclass
class ErrorObject:
    """Structured error, mirroring the API's ErrorObject schema."""
    code: str
    message: str
    retryable: bool
    request_id: str
    field_path: Optional[str] = None
    expected: Optional[str] = None
    received: Optional[str] = None
    retry_after: Optional[int] = None
    doc_url: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "ErrorObject":
        return cls(
            code=d.get("code", "TF-SRV-001"),
            message=d.get("message", ""),
            retryable=bool(d.get("retryable", False)),
            request_id=d.get("request_id", ""),
            field_path=d.get("field_path"),
            expected=d.get("expected"),
            received=d.get("received"),
            retry_after=d.get("retry_after"),
            doc_url=d.get("doc_url"),
        )

    @property
    def fix_owner(self) -> str:
        """Whose action fixes it: integrator | config_owner | platform | none."""
        return REGISTRY.get(self.code, {}).get("fix_owner", "unknown")


class TrueFigureError(Exception):
    """Raised for non-retryable failures after retries are exhausted or inapplicable."""

    def __init__(self, errors: list[ErrorObject], request_id: str = ""):
        self.errors = errors
        self.request_id = request_id or (errors[0].request_id if errors else "")
        summary = "; ".join(f"{e.code}: {e.message}" for e in errors) or "unknown error"
        super().__init__(f"[request_id={self.request_id}] {summary}")


class RateLimited(TrueFigureError):
    """TF-RATE-001. Client backoff honors retry_after; buffered replay is safe (idempotency)."""

    @property
    def retry_after(self) -> int:
        for e in self.errors:
            if e.retry_after is not None:
                return e.retry_after
        return 1
