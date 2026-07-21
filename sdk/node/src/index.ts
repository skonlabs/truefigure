/**
 * @truefigure/sdk — thin TypeScript client for Node 18+ and browsers.
 *
 * Transport + auth + envelope parsing + typed errors + contract-driven retry +
 * event batching + optional offline buffer. Zero proprietary/core logic: no
 * event_key, no canonicalization, no enum/business validation, no measurement.
 */
export { TrueFigureClient, BatchResult } from "./core.js";
export type { OfflineBuffer } from "./core.js";
export { FileOfflineBuffer } from "./buffer.js";
export {
  TrueFigureError,
  RateLimited,
  toErrorObject,
  fixOwner,
  type ErrorObject,
} from "./errors.js";
export * as events from "./events.js";
export { buildEvent, activity, lifecycle, costMeter, qualitySignal, revenueSignal } from "./events.js";
export type {
  Envelope,
  EventEnvelope,
  EventResult,
  Transport,
  ClientOptions,
} from "./types.js";

export const VERSION = "0.1.0";
