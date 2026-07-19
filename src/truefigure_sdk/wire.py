"""The wire-name map (normative, §2.4 header of schema.sql; build-spec §4).

The API speaks WIRE names; storage uses COLUMN names. Exactly 13 documented
places differ. This module is the single owner of that map, tested in both
directions. Everything not listed here is same-named on both sides.

Enum VALUES are identical on wire and in storage (lowercase snake_case, one
register) — only these field NAMES differ, plus two value transforms
(the api-key prefix and, handled by the change-events writer, `supersedes`).
"""

from __future__ import annotations

# (resource, wire_field, column_name). Each row is one documented rename.
RENAMES: tuple[tuple[str, str, str], ...] = (
    ("roster", "kind", "user_type"),
    ("roster", "role", "job_role"),
    ("imports", "kind", "import_type"),
    ("deployments", "type", "deployment_type"),
    ("deployments", "mode", "deployment_mode"),
    ("change_events", "type", "change_type"),
    ("change_events", "sidedness", "sided_type"),
    # `supersedes` is not a plain column: it sets superseded_by_id ON THE OLD ROW
    # (the change-events writer owns that direction). Recorded here for completeness.
    ("change_events", "supersedes", "superseded_by_id"),
    ("license", "period_unit", "period_unit_type"),
    ("webhooks", "events", "webhook_event_type"),
    ("lifecycle", "size_band", "size_type"),
    ("envelope", "origin", "origin_type"),
    # The api-key prefix (tf_live_/tf_test_) encodes api_key_mode; value transform
    # via key_prefix_to_mode / mode_to_key_prefix below.
    ("api_key", "prefix", "api_key_mode"),
)

_WIRE_TO_COLUMN: dict[str, dict[str, str]] = {}
_COLUMN_TO_WIRE: dict[str, dict[str, str]] = {}
for _resource, _wire, _column in RENAMES:
    _WIRE_TO_COLUMN.setdefault(_resource, {})[_wire] = _column
    _COLUMN_TO_WIRE.setdefault(_resource, {})[_column] = _wire


def to_column(resource: str, wire_field: str) -> str:
    """Wire field name -> storage column name (identity if not remapped)."""
    return _WIRE_TO_COLUMN.get(resource, {}).get(wire_field, wire_field)


def to_wire(resource: str, column_name: str) -> str:
    """Storage column name -> wire field name (identity if not remapped)."""
    return _COLUMN_TO_WIRE.get(resource, {}).get(column_name, column_name)


# ---- api-key prefix <-> api_key_mode value transform ------------------------
_PREFIX_TO_MODE = {"tf_live_": "production", "tf_test_": "test"}
_MODE_TO_PREFIX = {v: k for k, v in _PREFIX_TO_MODE.items()}


def key_prefix_to_mode(key: str) -> str | None:
    """Map a presented api key to its api_key_mode by prefix, or None if unknown."""
    for prefix, mode in _PREFIX_TO_MODE.items():
        if key.startswith(prefix):
            return mode
    return None


def mode_to_key_prefix(mode: str) -> str:
    return _MODE_TO_PREFIX[mode]
