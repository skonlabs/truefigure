"""Consumer-side webhook signature verification.

TrueFigure signs each webhook with:
    X-TrueFigure-Timestamp: <unix seconds>
    X-TrueFigure-Signature: v1=hex(hmac_sha256(secret, f"{timestamp}.{raw_body}"))

This is a standard, public HMAC scheme (the customer already holds the shared
secret) — it is client-side convenience, not proprietary logic. Verify the
signature on the EXACT raw request body, before JSON parsing, and reject stale
timestamps to prevent replay.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping, Optional, Union

_Body = Union[bytes, str]


class WebhookVerificationError(Exception):
    """Raised when a webhook signature is missing, malformed, stale, or invalid."""


def _sign(secret: str, timestamp: int, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def _as_bytes(raw_body: _Body) -> bytes:
    return raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body


def verify_signature(secret: str, timestamp: Union[int, str], raw_body: _Body, signature: str,
                     *, tolerance_s: Optional[int] = 300) -> bool:
    """Return True iff `signature` is a valid v1 HMAC for (timestamp, raw_body).

    Constant-time compare. If tolerance_s is set, timestamps older/newer than that
    many seconds are rejected (replay protection). Returns False on any mismatch.
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if tolerance_s is not None and abs(int(time.time()) - ts) > tolerance_s:
        return False
    expected = _sign(secret, ts, _as_bytes(raw_body))
    return hmac.compare_digest(expected, signature or "")


def verify(secret: str, headers: Mapping[str, str], raw_body: _Body,
           *, tolerance_s: Optional[int] = 300) -> None:
    """Verify a webhook from its headers; raise WebhookVerificationError if invalid.

    Header lookup is case-insensitive. On success returns None; the caller may then
    safely json.loads(raw_body).
    """
    lower = {k.lower(): v for k, v in headers.items()}
    ts = lower.get("x-truefigure-timestamp")
    sig = lower.get("x-truefigure-signature")
    if not ts or not sig:
        raise WebhookVerificationError("missing X-TrueFigure-Timestamp/Signature headers")
    if not verify_signature(secret, ts, raw_body, sig, tolerance_s=tolerance_s):
        raise WebhookVerificationError("webhook signature invalid or timestamp out of tolerance")
