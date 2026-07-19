"""TrueFigureClient: thin reference client.

Design rules (from the SDK specification):
- Thin: typed bindings + batching + retry/backoff + offline buffering + transport. Zero business logic.
- Every log line is structured JSON with request_id for client<->server correlation.
- Retry is contract-driven: only errors with retryable=true (or HTTP 429/5xx) retry; backoff honors retry_after.
- At-least-once delivery + server idempotency (natural keys) = exactly-once effect.
- Test mode (parse-echo) is the same endpoint with a header, never a separate system.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Callable, Optional

from .errors import ErrorObject, TrueFigureError, RateLimited
from . import events as ev

logger = logging.getLogger("truefigure")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        base = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for attr in ("request_id", "deployment_id", "event_key", "code", "batch_size"):
            v = getattr(record, attr, None)
            if v is not None:
                base[attr] = v
        return json.dumps(base)


def configure_logging(level=logging.INFO):
    h = logging.StreamHandler()
    h.setFormatter(_JsonFormatter())
    logger.handlers = [h]
    logger.setLevel(level)


class OfflineBuffer:
    """JSONL spool: events survive process crashes and network outages; replay is idempotent-safe."""

    def __init__(self, path: str):
        self.path = path

    def append(self, envelopes: list[dict]):
        with open(self.path, "a", encoding="utf-8") as f:
            for e in envelopes:
                f.write(json.dumps(e) + "\n")

    def drain(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                items = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
        open(self.path, "w").close()
        return items


class BatchResult:
    def __init__(self, results: list[dict], request_id: str):
        self.results = results
        self.request_id = request_id
        self.accepted = [r for r in results if r.get("status") == "accepted"]
        self.duplicates = [r for r in results if r.get("status") == "duplicate"]
        self.rejected = [r for r in results if r.get("status") == "rejected"]

    def __repr__(self):
        return (f"BatchResult(accepted={len(self.accepted)}, duplicates={len(self.duplicates)}, "
                f"rejected={len(self.rejected)}, request_id={self.request_id})")


class TrueFigureClient:
    MAX_BATCH = 500

    def __init__(self, api_key: str, *, base_url: str = "https://api.truefigure.io",
                 deployment_id: Optional[str] = None, buffer_path: Optional[str] = None,
                 max_retries: int = 5, timeout: float = 10.0, source_ref: Optional[str] = None,
                 transport: Optional[Callable[[str, str, dict, dict], tuple[int, dict, dict]]] = None):
        """transport(method, url, headers, body_dict) -> (status, headers, body_dict); injectable for tests."""
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.deployment_id = deployment_id
        self.max_retries = max_retries
        self.timeout = timeout
        self.source_ref = source_ref
        self.buffer = OfflineBuffer(buffer_path) if buffer_path else None
        self._pending: list[dict] = []
        self._transport = transport or self._http

    # ---------------- event enqueue API ----------------
    def track_activity(self, **kw) -> str:
        return self._enqueue(ev.activity(self._dep(kw.pop("deployment_id", None)), **kw))

    def track_lifecycle(self, **kw) -> str:
        return self._enqueue(ev.lifecycle(self._dep(kw.pop("deployment_id", None)), **kw))

    def track_cost(self, **kw) -> str:
        return self._enqueue(ev.cost_meter(self._dep(kw.pop("deployment_id", None)), **kw))

    def track_quality(self, **kw) -> str:
        return self._enqueue(ev.quality_signal(self._dep(kw.pop("deployment_id", None)), **kw))

    def track_revenue(self, **kw) -> str:
        return self._enqueue(ev.revenue_signal(self._dep(kw.pop("deployment_id", None)), **kw))

    def _dep(self, override):
        d = override or self.deployment_id
        if not d:
            raise ValueError("deployment_id required (constructor default or per-call)")
        return d

    def _enqueue(self, envelope: dict) -> str:
        key = envelope["_event_key"]
        self._pending.append(envelope)
        if len(self._pending) >= self.MAX_BATCH:
            self.flush()
        return key

    # ---------------- flush / echo ----------------
    def flush(self, mode: str = "production") -> BatchResult:
        batch, self._pending = self._pending, []
        if self.buffer:
            batch = self.buffer.drain() + batch
        if not batch:
            return BatchResult([], "")
        results, req_id = [], ""
        for i in range(0, len(batch), self.MAX_BATCH):
            chunk = batch[i:i + self.MAX_BATCH]
            wire = [{k: v for k, v in e.items() if k != "_event_key"} for e in chunk]
            try:
                env = self._request("POST", "/v1/events:batch", {"events": wire},
                                    extra_headers={"X-TrueFigure-Mode": mode})
            except (TrueFigureError,) as err:
                if self.buffer:
                    self.buffer.append(chunk)
                    logger.warning("flush failed; %d events spooled to offline buffer",
                                   len(chunk), extra={"request_id": err.request_id, "batch_size": len(chunk)})
                    continue
                self._pending = chunk + self._pending
                raise
            req_id = env["meta"]["request_id"]
            for r in env["data"]["results"]:
                results.append(r)
                if r.get("status") == "rejected":
                    e = r.get("error", {})
                    logger.error("event rejected", extra={
                        "request_id": req_id, "event_key": r.get("event_key"),
                        "code": e.get("code"),
                    })
        br = BatchResult(results, req_id)
        logger.info("flush complete: %r", br, extra={"request_id": req_id, "batch_size": len(batch)})
        return br

    def echo(self, envelopes: Optional[list[dict]] = None) -> BatchResult:
        """Parse-echo test mode: same endpoint, header-switched; stores nothing server-side."""
        if envelopes is None:
            envelopes, self._pending = self._pending, []
        wire = [{k: v for k, v in e.items() if k != "_event_key"} for e in envelopes]
        env = self._request("POST", "/v1/events:batch", {"events": wire},
                            extra_headers={"X-TrueFigure-Mode": "test"})
        return BatchResult(env["data"]["results"], env["meta"]["request_id"])

    # ---------------- read plane ----------------
    def event_status(self, event_key: str) -> dict:
        return self._request("GET", f"/v1/events/{event_key}/status")["data"]

    def live_usage(self, deployment_id: Optional[str] = None) -> dict:
        return self._request("GET", f"/v1/live/usage/{self._dep(deployment_id)}")["data"]

    def live_cost(self, deployment_id: Optional[str] = None) -> dict:
        return self._request("GET", f"/v1/live/cost/{self._dep(deployment_id)}")["data"]

    def integration_health(self, deployment_id: Optional[str] = None) -> dict:
        return self._request("GET", f"/v1/live/health/{self._dep(deployment_id)}")["data"]

    def figures(self, deployment_id: Optional[str] = None, period: Optional[str] = None) -> dict:
        """Finalized computations. refused figures arrive here as data, not errors (BR-005)."""
        q = f"?period={period}" if period else ""
        return self._request("GET", f"/v1/figures/{self._dep(deployment_id)}{q}")["data"]

    # ---------------- config plane ----------------
    def register_deployment(self, name: str, type: str, external_ref: Optional[str] = None,
                            planned_rollout_at: Optional[str] = None,
                            service_user_refs: Optional[list] = None) -> dict:
        body = {"name": name, "type": type}
        if external_ref: body["external_ref"] = external_ref
        if planned_rollout_at: body["planned_rollout_at"] = planned_rollout_at
        if service_user_refs: body["service_user_refs"] = service_user_refs
        return self._request("POST", "/v1/deployments", body)["data"]

    def create_parameter_version(self, parameters: dict, effective_from: str) -> dict:
        return self._request("POST", "/v1/parameters",
                             {"parameters": parameters, "effective_from": effective_from})["data"]

    def whoami(self) -> dict:
        """Key self-check: workspace, environment, mode, scope, roles, rate limits (C-startup)."""
        return self._request("GET", "/v1/whoami")["data"]

    def declare_change_event(self, type: str, occurred_at: str, sidedness: str,
                             description: str, scope_deployments: Optional[list] = None) -> dict:
        body = {"type": type, "occurred_at": occurred_at, "sidedness": sidedness, "description": description}
        if scope_deployments: body["scope_deployments"] = scope_deployments
        return self._request("POST", "/v1/change-events", body)["data"]

    def create_import(self, kind: str, expected_events: Optional[int] = None) -> dict:
        body = {"kind": kind}
        if expected_events: body["expected_events"] = expected_events
        if self.source_ref: body["source_ref"] = self.source_ref
        return self._request("POST", "/v1/imports", body)["data"]

    def import_status(self, import_id: str) -> dict:
        return self._request("GET", f"/v1/imports/{import_id}")["data"]

    def alerts(self, deployment_id: Optional[str] = None, status: str = "open") -> dict:
        q = f"?status={status}" + (f"&deployment_id={deployment_id}" if deployment_id else "")
        return self._request("GET", f"/v1/alerts{q}")["data"]

    def ack_alert(self, alert_id: str) -> dict:
        return self._request("POST", f"/v1/alerts/{alert_id}:ack")["data"]

    def reports(self, deployment_id: Optional[str] = None, period: Optional[str] = None) -> dict:
        q = f"?period={period}" if period else ""
        return self._request("GET", f"/v1/reports/{self._dep(deployment_id)}{q}")["data"]

    def figure_lineage(self, figure_id: str, deployment_id: Optional[str] = None) -> dict:
        return self._request("GET", f"/v1/figures/{self._dep(deployment_id)}/{figure_id}/lineage")["data"]

    # ---------------- transport with contract-driven retry ----------------
    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 extra_headers: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.source_ref:
            headers["X-TrueFigure-Source"] = self.source_ref
        if extra_headers:
            headers.update(extra_headers)
        url = self.base_url + path
        attempt, delay = 0, 0.5
        while True:
            attempt += 1
            status, rh, env = self._transport(method, url, headers, body or {})
            errors = [ErrorObject.from_dict(e) for e in env.get("errors", [])]
            req_id = env.get("meta", {}).get("request_id", rh.get("x-request-id", ""))
            if status < 400 and not errors:
                return env
            retryable = status == 429 or status >= 500 or any(e.retryable for e in errors)
            if retryable and attempt <= self.max_retries:
                wait = next((e.retry_after for e in errors if e.retry_after), None)
                wait = wait if wait is not None else int(rh.get("retry-after", 0)) or delay
                logger.warning("retryable failure (attempt %d), sleeping %ss", attempt, wait,
                               extra={"request_id": req_id, "code": errors[0].code if errors else str(status)})
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if status == 429:
                raise RateLimited(errors, req_id)
            raise TrueFigureError(errors or [ErrorObject("TF-SRV-001", f"HTTP {status}", True, req_id)], req_id)

    @staticmethod
    def _http(method: str, url: str, headers: dict, body: dict) -> tuple[int, dict, dict]:
        data = json.dumps(body).encode("utf-8") if method != "GET" else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                env = json.loads(e.read().decode("utf-8"))
            except Exception:
                env = {"data": None, "meta": {}, "errors": [{"code": "TF-SRV-001", "message": str(e), "retryable": e.code >= 500}]}
            return e.code, dict(e.headers or {}), env
