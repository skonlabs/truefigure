"""The closed error-code registry, loaded from registry/error-codes.json at runtime.

Every error response carries exactly one TF-<PLANE>-<NNN> code from this file.
Inventing codes is a defect, so the registry is the single source of truth: the
error layer looks codes up here and refuses unknown ones.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# repo layout: <root>/registry/error-codes.json ; this file is <root>/src/truefigure_sdk/registry.py
_REGISTRY_PATH = Path(__file__).resolve().parents[4] / "registry" / "error-codes.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    with _REGISTRY_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    if "codes" not in data:  # pragma: no cover - registry is a shipped closed file
        raise RuntimeError("error registry malformed: missing 'codes'")
    return data


def registry_version() -> str:
    return str(_load()["registry_version"])


def all_codes() -> dict[str, dict[str, Any]]:
    """Every code definition keyed by TF-<PLANE>-<NNN>."""
    return dict(_load()["codes"])


def code_ids() -> frozenset[str]:
    return frozenset(_load()["codes"].keys())


def get_code(code: str) -> dict[str, Any]:
    """Return a code's definition, or raise if the code is not in the registry."""
    codes = _load()["codes"]
    if code not in codes:
        raise KeyError(f"error code {code!r} is not in the closed registry")
    return dict(codes[code])
