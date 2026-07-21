"""Language-specific typed views over the public response shapes.

These TypedDicts describe the PUBLIC response bodies documented in openapi.yaml.
They are developer-ergonomics types (editor autocomplete, mypy) only — no
proprietary logic. total=False because the server may add fields over time and
refused/awaiting figures omit value fields by design.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class Meta(TypedDict, total=False):
    schema_version: str
    request_id: str
    as_of: str
    deployment_id: str
    next_cursor: Optional[str]


class Whoami(TypedDict, total=False):
    org_ref: str
    plan_type: str
    workspace_ref: str
    environment: str
    mode: str
    scope: str
    roles: list[str]
    rate_limit_rpm: int


class Figure(TypedDict, total=False):
    figure_id: str
    version: int
    status: str            # computed | refused | awaiting_parameters
    grade: str             # estimate | measured | verified
    claim: str
    value: Any
    value_class: str
    period: str
    change_treatment: str
    margin_exceeds_effect: bool
    refusal_reason: str
    missing_parameter: str
    expected_verdict_date: str
    parameter_set_version: int
    lineage: dict[str, Any]


class SeatUsage(TypedDict, total=False):
    activated: int
    paid: Optional[int]
    never_activated: Optional[int]
    near_zero: int
    moderate: int
    heavy: int


class LiveUsage(TypedDict, total=False):
    grade: str
    seats: SeatUsage
    waste: dict[str, Any]
    cohort_status: str
    code: str


class BatchEventResult(TypedDict, total=False):
    index: int
    status: str            # accepted | duplicate | rejected
    event_key: str
    code: str
    error: dict[str, Any]
    echo: dict[str, Any]


class ImportStatus(TypedDict, total=False):
    import_id: str
    status: str            # queued | processing | completed | completed_with_rejects | failed
    received: int
    accepted: int
    duplicates: int
    rejected: int
    error_report_url: Optional[str]
