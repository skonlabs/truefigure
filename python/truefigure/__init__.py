"""TrueFigure Python SDK — a convenience client for AI value verification.

Client-side responsibilities (transport ergonomics only): auth headers, request
construction, (de)serialization, retries with backoff, timeouts, cursor
pagination, NDJSON file upload, async import polling, error mapping, webhook
signature verification, convenience workflows, and typed results.

The SDK contains NO logic that a downloaded copy could leak: no event_key digest,
no timestamp canonicalization, no dedup-scope rules, no enum/business validation,
no measurement. It shapes envelopes and sends them; the server is the single
source of truth — it validates, canonicalizes, computes and returns the event_key,
deduplicates, and measures.
"""
from . import events, types, webhooks
from .client import BatchResult, OfflineBuffer, TrueFigureClient, configure_logging
from .errors import REGISTRY, ErrorObject, RateLimited, TrueFigureError
from .webhooks import WebhookVerificationError, verify, verify_signature

__version__ = "0.1.0"
__all__ = ["TrueFigureClient", "BatchResult", "OfflineBuffer", "configure_logging",
           "TrueFigureError", "RateLimited", "ErrorObject", "REGISTRY",
           "verify", "verify_signature", "WebhookVerificationError",
           "events", "webhooks", "types"]
