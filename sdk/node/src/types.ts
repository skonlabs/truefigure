/**
 * Wire types for the thin client. These describe only the PUBLIC envelope shapes
 * from openapi.yaml / events.schema.json — no proprietary logic is encoded here.
 */

/** The response envelope every endpoint returns. */
export interface Envelope<T = unknown> {
  data: T;
  meta: { schema_version?: string; request_id?: string; as_of?: string; [k: string]: unknown };
  errors: Array<Record<string, unknown>>;
}

/** A raw ingestion envelope. The client shapes and sends this verbatim. */
export interface EventEnvelope {
  schema_version: string;
  deployment_id: string;
  event_type: string;
  origin: string;
  payload: Record<string, unknown>;
  idempotency_scope?: string;
}

/** One result row from POST /v1/events:batch. `event_key` is assigned by the SERVER. */
export interface EventResult {
  index: number;
  status: "accepted" | "duplicate" | "rejected";
  event_key?: string;
  code?: string;
  error?: Record<string, unknown>;
  echo?: Record<string, unknown>;
}

/** Low-level transport contract; injectable for tests. */
export type Transport = (
  method: string,
  url: string,
  headers: Record<string, string>,
  body: Record<string, unknown> | null,
) => Promise<{ status: number; headers: Record<string, string>; body: Envelope }>;

export interface ClientOptions {
  baseUrl?: string;
  deploymentId?: string;
  maxRetries?: number;
  timeoutMs?: number;
  sourceRef?: string;
  /** Injectable transport (tests / custom runtimes). Defaults to fetch. */
  transport?: Transport;
  /** fetch implementation override (e.g. a polyfill). Defaults to globalThis.fetch. */
  fetchImpl?: typeof fetch;
  /** async sleep override (tests). Defaults to setTimeout. */
  sleep?: (ms: number) => Promise<void>;
}
