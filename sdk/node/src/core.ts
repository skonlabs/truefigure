/**
 * TrueFigureClient — thin, isomorphic transport client (Node 18+ and browsers).
 *
 * Transport only: typed 1:1 endpoint bindings + batching + contract-driven
 * retry/backoff + JSON envelope parsing + typed error mapping. It computes NO
 * event_key, canonicalizes NO timestamps, validates NO enums/business rules, and
 * performs NO grade/margin/figure math. The server owns all of that; the client
 * sends raw envelopes and receives server-assigned event_keys back.
 */
import { RateLimited, TrueFigureError, toErrorObject, type ErrorObject } from "./errors.js";
import type { ClientOptions, Envelope, EventEnvelope, EventResult, Transport } from "./types.js";

/** Optional durable spool so events survive outages; replay is safe (server idempotency). */
export interface OfflineBuffer {
  append(envelopes: EventEnvelope[]): void | Promise<void>;
  drain(): EventEnvelope[] | Promise<EventEnvelope[]>;
}

export class BatchResult {
  readonly results: EventResult[];
  readonly requestId: string;
  readonly accepted: EventResult[];
  readonly duplicates: EventResult[];
  readonly rejected: EventResult[];
  constructor(results: EventResult[], requestId: string) {
    this.results = results;
    this.requestId = requestId;
    this.accepted = results.filter((r) => r.status === "accepted");
    this.duplicates = results.filter((r) => r.status === "duplicate");
    this.rejected = results.filter((r) => r.status === "rejected");
  }
}

const defaultSleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export class TrueFigureClient {
  static readonly MAX_BATCH = 500;

  readonly baseUrl: string;
  readonly deploymentId?: string;
  private readonly apiKey: string;
  private readonly maxRetries: number;
  private readonly timeoutMs: number;
  private readonly sourceRef?: string;
  private readonly transport: Transport;
  private readonly sleep: (ms: number) => Promise<void>;
  private pending: EventEnvelope[] = [];
  buffer?: OfflineBuffer;

  constructor(apiKey: string, opts: ClientOptions = {}) {
    this.apiKey = apiKey;
    this.baseUrl = (opts.baseUrl ?? "https://api.truefigure.io").replace(/\/+$/, "");
    this.deploymentId = opts.deploymentId;
    this.maxRetries = opts.maxRetries ?? 5;
    this.timeoutMs = opts.timeoutMs ?? 10000;
    this.sourceRef = opts.sourceRef;
    this.sleep = opts.sleep ?? defaultSleep;
    this.transport = opts.transport ?? this.fetchTransport(opts.fetchImpl);
  }

  // -------- event enqueue (shape + queue; server assigns keys) --------
  private dep(override?: string): string {
    const d = override ?? this.deploymentId;
    if (!d) throw new Error("deploymentId required (constructor default or per-call)");
    return d;
  }

  /** Enqueue a pre-shaped raw envelope. Returns the queue position. */
  track(envelope: EventEnvelope): number {
    this.pending.push(envelope);
    return this.pending.length;
  }

  /** POST a list of raw envelopes directly (server assigns keys). */
  async ingest(envelopes: EventEnvelope[], mode = "production"): Promise<BatchResult> {
    const env = await this.request("POST", "/v1/events:batch", { events: envelopes },
      { "X-TrueFigure-Mode": mode });
    const data = env.data as { results: EventResult[] };
    return new BatchResult(data.results, env.meta.request_id ?? "");
  }

  /** Parse-echo test mode: same endpoint, header-switched; stores nothing server-side. */
  async echo(envelopes?: EventEnvelope[]): Promise<BatchResult> {
    const batch = envelopes ?? this.pending.splice(0);
    return this.ingest(batch, "test");
  }

  /** Flush the pending queue in chunks, spooling to the offline buffer on failure. */
  async flush(mode = "production"): Promise<BatchResult> {
    let batch = this.pending.splice(0);
    if (this.buffer) batch = [...(await this.buffer.drain()), ...batch];
    if (batch.length === 0) return new BatchResult([], "");
    const results: EventResult[] = [];
    let requestId = "";
    for (let i = 0; i < batch.length; i += TrueFigureClient.MAX_BATCH) {
      const chunk = batch.slice(i, i + TrueFigureClient.MAX_BATCH);
      let env: Envelope;
      try {
        env = await this.request("POST", "/v1/events:batch", { events: chunk },
          { "X-TrueFigure-Mode": mode });
      } catch (err) {
        if (this.buffer) {
          await this.buffer.append(chunk);
          continue;
        }
        this.pending = [...chunk, ...this.pending];
        throw err;
      }
      const data = env.data as { results: EventResult[] };
      requestId = env.meta.request_id ?? "";
      results.push(...data.results);
    }
    return new BatchResult(results, requestId);
  }

  // -------- write plane --------
  ingestBatch(envelopes: EventEnvelope[], mode = "production"): Promise<BatchResult> {
    return this.ingest(envelopes, mode);
  }
  eventStatus(eventKey: string): Promise<unknown> {
    return this.data("GET", `/v1/events/${enc(eventKey)}/status`);
  }

  // -------- read plane --------
  liveUsage(deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/live/usage/${enc(this.dep(deploymentId))}`);
  }
  liveCost(deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/live/cost/${enc(this.dep(deploymentId))}`);
  }
  integrationHealth(deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/live/health/${enc(this.dep(deploymentId))}`);
  }
  figures(deploymentId?: string, period?: string): Promise<unknown> {
    return this.data("GET", `/v1/figures/${enc(this.dep(deploymentId))}${query({ period })}`);
  }
  getFigure(figureId: string, deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/figures/${enc(this.dep(deploymentId))}/${enc(figureId)}`);
  }
  figureLineage(figureId: string, deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/figures/${enc(this.dep(deploymentId))}/${enc(figureId)}/lineage`);
  }
  reports(deploymentId?: string, period?: string): Promise<unknown> {
    return this.data("GET", `/v1/reports/${enc(this.dep(deploymentId))}${query({ period })}`);
  }
  getReport(reportId: string, deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/reports/${enc(this.dep(deploymentId))}/${enc(reportId)}`);
  }
  alerts(opts: { status?: string; deploymentId?: string } = {}): Promise<unknown> {
    return this.data("GET", `/v1/alerts${query({ status: opts.status ?? "open", deployment_id: opts.deploymentId })}`);
  }
  ackAlert(alertId: string): Promise<unknown> {
    return this.data("POST", `/v1/alerts/${enc(alertId)}:ack`);
  }

  // -------- config plane --------
  whoami(): Promise<unknown> {
    return this.data("GET", "/v1/whoami");
  }
  registerDeployment(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/deployments", body);
  }
  listDeployments(): Promise<unknown> {
    return this.data("GET", "/v1/deployments");
  }
  getDeployment(deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/deployments/${enc(this.dep(deploymentId))}`);
  }
  updateDeployment(body: Record<string, unknown>, deploymentId?: string): Promise<unknown> {
    return this.data("PATCH", `/v1/deployments/${enc(this.dep(deploymentId))}`, body);
  }
  declareLicense(body: Record<string, unknown>, deploymentId?: string): Promise<unknown> {
    return this.data("PUT", `/v1/deployments/${enc(this.dep(deploymentId))}/license`, body);
  }
  getLicense(deploymentId?: string): Promise<unknown> {
    return this.data("GET", `/v1/deployments/${enc(this.dep(deploymentId))}/license`);
  }
  upsertRoster(users: Array<Record<string, unknown>>): Promise<unknown> {
    return this.data("POST", "/v1/roster:batch", { users });
  }
  listRoster(opts: { status?: string; kind?: string } = {}): Promise<unknown> {
    return this.data("GET", `/v1/roster${query({ status: opts.status, kind: opts.kind })}`);
  }
  registerIdNamespace(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/id-namespaces", body);
  }
  listIdNamespaces(): Promise<unknown> {
    return this.data("GET", "/v1/id-namespaces");
  }
  createParameterVersion(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/parameters", body);
  }
  listParameters(): Promise<unknown> {
    return this.data("GET", "/v1/parameters");
  }
  getParameterVersion(version: number): Promise<unknown> {
    return this.data("GET", `/v1/parameters/${version}`);
  }
  registerLabelSchema(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/qa-labels/schemas", body);
  }
  listLabelSchemas(): Promise<unknown> {
    return this.data("GET", "/v1/qa-labels/schemas");
  }
  createWebhook(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/webhooks", body);
  }
  listWebhooks(): Promise<unknown> {
    return this.data("GET", "/v1/webhooks");
  }
  deleteWebhook(webhookId: string): Promise<unknown> {
    return this.data("DELETE", `/v1/webhooks/${enc(webhookId)}`);
  }
  webhookDeliveries(webhookId: string): Promise<unknown> {
    return this.data("GET", `/v1/webhooks/${enc(webhookId)}/deliveries`);
  }
  createMappingContract(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/mapping-contracts", body);
  }
  listMappingContracts(): Promise<unknown> {
    return this.data("GET", "/v1/mapping-contracts");
  }
  declareChangeEvent(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/change-events", body);
  }
  listChangeEvents(): Promise<unknown> {
    return this.data("GET", "/v1/change-events");
  }
  createImport(body: Record<string, unknown>): Promise<unknown> {
    return this.data("POST", "/v1/imports", body);
  }
  importStatus(importId: string): Promise<unknown> {
    return this.data("GET", `/v1/imports/${enc(importId)}`);
  }

  // -------- transport with contract-driven retry --------
  private async data(method: string, path: string, body?: Record<string, unknown>): Promise<unknown> {
    return (await this.request(method, path, body)).data;
  }

  async request(
    method: string,
    path: string,
    body?: Record<string, unknown> | null,
    extraHeaders?: Record<string, string>,
  ): Promise<Envelope> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
    };
    if (this.sourceRef) headers["X-TrueFigure-Source"] = this.sourceRef;
    Object.assign(headers, extraHeaders ?? {});
    const url = this.baseUrl + path;
    let attempt = 0;
    let delay = 500;
    for (;;) {
      attempt += 1;
      const { status, headers: rh, body: env } = await this.transport(method, url, headers, body ?? null);
      const errors: ErrorObject[] = (env.errors ?? []).map(toErrorObject);
      const requestId = env.meta?.request_id ?? rh["x-request-id"] ?? "";
      if (status < 400 && errors.length === 0) return env;
      const retryable = status === 429 || status >= 500 || errors.some((e) => e.retryable);
      if (retryable && attempt <= this.maxRetries) {
        const fromErr = errors.find((e) => e.retryAfter != null)?.retryAfter;
        const waitS = fromErr ?? Number(rh["retry-after"] ?? 0) ?? 0;
        await this.sleep((waitS > 0 ? waitS * 1000 : delay));
        delay = Math.min(delay * 2, 30000);
        continue;
      }
      const objs = errors.length ? errors
        : [toErrorObject({ code: "TF-SRV-001", message: `HTTP ${status}`, retryable: status >= 500, request_id: requestId })];
      if (status === 429) throw new RateLimited(objs, requestId);
      throw new TrueFigureError(objs, requestId);
    }
  }

  private fetchTransport(fetchImpl?: typeof fetch): Transport {
    const doFetch = fetchImpl ?? (globalThis.fetch as typeof fetch | undefined);
    if (!doFetch) throw new Error("no fetch available; pass opts.fetchImpl or opts.transport");
    const timeoutMs = this.timeoutMs;
    return async (method, url, headers, body) => {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), timeoutMs);
      try {
        const resp = await doFetch(url, {
          method,
          headers,
          body: method === "GET" || body == null ? undefined : JSON.stringify(body),
          signal: ctl.signal,
        });
        const rh: Record<string, string> = {};
        resp.headers.forEach((v, k) => { rh[k] = v; });
        let parsed: Envelope;
        const text = await resp.text();
        try {
          parsed = text ? (JSON.parse(text) as Envelope) : { data: null, meta: {}, errors: [] };
        } catch {
          parsed = { data: null, meta: {}, errors: [{ code: "TF-SRV-001", message: "non-JSON response", retryable: resp.status >= 500 }] };
        }
        return { status: resp.status, headers: rh, body: parsed };
      } finally {
        clearTimeout(timer);
      }
    };
  }
}

function enc(s: string): string {
  return encodeURIComponent(s);
}

function query(params: Record<string, string | undefined>): string {
  const parts = Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}
