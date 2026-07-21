/**
 * Event envelope builders — pure shaping of the PUBLIC wire contract.
 *
 * THIN BY CONSTRUCTION. No event_key, no timestamp canonicalization, no
 * dedup-scope rules, no enum whitelists, no business-rule validation, no
 * value/grade/margin math. Each builder only assembles the request envelope
 * described by openapi.yaml + events.schema.json. The server owns validation,
 * identity, deduplication, canonicalization, and measurement.
 */
import type { EventEnvelope } from "./types.js";

export const SCHEMA_VERSION = "1.0";

export interface BuildOpts {
  origin?: string;
  idempotencyScope?: string;
}

export function buildEvent(
  deploymentId: string,
  eventType: string,
  payload: Record<string, unknown>,
  opts: BuildOpts = {},
): EventEnvelope {
  const env: EventEnvelope = {
    schema_version: SCHEMA_VERSION,
    deployment_id: deploymentId,
    event_type: eventType,
    origin: opts.origin ?? "customer_system",
    payload: { ...payload },
  };
  if (opts.idempotencyScope != null) env.idempotency_scope = opts.idempotencyScope;
  return env;
}

function shape(
  deploymentId: string,
  eventType: string,
  fixed: Record<string, unknown>,
  extra: Record<string, unknown>,
  opts: BuildOpts,
): EventEnvelope {
  const payload: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(fixed)) if (v !== undefined) payload[k] = v;
  Object.assign(payload, extra);
  return buildEvent(deploymentId, eventType, payload, opts);
}

export function activity(
  deploymentId: string,
  p: { userRef: string; timestamp: string; workItemId: string; actionType: string } & BuildOpts & Record<string, unknown>,
): EventEnvelope {
  const { userRef, timestamp, workItemId, actionType, origin, idempotencyScope, ...extra } = p;
  return shape(deploymentId, "activity",
    { user_ref: userRef, timestamp, work_item_id: workItemId, action_type: actionType },
    extra, { origin, idempotencyScope });
}

export function lifecycle(
  deploymentId: string,
  p: { workItemId: string; event: string; timestamp: string } & BuildOpts & Record<string, unknown>,
): EventEnvelope {
  const { workItemId, event, timestamp, origin, idempotencyScope, ...extra } = p;
  return shape(deploymentId, "lifecycle",
    { work_item_id: workItemId, event, timestamp }, extra, { origin, idempotencyScope });
}

export function costMeter(
  deploymentId: string,
  p: { meter: string; quantity: number; timestamp: string } & BuildOpts & Record<string, unknown>,
): EventEnvelope {
  const { meter, quantity, timestamp, origin, idempotencyScope, ...extra } = p;
  return shape(deploymentId, "cost_meter",
    { meter, quantity, timestamp }, extra, { origin, idempotencyScope });
}

export function qualitySignal(
  deploymentId: string,
  p: { workItemId: string; signal: string; timestamp: string } & BuildOpts & Record<string, unknown>,
): EventEnvelope {
  const { workItemId, signal, timestamp, origin, idempotencyScope, ...extra } = p;
  return shape(deploymentId, "quality_signal",
    { work_item_id: workItemId, signal, timestamp }, extra, { origin, idempotencyScope });
}

export function revenueSignal(
  deploymentId: string,
  p: { workItemId: string; revenueRef: string; timestamp: string } & BuildOpts & Record<string, unknown>,
): EventEnvelope {
  const { workItemId, revenueRef, timestamp, origin, idempotencyScope, ...extra } = p;
  return shape(deploymentId, "revenue_signal",
    { work_item_id: workItemId, revenue_ref: revenueRef, timestamp }, extra, { origin, idempotencyScope });
}
