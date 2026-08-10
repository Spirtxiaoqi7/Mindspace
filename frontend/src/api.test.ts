import { afterEach, describe, expect, it, vi } from "vitest";

import { consumeEventStream, request } from "./api";
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

  it("delivers durable model.attempt events without renaming diagnostic fields", async () => {
    const attempt = {
      attempt: 2,
      request_kind: "main_generation",
      status: "http_error",
      elapsed_ms: 420,
      http_status: 429,
      compatibility_variant: "responses",
      retry_reason: "rate_limit",
      error: "busy",
    };
    const payload = `event: model.attempt\ndata: ${JSON.stringify({ ...envelope("model.attempt", 8), data: attempt })}\n\n`;
    const events: StreamEnvelope[] = [];
    await consumeEventStream(new Response(payload, { status: 200 }), (event) => events.push(event));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("model.attempt");
    expect(events[0].data).toEqual(attempt);
  });
});

describe("desktop settings routing", () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  });

  it("routes desktop settings mutations through the Launcher bridge", async () => {
    const saveSettings = vi.fn(async (payload: Record<string, unknown>) => ({
      ok: true,
      payload: { success: true, settings: { llm: { credentials_persistence: "process_only" } } },
    }));
    Object.defineProperty(globalThis, "window", { configurable: true, value: { launcher: { saveSettings } } });
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const result = await request<{ success: boolean }>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({ llm: { api_key: "desktop-secret" } }),
    });

    expect(result.success).toBe(true);
    expect(saveSettings).toHaveBeenCalledWith({ llm: { api_key: "desktop-secret" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("routes desktop settings reads through the Launcher status bridge", async () => {
    const getSettings = vi.fn(async () => ({
      ok: true,
      payload: { llm: { credentials_persistence: "secure_storage", credentials_persisted: true } },
    }));
    Object.defineProperty(globalThis, "window", { configurable: true, value: { launcher: { getSettings } } });
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const result = await request<{ llm: { credentials_persistence: string } }>("/api/v1/settings");

    expect(result.llm.credentials_persistence).toBe("secure_storage");
    expect(getSettings).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps browser development on the direct Core route", async () => {
    Object.defineProperty(globalThis, "window", { configurable: true, value: {} });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ success: true, settings: { llm: { credentials_persistence: "process_only" } } }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));

    const result = await request<{ settings: { llm: { credentials_persistence: string } } }>("/api/v1/settings", {
      method: "PATCH",
      body: JSON.stringify({ llm: { api_key: "browser-process-only" } }),
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.settings.llm.credentials_persistence).toBe("process_only");
  });
});
