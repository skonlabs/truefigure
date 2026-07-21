"""TrueFigure SDK: a THIN transport client for AI value verification.

The SDK only witnesses facts (activity, lifecycle, cost meters, quality/revenue
signals) and moves them over HTTP. It contains no proprietary logic: identity,
deduplication, timestamp canonicalization, validation, and all measurement are
performed by the independent server-side engine. Content and value-assertion
fields are rejected by the server, not the client.
"""
from .client import TrueFigureClient, BatchResult, OfflineBuffer, configure_logging
from .errors import TrueFigureError, RateLimited, ErrorObject, REGISTRY
from . import events

__version__ = "0.1.0"
__all__ = ["TrueFigureClient", "BatchResult", "OfflineBuffer", "configure_logging",
           "TrueFigureError", "RateLimited", "ErrorObject", "REGISTRY", "events"]
