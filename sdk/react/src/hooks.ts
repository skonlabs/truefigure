/**
 * Read-plane + write-plane hooks over the thin @truefigure/sdk client.
 *
 * These are UI ergonomics only. Every hook delegates to a 1:1 client call; the
 * server owns all measurement, identity, and business rules. No event_key, no
 * canonicalization, no grade/figure math is present or reachable here.
 */
import { useCallback, useContext, useMemo } from "react";
import type { EventEnvelope, TrueFigureClient } from "@truefigure/sdk/browser";
import { TrueFigureContext } from "./context.js";
import { useAsyncResource, type AsyncState } from "./useAsync.js";

/** The client from the nearest <TrueFigureProvider>. Throws if missing. */
export function useTrueFigure(): TrueFigureClient {
  const client = useContext(TrueFigureContext);
  if (!client) throw new Error("useTrueFigure must be used within a <TrueFigureProvider>");
  return client;
}

export interface QueryOptions {
  /** When false, the hook does not fetch (e.g. waiting on a deploymentId). */
  enabled?: boolean;
}

export function useWhoami(opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.whoami(), [c], opts.enabled ?? true);
}

export function useFigures(deploymentId?: string, period?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.figures(deploymentId, period), [c, deploymentId, period], opts.enabled ?? true);
}

export function useFigure(figureId: string, deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.getFigure(figureId, deploymentId), [c, figureId, deploymentId], opts.enabled ?? true);
}

export function useFigureLineage(figureId: string, deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.figureLineage(figureId, deploymentId), [c, figureId, deploymentId], opts.enabled ?? true);
}

export function useLiveUsage(deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.liveUsage(deploymentId), [c, deploymentId], opts.enabled ?? true);
}

export function useLiveCost(deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.liveCost(deploymentId), [c, deploymentId], opts.enabled ?? true);
}

export function useIntegrationHealth(deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.integrationHealth(deploymentId), [c, deploymentId], opts.enabled ?? true);
}

export function useReports(deploymentId?: string, period?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.reports(deploymentId, period), [c, deploymentId, period], opts.enabled ?? true);
}

export function useReport(reportId: string, deploymentId?: string, opts: QueryOptions = {}): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.getReport(reportId, deploymentId), [c, reportId, deploymentId], opts.enabled ?? true);
}

export function useAlerts(
  args: { status?: string; deploymentId?: string } = {},
  opts: QueryOptions = {},
): AsyncState<unknown> {
  const c = useTrueFigure();
  return useAsyncResource(() => c.alerts(args), [c, args.status, args.deploymentId], opts.enabled ?? true);
}

export interface TrackApi {
  /** Enqueue a pre-shaped raw envelope; returns queue position. */
  track: (envelope: EventEnvelope) => number;
  /** POST a list of raw envelopes directly (server assigns keys). */
  ingest: TrueFigureClient["ingest"];
  /** Flush the pending queue. */
  flush: TrueFigureClient["flush"];
}

/**
 * Write helpers. Prefer a scoped key; most browser dashboards are read-only.
 */
export function useTrack(): TrackApi {
  const c = useTrueFigure();
  const track = useCallback((envelope: EventEnvelope) => c.track(envelope), [c]);
  const ingest = useCallback<TrueFigureClient["ingest"]>((envs, mode) => c.ingest(envs, mode), [c]);
  const flush = useCallback<TrueFigureClient["flush"]>((mode) => c.flush(mode), [c]);
  return useMemo(() => ({ track, ingest, flush }), [track, ingest, flush]);
}
