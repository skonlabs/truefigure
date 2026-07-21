# @truefigure/sdk

Thin TypeScript client for the TrueFigure API. Works in Node 18+ and modern
browsers. **Transport only — no proprietary logic** (no `event_key`, no
timestamp canonicalization, no dedup-scope rules, no enum/business validation,
no grade/figure math). The server owns all of that; this client sends raw
envelopes and receives server-assigned `event_key`s back.

## Install & build

```sh
cd sdk/node
npm install
npm run build      # tsc -> dist/
npm test           # build + node --test
```

## Usage

```ts
import { TrueFigureClient, activity, TrueFigureError } from "@truefigure/sdk";

const tf = new TrueFigureClient(process.env.TF_API_KEY!, {
  baseUrl: "https://api.truefigure.io",
  deploymentId: "dep_1",
  sourceRef: "aims_export",   // optional X-TrueFigure-Source
});

// Enqueue + flush (server assigns keys, returned in the result)
tf.track(activity("dep_1", {
  userRef: "u1", timestamp: "2026-07-01T00:00:00Z",
  workItemId: "w1", actionType: "suggestion_accepted",
}));
const res = await tf.flush();
console.log(res.accepted.map((r) => r.event_key));

// Or post raw envelopes directly
await tf.ingest([{ schema_version: "1.0", deployment_id: "dep_1",
  event_type: "cost_meter", origin: "customer_system",
  payload: { meter: "api_calls", quantity: 12, timestamp: "2026-07-01T00:00:00Z" } }]);

// Read plane
const usage = await tf.liveUsage("dep_1");
const figs  = await tf.figures("dep_1", "2026-Q2");   // refused figures arrive as data

try {
  await tf.getDeployment("ghost");
} catch (e) {
  if (e instanceof TrueFigureError) console.error(e.errors[0].code, e.errors[0].fixOwner);
}
```

### Durable offline buffer (Node only)

```ts
import { TrueFigureClient, FileOfflineBuffer } from "@truefigure/sdk";
const tf = new TrueFigureClient(key, { deploymentId: "dep_1" });
tf.buffer = new FileOfflineBuffer("/var/spool/truefigure.jsonl");
// failed flushes spool to disk; a later flush() drains and replays (idempotent)
```

In the browser, import from `@truefigure/sdk/browser` (no `node:fs`) and, if you
need spooling, implement the `OfflineBuffer` interface over
localStorage/IndexedDB.

## Surface

All 38 API operations have a 1:1 method: events (`track`/`ingest`/`echo`/`flush`,
`eventStatus`), read plane (`figures`, `getFigure`, `figureLineage`, `reports`,
`getReport`, `liveUsage`, `liveCost`, `integrationHealth`, `alerts`, `ackAlert`),
and config plane (`whoami`, deployments, license, roster, id-namespaces,
parameters, qa-label schemas, webhooks, mapping-contracts, change-events,
imports).
