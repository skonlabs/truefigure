"""Object storage for import uploads, import error reports, and report artifacts.

Two backends, chosen by environment:
  * SupabaseStorage  — when SUPABASE_URL + SUPABASE_SERVICE_KEY are set; talks to
    the Supabase Storage REST API with signed, expiring URLs (single-use uploads).
  * FilesystemStorage — the plain-Postgres / local-dev / CI fallback; stores under
    TF_STORAGE_DIR (or a temp dir). Signed URLs are opaque local references.

The application only depends on this interface, so nothing above it is
Supabase-specific. Buckets: import-uploads, import-error-reports, report-artifacts.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, bucket: str, path: str, data: bytes) -> None: ...
    def get(self, bucket: str, path: str) -> bytes: ...
    def exists(self, bucket: str, path: str) -> bool: ...
    def signed_upload_url(self, bucket: str, path: str, expires_s: int = 86400) -> str: ...
    def signed_download_url(self, bucket: str, path: str, expires_s: int = 86400) -> str: ...


class FilesystemStorage:
    """Local-filesystem backend used for dev/CI and the plain-Postgres path."""

    def __init__(self, base: str | None = None) -> None:
        self.base = Path(base or os.environ.get("TF_STORAGE_DIR") or tempfile.gettempdir()) / "tf_storage"

    def _p(self, bucket: str, path: str) -> Path:
        return self.base / bucket / path

    def put(self, bucket: str, path: str, data: bytes) -> None:
        p = self._p(bucket, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, bucket: str, path: str) -> bytes:
        return self._p(bucket, path).read_bytes()

    def exists(self, bucket: str, path: str) -> bool:
        return self._p(bucket, path).exists()

    def signed_upload_url(self, bucket: str, path: str, expires_s: int = 86400) -> str:
        return f"tfstore://{bucket}/{path}?mode=upload&expires_s={expires_s}"

    def signed_download_url(self, bucket: str, path: str, expires_s: int = 86400) -> str:
        return f"tfstore://{bucket}/{path}?mode=download&expires_s={expires_s}"


class SupabaseStorage:  # pragma: no cover - external service, unreachable from the sandbox
    """Supabase Storage backend. Exercised only against a live project (the sandbox
    egress proxy blocks *.supabase.co), so it is excluded from coverage and verified
    in the hosted environment. The wire calls follow the Storage REST API."""

    def __init__(self, url: str, service_key: str) -> None:
        self._url = url.rstrip("/")
        self._key = service_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "apikey": self._key}

    def put(self, bucket: str, path: str, data: bytes) -> None:
        import httpx

        r = httpx.post(f"{self._url}/storage/v1/object/{bucket}/{path}", content=data,
                       headers={**self._headers(), "x-upsert": "true"})
        r.raise_for_status()

    def get(self, bucket: str, path: str) -> bytes:
        import httpx

        r = httpx.get(f"{self._url}/storage/v1/object/{bucket}/{path}", headers=self._headers())
        r.raise_for_status()
        return r.content

    def exists(self, bucket: str, path: str) -> bool:
        import httpx

        r = httpx.get(f"{self._url}/storage/v1/object/info/{bucket}/{path}", headers=self._headers())
        return r.status_code == 200

    def signed_upload_url(self, bucket: str, path: str, expires_s: int = 86400) -> str:
        import httpx

        r = httpx.post(f"{self._url}/storage/v1/object/upload/sign/{bucket}/{path}", headers=self._headers())
        r.raise_for_status()
        return self._url + str(r.json()["url"])

    def signed_download_url(self, bucket: str, path: str, expires_s: int = 86400) -> str:
        import httpx

        r = httpx.post(f"{self._url}/storage/v1/object/sign/{bucket}/{path}",
                       json={"expiresIn": expires_s}, headers=self._headers())
        r.raise_for_status()
        return self._url + str(r.json()["signedURL"])


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if url and key:  # pragma: no cover - production wiring
            _storage = SupabaseStorage(url, key)
        else:
            _storage = FilesystemStorage()
    return _storage
