"""TrueFigure SDK: the measurement contract for AI value verification.

The SDK witnesses facts (activity, lifecycle, cost meters, quality/revenue signals);
an independent server-side engine measures. There is no field for content, and no
field for value assertions - by construction.
"""
from .client import TrueFigureClient, BatchResult, OfflineBuffer, configure_logging
from .errors import TrueFigureError, RateLimited, ErrorObject, REGISTRY
from . import events

__version__ = "0.1.0"
__all__ = ["TrueFigureClient", "BatchResult", "OfflineBuffer", "configure_logging",
           "TrueFigureError", "RateLimited", "ErrorObject", "REGISTRY", "events"]
