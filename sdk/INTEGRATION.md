# TrueFigure — Integration Guide

This is everything you need to integrate with TrueFigure. You send us **facts**
(events); our service measures them and returns **graded figures**. You never run
any TrueFigure code beyond this thin client — all measurement happens on our
servers.

- **You need:** an API key, your base URL, and (optionally) a webhook secret. We
  provide these.
- **You install:** the `truefigure-sdk` Python package.
- **You talk to:** `https://api.truefigure.io` over HTTPS. Nothing else.

---

## 1. Install

```bash
pip install truefigure-sdk        # Python 3.9+ · zero dependencies
```

## 2. Authenticate

You'll receive two keys — a **test** key (`tf_test_…`) and a **production** key —
plus your base URL. Verify the key before anything else:

```python
from truefigure import TrueFigureClient

tf = TrueFigureClient(
    api_key="tf_live_…",
    base_url="https://api.truefigure.io",
    deployment_id="dep_1",      # optional default; can pass per-call instead
)

print(tf.whoami())   # -> workspace, environment, mode, scope, roles, rate-limit tier
```

Keep keys in a secret manager / environment variables — never in source control.

## 3. Concepts (30 seconds)

- **Deployment** — one measured rollout of your AI (a product, a team, a pilot).
- **Event** — a fact you witness. Five families: `activity`, `lifecycle`,
  `cost_meter`, `quality_signal`, `revenue_signal`.
- **Figure** — a finalized, graded computation we produce (cost, seat waste,
  containment, time-savings, …). A figure carries a **grade**
  (`estimate` → `measured` → `verified`) and may be **refused** when evidence is
  insufficient. **A refusal is a normal successful response, not an error.**
- **Idempotency** — resend the same fact freely; the server deduplicates it and
  returns the same `event_key`. At-least-once delivery + server dedup = exactly-once.

## 4. Emit events

Build events with the typed helpers (or pass raw dicts). Values for
`action_type`, `meter`, `signal`, etc. are defined in `openapi.yaml` /
`events.schema.json`.

```python
from truefigure import events

# enqueue + flush (batched; the server assigns each event_key)
tf.track_activity(user_ref="u_87", timestamp="2026-07-01T14:05:00Z",
                  work_item_id="CASE-42", action_type="suggestion_accepted")
tf.track_cost(meter="tokens_out", quantity=1820, timestamp="2026-07-01T14:05:00Z")
tf.track_lifecycle(work_item_id="CASE-42", event="completed", timestamp="2026-07-01T15:00:00Z")

result = tf.flush()                      # -> BatchResult(accepted, duplicates, rejected)
print([r["event_key"] for r in result.accepted])
```

**Event families & required payload fields**

| Family | Required fields | Example key values |
|---|---|---|
| `activity` | `user_ref`, `work_item_id`, `action_type`, `timestamp` | `suggestion_accepted`, `draft_generated`, `case_handled_autonomous` |
| `lifecycle` | `work_item_id`, `event`, `timestamp` | `created`, `status_change`, `completed` |
| `cost_meter` | `meter`, `quantity`, `timestamp` | `tokens_in`, `tokens_out`, `api_calls`, `seats_active` |
| `quality_signal` | `work_item_id`, `signal`, `timestamp` | `error_found`, `rework_required`, `qa_label` |
| `revenue_signal` | `work_item_id`, `revenue_ref`, `timestamp` | touchpoint: `influenced`, `closed_adjacent` |

Notes:
- `timestamp` is any ISO-8601 with a timezone (e.g. `…Z` or `+01:00`). The server
  canonicalizes it.
- `user_ref` / `work_item_id` are **your opaque IDs** — pseudonymous keys, never
  names or content. There is deliberately no field for prompt/response content.
- Extra payload fields are forwarded verbatim.

**Test mode first.** `echo()` runs the exact same pipeline but stores nothing —
use it to validate your mapping before going live:

```python
tf.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                  work_item_id="w1", action_type="draft_generated")
dry_run = tf.echo()          # parse + resolve + report; persists nothing
```

**Reliability.** Point the client at a spool file and failed flushes survive
crashes/outages; a later `flush()` replays them safely (dedup makes replay a no-op):

```python
tf = TrueFigureClient(api_key, deployment_id="dep_1", buffer_path="/var/spool/tf.jsonl")
```

## 5. Backfill (bulk history)

Upload historical events as NDJSON in one job (same schema, same dedup):

```python
history = [events.activity("dep_1", user_ref=u, timestamp=t,
                            work_item_id=w, action_type="draft_generated")
           for (u, t, w) in rows]

status = tf.upload_import(history, kind="backfill", wait=True)
print(status)   # -> {"status": "completed", "received": …, "accepted": …, "duplicates": …, "rejected": …}
```

## 6. Read results

```python
tf.figures("dep_1", period="2026-Q2")     # finalized figures (refused ones are DATA)
tf.get_figure("fig_cost_2026-07", "dep_1")
tf.figure_lineage("fig_cost_2026-07", "dep_1")   # full evidence chain

tf.live_usage("dep_1")           # seats: activated / paid / near-zero / heavy, waste
tf.live_cost("dep_1")            # priced consumption + projection
tf.integration_health("dep_1")   # accept / join / identity-resolution rates, drift

tf.reports("dep_1")              # issued reports (+ signed artifact URL)
tf.alerts(status="open"); tf.ack_alert("al_…")
```

Paginated lists stream transparently:

```python
for user in tf.iter_roster(status="active"):
    ...
```

## 7. Webhooks (optional push)

Subscribe once, then **verify every inbound delivery** before trusting it. We sign
each webhook with HMAC over `f"{timestamp}.{raw_body}"`; you hold the secret.

```python
tf.create_webhook(url="https://you.example/tf-hook",
                  events=["figure.updated", "alert.raised", "refusal.lifted", "report.issued"],
                  secret=WEBHOOK_SECRET)
```

```python
from truefigure import verify, WebhookVerificationError

# in your webhook handler — verify on the EXACT raw body, before JSON parsing:
try:
    verify(WEBHOOK_SECRET, request.headers, request.raw_body)   # raises if bad/stale
except WebhookVerificationError:
    return 400
event = json.loads(request.raw_body)
```

## 8. Errors & retries

The client retries transient failures automatically (HTTP 429/5xx and any
`retryable` error, honoring `Retry-After`). Non-retryable failures raise a typed
error whose code tells you **who fixes it**:

```python
from truefigure import TrueFigureError

try:
    tf.get_deployment("ghost")
except TrueFigureError as e:
    err = e.errors[0]
    print(err.code, err.message, err.fix_owner)   # fix_owner: integrator | config_owner | platform
```

Error codes are a closed registry (`TF-<PLANE>-<NNN>`) — safe to `switch` on. See
`error-codes.json`.

## 9. Endpoint ↔ method quick reference

| Area | Methods |
|---|---|
| Events | `track_activity/lifecycle/cost/quality/revenue`, `track`, `flush`, `ingest`, `echo`, `event_status` |
| Read | `figures`, `get_figure`, `figure_lineage`, `reports`, `get_report`, `live_usage`, `live_cost`, `integration_health`, `alerts`, `ack_alert` |
| Config | `register_deployment`, `list/get/update_deployment`, `declare_license`, `get_license`, `upsert_roster`, `list_roster`, `register_id_namespace`, `create_parameter_version`, `register_label_schema`, `create_webhook`, `create_mapping_contract`, `declare_change_event`, `provision` |
| Imports | `upload_import`, `create_import`, `import_status`, `wait_for_import` |

The full HTTP surface is in `openapi.yaml`; if you don't use Python, generate a
client from it — the wire contract is identical.

## Support

- **Contract:** `openapi.yaml`, `events.schema.json`, `error-codes.json` (shipped with this bundle).
- Reach your TrueFigure contact for keys, quota changes, and provisioning.
