/**
 * @truefigure/sdk/browser — browser-safe entry (no node:fs offline buffer).
 *
 * Identical thin client as the main entry, minus FileOfflineBuffer. Import this
 * from browser/React code so no Node built-ins enter the bundle. Consumers that
 * want durable spooling in the browser can implement the OfflineBuffer interface
 * (e.g. over localStorage/IndexedDB) and assign it to `client.buffer`.
 */
export { TrueFigureClient, BatchResult } from "./core.js";
export type { OfflineBuffer } from "./core.js";
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
