import { test } from "node:test";
import assert from "node:assert/strict";
import {
  TrueFigureClient,
  TrueFigureError,
  activity,
  lifecycle,
  buildEvent,
  fixOwner,
} from "../dist/index.js";

function makeTransport(script) {
  const calls = [];
  const transport = async (method, url, headers, body) => {
    calls.push({ method, url, headers, body });
    return script.shift() ?? { status: 200, headers: {}, body: okEnv() };
  };
  transport.calls = calls;
  return transport;
}
const okEnv = (results = [], requestId = "req-1") => ({
  data: { results }, meta: { schema_version: "1.0", request_id: requestId, as_of: "now" }, errors: [],
});
const accepted = (n) => Array.from({ length: n }, (_, i) => ({ index: i, status: "accepted", event_key: `k${i}` }));
const noSleep = async () => {};

test("builder shapes the public envelope and computes no key", () => {
  const e = activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "draft_generated" });
  assert.deepEqual(e, {
    schema_version: "1.0", deployment_id: "dep-1", event_type: "activity", origin: "customer_system",
    payload: { user_ref: "u1", timestamp: "2026-07-01T00:00:00Z", work_item_id: "w1", action_type: "draft_generated" },
  });
  assert.ok(!("_event_key" in e));
});

test("builder does not canonicalize the timestamp (server does)", () => {
  const e = lifecycle("dep-1", { workItemId: "w1", event: "completed", timestamp: "2026-07-15T15:02:11+01:00" });
  assert.equal(e.payload.timestamp, "2026-07-15T15:02:11+01:00");
});

test("builder forwards extra payload fields and does not validate enums", () => {
  const e = activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "anything", modelVersionRef: "gpt-x" });
  assert.equal(e.payload.action_type, "anything");
  assert.equal(e.payload.modelVersionRef, "gpt-x");
});

test("buildEvent generic with idempotency scope", () => {
  const e = buildEvent("dep-1", "revenue_signal", { work_item_id: "w1", revenue_ref: "DEAL", timestamp: "2026-07-01T00:00:00Z" }, { idempotencyScope: "q3" });
  assert.equal(e.event_type, "revenue_signal");
  assert.equal(e.idempotency_scope, "q3");
});

test("flush sends the batch and surfaces SERVER-assigned keys", async () => {
  const t = makeTransport([{ status: 200, headers: {}, body: okEnv(accepted(2)) }]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sleep: noSleep });
  assert.equal(c.track(activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "draft_generated" })), 1);
  assert.equal(c.track(activity("dep-1", { userRef: "u2", timestamp: "2026-07-01T00:00:00Z", workItemId: "w2", actionType: "draft_generated" })), 2);
  const br = await c.flush();
  assert.equal(br.accepted.length, 2);
  assert.deepEqual(br.accepted.map((r) => r.event_key), ["k0", "k1"]);
  const sent = t.calls[0].body.events;
  assert.ok(sent.every((e) => !("_event_key" in e)));
  assert.equal(t.calls[0].headers["X-TrueFigure-Mode"], "production");
});

test("ingest passthrough posts raw envelopes verbatim", async () => {
  const t = makeTransport([{ status: 200, headers: {}, body: okEnv(accepted(1)) }]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sleep: noSleep });
  const env = activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "draft_generated" });
  const br = await c.ingest([env]);
  assert.equal(br.accepted.length, 1);
  assert.deepEqual(t.calls[0].body.events, [env]);
});

test("retry on 429 honors retry_after then succeeds", async () => {
  const rateEnv = { data: null, meta: { request_id: "r" }, errors: [{ code: "TF-RATE-001", message: "slow", retryable: true, retry_after: 2, request_id: "r" }] };
  const t = makeTransport([
    { status: 429, headers: {}, body: rateEnv },
    { status: 200, headers: {}, body: okEnv(accepted(1)) },
  ]);
  const waits = [];
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sleep: async (ms) => { waits.push(ms); } });
  c.track(activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "draft_generated" }));
  const br = await c.flush();
  assert.equal(br.accepted.length, 1);
  assert.deepEqual(waits, [2000]);
});

test("non-retryable error raises typed error with code and fix owner", async () => {
  const errEnv = { data: null, meta: { request_id: "r9" }, errors: [{ code: "TF-EVT-002", message: "unknown deployment", retryable: false, request_id: "r9" }] };
  const t = makeTransport([{ status: 404, headers: {}, body: errEnv }]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "ghost", sleep: noSleep });
  await assert.rejects(() => c.figures(), (e) => {
    assert.ok(e instanceof TrueFigureError);
    assert.equal(e.errors[0].code, "TF-EVT-002");
    assert.equal(e.errors[0].fixOwner, "config_owner");
    return true;
  });
});

test("read plane refused figures arrive as data, not errors", async () => {
  const fig = { figures: [{ figure_id: "f1", status: "refused", margin_exceeds_effect: true }] };
  const t = makeTransport([{ status: 200, headers: {}, body: { data: fig, meta: { request_id: "r" }, errors: [] } }]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sleep: noSleep });
  const data = await c.figures(undefined, "2026-Q2");
  assert.equal(data.figures[0].status, "refused");
  assert.match(t.calls[0].url, /\/v1\/figures\/dep-1\?period=2026-Q2$/);
});

test("source ref header attached; echo uses test mode", async () => {
  const t = makeTransport([{ status: 200, headers: {}, body: okEnv(accepted(1)) }]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sourceRef: "aims_export", sleep: noSleep });
  await c.echo([activity("dep-1", { userRef: "u1", timestamp: "2026-07-01T00:00:00Z", workItemId: "w1", actionType: "draft_generated" })]);
  assert.equal(t.calls[0].headers["X-TrueFigure-Source"], "aims_export");
  assert.equal(t.calls[0].headers["X-TrueFigure-Mode"], "test");
});

test("config-plane bindings hit the right method and path", async () => {
  const t = makeTransport([
    { status: 200, headers: {}, body: { data: { workspace_ref: "ws_1" }, meta: { request_id: "r" }, errors: [] } },
    { status: 201, headers: {}, body: { data: { change_event_id: "chg_1" }, meta: { request_id: "r" }, errors: [] } },
  ]);
  const c = new TrueFigureClient("key", { transport: t, deploymentId: "dep-1", sleep: noSleep });
  const who = await c.whoami();
  assert.equal(who.workspace_ref, "ws_1");
  const ce = await c.declareChangeEvent({ type: "system_cutover", occurred_at: "2026-10-26T00:00:00Z", sidedness: "one_sided", description: "cutover" });
  assert.equal(ce.change_event_id, "chg_1");
  assert.equal(t.calls[1].method, "POST");
  assert.ok(t.calls[1].url.endsWith("/v1/change-events"));
});

test("fixOwner reads the public catalog", () => {
  assert.equal(fixOwner("TF-EVT-001"), "integrator");
  assert.equal(fixOwner("TF-NONEXISTENT-999"), "unknown");
});
