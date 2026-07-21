# TrueFigure Python SDK

The official Python client for the TrueFigure API. It is a **convenience client**
— it does real client-side work to make integration pleasant — while the
**server remains authoritative** for everything that matters.

```python
from truefigure import TrueFigureClient
tf = TrueFigureClient(api_key, deployment_id="dep_1")
tf.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                  work_item_id="w1", action_type="suggestion_accepted")
result = tf.flush()          # -> BatchResult(accepted, duplicates, rejected)
```

## What the SDK does (client-side convenience)

- **Auth header handling** — `Authorization: Bearer …`, optional `X-TrueFigure-Source`
- **Request construction & (de)serialization** — typed builders assemble the
  public event envelopes; JSON in/out
- **Public-contract input validation** — required fields, public enum membership
  (`action_type`, `meter`, `signal`, …), timezone-aware timestamps, `quantity >= 0`,
  fast and friendly — *before* a round-trip
- **Idempotency `event_key`** — computed locally so you get an immediate handle;
  the server recomputes it authoritatively (the client copy is a convenience)
- **Retries & backoff** — contract-driven: only `retryable` / HTTP 429 / 5xx;
  honors `retry_after`
- **Timeouts** — per-client, configurable (`timeout=`)
- **Cursor pagination** — `paginate()`, `iter_roster()`, `iter_deployments()`, …
- **File upload handling** — `upload_import()` PUTs NDJSON to the signed URL
- **Async job polling** — `wait_for_import()` polls to a terminal state
- **Error mapping** — typed `TrueFigureError` / `RateLimited` with the closed
  `TF-<PLANE>-<NNN>` codes and `fix_owner`
- **Webhook signature verification** — `truefigure.verify(secret, headers, body)`
  (standard HMAC; the customer holds the secret)
- **Convenience workflows** — `provision()` (deployment + license + parameters)
- **Offline buffering** — durable JSONL spool; replay is safe (server idempotency)
- **Language-specific types** — `truefigure.types` TypedDicts for responses

## What the SDK does NOT contain (server-authoritative core)

The proprietary/core logic lives only in the server's `domain` layer and can
never be lifted from the client: the **measurement engine**, grade computation,
refusal/margin logic, **thresholds that materially affect results**, **pricing**,
identity resolution, and compliance enforcement. The SDK witnesses facts and
reads results; it never values anything itself. The server always re-validates,
re-canonicalizes, recomputes the key, deduplicates, and measures.

## Examples

```python
# Bulk backfill: upload NDJSON and wait for the job to finish
evs = [events.activity("dep_1", user_ref=u, timestamp=t, work_item_id=w, action_type="draft_generated")
       for (u, t, w) in rows]
status = tf.upload_import(evs, kind="backfill", wait=True)   # -> {"status": "completed", ...}

# Paginate the roster
for user in tf.iter_roster(status="active"):
    ...

# One-call bring-up
tf.provision(name="Acme", type="saas_tool",
             license={"seats_paid": 50, "valid_from": "2026-01-01", "price_per_seat": 100.0},
             parameters={"labor_rates": {"default": 40}}, effective_from="2026-01-01")

# Verify an inbound webhook before trusting it
from truefigure import verify, WebhookVerificationError
try:
    verify(webhook_secret, request.headers, request.raw_body)   # raises on bad signature/stale ts
    event = json.loads(request.raw_body)
except WebhookVerificationError:
    return 400
```

## Test

```sh
cd python && python -m pytest tests/ -q
```
