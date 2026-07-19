"""Server-issued opaque ids — prefix-typed, stable, <=64 chars (§1.1).

Documented prefixes: dep_, ns_, wh_, fig_, ls_. Additional internal resources
follow the same scheme (ce_ change events, mc_ mapping contracts, ql_ qa-label
schemas, dl_ webhook deliveries, al_ alerts, rp_ reports).
"""

from __future__ import annotations

import secrets

_PREFIXES = {
    "deployment": "dep_",
    "namespace": "ns_",
    "webhook": "wh_",
    "figure": "fig_",
    "license": "ls_",
    "change_event": "ce_",
    "mapping_contract": "mc_",
    "qa_label": "ql_",
    "delivery": "dl_",
    "alert": "al_",
    "report": "rp_",
    "import_job": "imp_",
    "api_key": "ak_",
    "work_item": "wi_",
    "alert_raw": "al_",
}


def new_ref(kind: str) -> str:
    prefix = _PREFIXES[kind]
    return prefix + secrets.token_hex(12)


def encode_id(kind: str, numeric_id: int) -> str:
    """Wire id for a table lacking a natural *_ref column: prefix + surrogate id."""
    return _PREFIXES[kind] + str(numeric_id)


def decode_id(kind: str, wire_id: str) -> int | None:
    """Inverse of encode_id; None if the wire id is not of the expected shape."""
    prefix = _PREFIXES[kind]
    if not wire_id.startswith(prefix):
        return None
    try:
        return int(wire_id[len(prefix):])
    except ValueError:
        return None
