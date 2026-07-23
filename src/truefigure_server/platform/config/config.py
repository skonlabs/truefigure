"""Runtime configuration, sourced only from the environment.

No connection string, service key, or secret ever lives in the repo, fixtures,
or logs — they arrive through the process environment (§3 of the build spec).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Immutable process settings."""

    database_url: str
    environment: str  # "production" | "sandbox" — the mode this server serves
    schema_version: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"required environment variable {name} is not set")
    return val


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    environment = os.environ.get("TF_ENVIRONMENT", "production")
    if environment not in ("production", "sandbox"):
        raise RuntimeError(
            f"TF_ENVIRONMENT must be 'production' or 'sandbox', got {environment!r}"
        )
    return Settings(
        database_url=_require("DATABASE_URL"),
        environment=environment,
        schema_version=os.environ.get("TF_SCHEMA_VERSION", "1.0"),
    )
