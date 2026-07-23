"""Error objects and the TrueFigure error exception.

An ErrorObject mirrors the registry entry plus optional per-occurrence context
(field_path, retry_after, detail). TFError is raised anywhere in the stack and
converted to the response envelope by the app's exception handler.
"""

from __future__ import annotations

from typing import Any

from truefigure_server.platform.config import registry


class TFError(Exception):
    """A contract error carrying exactly one registry code."""

    def __init__(
        self,
        code: str,
        *,
        field_path: str | None = None,
        retry_after: int | None = None,
        detail: str | None = None,
        message: str | None = None,
    ) -> None:
        definition = registry.get_code(code)  # raises if code invented
        self.code = code
        self.name: str = definition["name"]
        self.http: int = int(definition["http"])
        self.retryable: bool = bool(definition["retryable"])
        self.message: str = message or str(definition["message"])
        self.field_path = field_path
        self.retry_after = retry_after
        self.detail = detail
        super().__init__(f"{code} {self.name}: {self.message}")

    def to_error_object(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.field_path is not None:
            obj["field_path"] = self.field_path
        if self.retry_after is not None:
            obj["retry_after"] = self.retry_after
        if self.detail is not None:
            obj["detail"] = self.detail
        return obj


def error_object(code: str, **kwargs: Any) -> dict[str, Any]:
    """Build a bare error object dict without raising."""
    return TFError(code, **kwargs).to_error_object()
