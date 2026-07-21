"""TrueFigure Python SDK — a convenience client for AI value verification.

Client-side responsibilities (developer ergonomics): auth headers, request
construction, (de)serialization, public-contract input validation, retries with
backoff, timeouts, cursor pagination, NDJSON file upload, async import polling,
error mapping, webhook signature verification, convenience workflows, and typed
results. The SDK also computes the idempotency event_key locally for immediate
handles — but the server is always AUTHORITATIVE: it re-validates, canonicalizes,
recomputes the key, deduplicates, and performs all measurement. Proprietary/core
logic (the measurement engine, grades, thresholds, pricing) never lives here.
"""
from .client import TrueFigureClient, BatchResult, OfflineBuffer, configure_logging
from .errors import TrueFigureError, RateLimited, ErrorObject, REGISTRY
from .webhooks import verify, verify_signature, WebhookVerificationError
from . import events, webhooks, types

__version__ = "0.1.0"
__all__ = ["TrueFigureClient", "BatchResult", "OfflineBuffer", "configure_logging",
           "TrueFigureError", "RateLimited", "ErrorObject", "REGISTRY",
           "verify", "verify_signature", "WebhookVerificationError",
           "events", "webhooks", "types"]
