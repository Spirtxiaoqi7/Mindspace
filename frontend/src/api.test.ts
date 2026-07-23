import { describe, expect, it } from "vitest";

import { consumeEventStream } from "./api";
import type { StreamEnvelope } from "./types";

const envelope = (event: string, seq: number): StreamEnvelope => ({
  version: "1.0",
  event,
  seq,
  run_id: "run-1",
  session_id: "session-1",
  round: 1,
  timestamp: new Date(0).toISOString(),
  data: {},
});

describe("consumeEventStream", () => {
  it("deduplicates replayed sequences and recognizes terminal events", async () => {
    const payloads = [
      `id: 1\nevent: response.delta\ndata: ${JSON.stringify(envelope("response.delta", 1))}`,
      `id: 1\nevent: response.delta\ndata: ${JSON.stringify(envelope("response.delta", 1))}`,
      "event: broken\ndata: {not-json}",
      `id: 2\nevent: run.completed\ndata: ${JSON.stringify(envelope("run.completed", 2))}`,
    ].join("\n\n") + "\n\n";
    const events: StreamEnvelope[] = [];

    const result = await consumeEventStream(
      new Response(payloads, { status: 200 }),
      (event) => events.push(event),
    );

    expect(events.map((event) => event.seq)).toEqual([1, 2]);
    expect(result).toEqual({ lastSequence: 2, terminal: true });
  });

  it("treats a recovered Core interruption as terminal without retrying", async () => {
    const payload =
      `id: 1000043\nevent: run.interrupted\ndata: ${
        JSON.stringify(envelope("run.interrupted", 1000043))
      }\n\n`;

    const result = await consumeEventStream(
      new Response(payload, { status: 200 }),
      () => undefined,
      42,
    );

    expect(result).toEqual({ lastSequence: 1000043, terminal: true });
  });
});
