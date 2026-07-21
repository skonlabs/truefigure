# TrueFigure SDKs — Python · Node · React

Official client SDKs for the TrueFigure API. **All three are thin by
construction.** They move data over HTTP and nothing more.

## The thin-client guarantee

Every shipped SDK does exactly this and no more:

- attach auth (`Authorization: Bearer …`) and optional `X-TrueFigure-Source`
- shape the **public** request envelopes documented in `openapi.yaml` /
  `schemas/events.schema.json`
- parse the `{ data, meta, errors }` response envelope
- map errors to typed exceptions with the closed `TF-<PLANE>-<NNN>` codes
- contract-driven retry/backoff (only `retryable` / HTTP 429 / 5xx; honor
  `retry_after`)
- batch events and (Node/Python) optionally spool them to a durable buffer

## What is deliberately NOT in any SDK

The proprietary/core logic lives **only on the server** and is never shipped to
a client, so it cannot be lifted, cloned, or reverse-engineered from a bundle:

| Server-only (never in an SDK) | Why it stays server-side |
|---|---|
| `event_key` SHA-256 digest | reveals the idempotency/identity algorithm |
| per-family **dedup scope** rules (spec 3.7) | reveals how facts are de-duplicated |
| timestamp **canonicalization** (spec 3.8) | part of the identity algorithm |
| enum / business-rule **validation** (BR-001…022) | the measurement contract |
| grade / margin / refusal / **figure math** | the core valuation engine |
| identity resolution, roster matching, labor rates | proprietary resolution |

A client sends a **raw** event envelope (with the timestamp verbatim); the
server validates it, canonicalizes it, computes the `event_key`, deduplicates,
and returns the authoritative key in the batch result. Re-sending the same fact
is a server-side no-op. The client never needs — and never has — any of that
logic.

This is enforced mechanically in CI:

```sh
grep -RInE 'hashlib|sha256|canonical_ts|natural_key|_event_key' \
  python/truefigure sdk/node/src sdk/react/src   # must match nothing
```

## Packages

| Package | Path | Runtime |
|---|---|---|
| `truefigure` (Python) | [`python/`](../python) | Python 3.11+ |
| `@truefigure/sdk` (Node/TS) | [`node/`](./node) | Node 18+ / browsers |
| `@truefigure/react` (React) | [`react/`](./react) | React 17+ (browser) |

`@truefigure/react` is a Provider + hooks layer over `@truefigure/sdk` — UI
ergonomics only, no logic of its own. In the browser, **use a scoped read-only
key**: anything shipped to a browser is public.

## Quick start

**Python**
```python
from truefigure import TrueFigureClient, events
tf = TrueFigureClient(api_key, deployment_id="dep_1")
tf.track_activity(user_ref="u1", timestamp="2026-07-01T00:00:00Z",
                  work_item_id="w1", action_type="suggestion_accepted")
result = tf.flush()                      # server assigns the event_keys
print(result.accepted[0]["event_key"])   # authoritative key from the server
```

**Node / TypeScript**
```ts
import { TrueFigureClient, activity } from "@truefigure/sdk";
const tf = new TrueFigureClient(apiKey, { deploymentId: "dep_1" });
tf.track(activity("dep_1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z",
                             workItemId: "w1", actionType: "suggestion_accepted" }));
const result = await tf.flush();
console.log(result.accepted[0].event_key);
```

**React**
```tsx
import { TrueFigureProvider, useLiveUsage } from "@truefigure/react";

function Dashboard() {
  const { data, loading, error } = useLiveUsage("dep_1");
  if (loading) return <Spinner/>;
  if (error) return <Error code={error.errors?.[0]?.code}/>;
  return <SeatWaste usage={data}/>;
}

<TrueFigureProvider apiKey={READ_ONLY_KEY} baseUrl="https://api.truefigure.io">
  <Dashboard/>
</TrueFigureProvider>
```
