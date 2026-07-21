/**
 * @truefigure/react — thin React bindings over @truefigure/sdk.
 *
 * A UI-ergonomics layer (Provider + hooks) only. All measurement, identity, and
 * business logic stay server-side; this package computes nothing proprietary.
 * Re-exports the thin client surface for convenience.
 */
export { TrueFigureProvider, type TrueFigureProviderProps } from "./provider.js";
export { TrueFigureContext } from "./context.js";
export {
  useTrueFigure,
  useWhoami,
  useFigures,
  useFigure,
  useFigureLineage,
  useLiveUsage,
  useLiveCost,
  useIntegrationHealth,
  useReports,
  useReport,
  useAlerts,
  useTrack,
  type QueryOptions,
  type TrackApi,
} from "./hooks.js";
export { useAsyncResource, asyncReducer, initialAsyncState, type AsyncState } from "./useAsync.js";

// Convenience re-exports of the thin client surface.
export {
  TrueFigureClient,
  BatchResult,
  TrueFigureError,
  RateLimited,
  fixOwner,
  events,
  buildEvent,
  activity,
  lifecycle,
  costMeter,
  qualitySignal,
  revenueSignal,
  type EventEnvelope,
  type EventResult,
  type ClientOptions,
  type ErrorObject,
} from "@truefigure/sdk/browser";

export const VERSION = "0.1.0";
