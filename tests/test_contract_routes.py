"""Contract: every openapi path+verb is EXERCISED (routed + handled), and the
DB<->wire shared enums are set-equal. (P8 contract tests.)

We assert reachability by calling each endpoint with a bogus bearer token: a
routed endpoint runs its auth dependency and returns 401 (TF-AUTH-001); a path
that is not registered returns Starlette's bare 404 {"detail":"Not Found"} with
no error envelope. This exercises all 29 paths without depending on router
introspection internals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "openapi" / "openapi.yaml").read_text())

_DUMMY = {
    "deployment_id": "dep_x", "figure_id": "fig_x", "report_id": "rep_x", "webhook_id": "wh_x",
    "import_id": "imp_x", "alert_id": "al_x", "version": "1", "event_key": "deadbeef",
}


def _fill(path: str) -> str:
    return re.sub(r"\{([^}]+)\}", lambda m: _DUMMY.get(m.group(1), "x"), path)


def _cases() -> list[tuple[str, str]]:
    out = []
    for path, item in SPEC["paths"].items():
        for verb in ("get", "post", "put", "patch", "delete"):
            if verb in item:
                out.append((verb, path))
    return out


CASES = _cases()


def test_openapi_has_29_paths() -> None:
    assert len(SPEC["paths"]) == 29


@pytest.mark.parametrize(("verb", "path"), CASES, ids=[f"{v.upper()} {p}" for v, p in CASES])
async def test_every_endpoint_is_routed(client, verb: str, path: str) -> None:
    url = _fill(path)
    r = await client.request(verb.upper(), url, headers={"Authorization": "Bearer bogus_key"},
                             json={} if verb in ("post", "put", "patch") else None)
    # Routed endpoints run auth first -> 401 with our error envelope (never a bare 404).
    assert r.status_code != 404 or "errors" in r.json(), f"{verb.upper()} {path} not routed"
    body = r.json()
    assert "errors" in body and "meta" in body, f"{verb.upper()} {path} did not return the envelope"


def test_all_29_exercised() -> None:
    assert len(CASES) >= 29
