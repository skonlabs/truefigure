/**
 * FileOfflineBuffer — a JSONL spool so events survive process crashes and
 * network outages. Replay is idempotent-safe because identity is server-side.
 * Node-only (uses fs); browsers should omit the buffer or supply their own.
 */
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import type { OfflineBuffer } from "./core.js";
import type { EventEnvelope } from "./types.js";

export class FileOfflineBuffer implements OfflineBuffer {
  constructor(private readonly path: string) {}

  append(envelopes: EventEnvelope[]): void {
    if (envelopes.length === 0) return;
    appendFileSync(this.path, envelopes.map((e) => JSON.stringify(e)).join("\n") + "\n", "utf-8");
  }

  drain(): EventEnvelope[] {
    let text: string;
    try {
      text = readFileSync(this.path, "utf-8");
    } catch {
      return [];
    }
    const items = text.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l) as EventEnvelope);
    writeFileSync(this.path, "", "utf-8");
    return items;
  }
}
