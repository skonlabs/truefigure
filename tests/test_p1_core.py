"""P1 — core plumbing: registry loading, error objects, response envelope."""

from __future__ import annotations

import pytest

from truefigure_sdk import envelope, registry
from truefigure_sdk.errors import TFError, error_object


def test_registry_loads_all_codes() -> None:
    codes = registry.all_codes()
    assert len(codes) == 26
    assert registry.registry_version()
    assert "TF-AUTH-001" in registry.code_ids()


def test_get_code_unknown_raises() -> None:
    with pytest.raises(KeyError):
        registry.get_code("TF-NOPE-999")


def test_tferror_rejects_invented_code() -> None:
    with pytest.raises(KeyError):
        TFError("TF-INVENTED-001")


def test_tferror_carries_registry_fields() -> None:
    err = TFError("TF-RATE-001", retry_after=30)
    assert err.http == 429
    assert err.retryable is True
    assert err.name == "RATE_LIMITED"
    obj = err.to_error_object()
    assert obj["code"] == "TF-RATE-001"
    assert obj["retry_after"] == 30


def test_error_object_with_field_path_and_detail() -> None:
    obj = error_object("TF-EVT-003", field_path="payload.content", detail="unknown field")
    assert obj["field_path"] == "payload.content"
    assert obj["detail"] == "unknown field"
    assert obj["retryable"] is False


def test_envelope_success_shape() -> None:
    env = envelope.envelope({"x": 1}, request_id="req_1", schema_version="1.0")
    assert env["data"] == {"x": 1}
    assert env["errors"] == []
    assert env["meta"]["schema_version"] == "1.0"
    assert env["meta"]["request_id"] == "req_1"
    assert "as_of" in env["meta"]


def test_envelope_optional_meta_fields() -> None:
    env = envelope.envelope(
        [],
        request_id="req_2",
        schema_version="1.0",
        deployment_id="dep_1",
        next_cursor="c2",
        as_of="2026-07-19T00:00:00.000000Z",
    )
    assert env["meta"]["deployment_id"] == "dep_1"
    assert env["meta"]["next_cursor"] == "c2"
    assert env["meta"]["as_of"] == "2026-07-19T00:00:00.000000Z"


def test_envelope_error_has_null_data() -> None:
    err = TFError("TF-AUTH-001")
    env = envelope.envelope(None, request_id="r", schema_version="1.0", errors=[err.to_error_object()])
    assert env["data"] is None
    assert env["errors"][0]["code"] == "TF-AUTH-001"
