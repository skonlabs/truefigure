/**
 * TrueFigure SDK error model (thin).
 *
 * The error-code registry is a closed, versioned enum shipped as a public
 * catalog (error-codes.json). Codes are never reused, so client switch
 * statements are safe for years. Refusals are NOT errors: a REFUSED figure is a
 * successful response about evidence.
 */
import registryFile from "./error-codes.json" with { type: "json" };

type CodeDef = { fix_owner?: string; retryable?: boolean; message?: string; http?: number };
const REGISTRY: Record<string, CodeDef> = (registryFile as { codes: Record<string, CodeDef> }).codes;

/** Whose action fixes a code: integrator | config_owner | platform | none | unknown. */
export function fixOwner(code: string): string {
  return REGISTRY[code]?.fix_owner ?? "unknown";
}

/** Structured error object, mirroring the API's ErrorObject schema. */
export interface ErrorObject {
  code: string;
  message: string;
  retryable: boolean;
  requestId: string;
  fieldPath?: string;
  expected?: string;
  received?: string;
  retryAfter?: number;
  docUrl?: string;
  /** Derived from the public catalog; not sent on the wire. */
  fixOwner: string;
}

export function toErrorObject(d: Record<string, unknown>): ErrorObject {
  const code = (d.code as string) ?? "TF-SRV-001";
  return {
    code,
    message: (d.message as string) ?? "",
    retryable: Boolean(d.retryable ?? false),
    requestId: (d.request_id as string) ?? "",
    fieldPath: d.field_path as string | undefined,
    expected: d.expected as string | undefined,
    received: d.received as string | undefined,
    retryAfter: d.retry_after as number | undefined,
    docUrl: d.doc_url as string | undefined,
    fixOwner: fixOwner(code),
  };
}

/** Raised for non-retryable failures after retries are exhausted or inapplicable. */
export class TrueFigureError extends Error {
  readonly errors: ErrorObject[];
  readonly requestId: string;
  constructor(errors: ErrorObject[], requestId = "") {
    const rid = requestId || errors[0]?.requestId || "";
    const summary = errors.map((e) => `${e.code}: ${e.message}`).join("; ") || "unknown error";
    super(`[request_id=${rid}] ${summary}`);
    this.name = "TrueFigureError";
    this.errors = errors;
    this.requestId = rid;
  }
}

/** TF-RATE-001. Backoff honors retryAfter; buffered replay is safe (server idempotency). */
export class RateLimited extends TrueFigureError {
  constructor(errors: ErrorObject[], requestId = "") {
    super(errors, requestId);
    this.name = "RateLimited";
  }
  get retryAfter(): number {
    for (const e of this.errors) if (e.retryAfter != null) return e.retryAfter;
    return 1;
  }
}
