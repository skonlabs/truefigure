"""Authentication and authorization.

Credential plane = TrueFigure API keys (`tf_live_`/`tf_test_`, 32-char secret).
Keys are stored only as a SHA-256 hash of the presented key; the secret is shown
once at issuance (ops CLI). A key resolves to a Principal carrying its workspace,
mode, scope, roles, and — for deployment-scoped keys — the granted deployments.

Enforced here:
  * TF-AUTH-001  missing / malformed / revoked key
  * TF-AUTH-002  key not scoped to this deployment (or plane)
  * TF-AUTH-003  key mode does not match the server environment (test-in-prod)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from fastapi import Request

from truefigure_server.api.request_models.wire import key_prefix_to_mode
from truefigure_server.errors import TFError
from truefigure_server.platform.config.config import get_settings
from truefigure_server.platform.database import db


def hash_key(presented_key: str) -> str:
    """Deterministic SHA-256 of the full presented key (high-entropy token)."""
    return hashlib.sha256(presented_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    api_key_id: int
    api_key_ref: str
    workspace_id: int
    owner_user_id: int
    mode: str  # "production" | "test"
    scope: str  # "workspace" | "deployment"
    roles: tuple[str, ...] = ()
    granted_deployment_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def is_test(self) -> bool:
        return self.mode == "test"

    def require_role(self, role: str) -> None:
        if role not in self.roles:
            raise TFError(
                "TF-AUTH-002",
                detail=f"key lacks required role '{role}'",
            )

    def require_deployment(self, deployment_id: int) -> None:
        """Deployment-scoped keys may only touch their granted deployments."""
        if self.scope == "deployment" and deployment_id not in self.granted_deployment_ids:
            raise TFError(
                "TF-AUTH-002",
                detail=f"key not scoped to deployment {deployment_id}",
            )


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header or not header.lower().startswith("bearer "):
        raise TFError("TF-AUTH-001", detail="missing Authorization: Bearer <api_key>")
    token = header[7:].strip()
    if not token:
        raise TFError("TF-AUTH-001", detail="empty bearer token")
    return token


def resolve_key(presented_key: str) -> Principal:
    """Resolve a presented key to a Principal, enforcing AUTH-001/002/003."""
    mode = key_prefix_to_mode(presented_key)
    if mode is None:
        raise TFError("TF-AUTH-001", detail="unrecognized key prefix")

    row = db.fetch_one(
        """
        SELECT k.id, k.api_key_ref, k.workspace_id, k.owner_user_id,
               k.api_key_mode, k.api_key_scope, k.api_key_status, k.roles
        FROM api_keys k
        WHERE k.secret_hash = %s
        """,
        (hash_key(presented_key),),
    )
    if row is None or row["api_key_status"] != "active":
        raise TFError("TF-AUTH-001", detail="key not found or revoked")

    # Mode must match the server environment: production-mode keys serve the
    # production environment; test-mode keys serve the sandbox environment
    # (a test key cannot act in production — TF-AUTH-003).
    settings = get_settings()
    expected_mode = "production" if settings.environment == "production" else "test"
    if row["api_key_mode"] != expected_mode:
        raise TFError(
            "TF-AUTH-003",
            detail=f"{row['api_key_mode']} key used against {settings.environment} environment",
        )

    granted: frozenset[int] = frozenset()
    if row["api_key_scope"] == "deployment":
        grants = db.fetch_all(
            "SELECT deployment_id FROM api_key_deployments WHERE api_key_id = %s",
            (row["id"],),
        )
        granted = frozenset(int(g["deployment_id"]) for g in grants)

    return Principal(
        api_key_id=int(row["id"]),
        api_key_ref=row["api_key_ref"],
        workspace_id=int(row["workspace_id"]),
        owner_user_id=int(row["owner_user_id"]),
        mode=row["api_key_mode"],
        scope=row["api_key_scope"],
        roles=tuple(row["roles"] or ()),
        granted_deployment_ids=granted,
    )


def require_principal(request: Request) -> Principal:
    """FastAPI dependency: authenticate the request, returning its Principal."""
    return resolve_key(_extract_bearer(request))
