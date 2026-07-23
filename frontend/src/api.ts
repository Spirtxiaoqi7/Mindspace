import type { StreamEnvelope } from "./types";

export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers:
      init.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `请求失败 ${response.status}`);
  }
  return payload as T;
}

export async function consumeEventStream(
  response: Response,
  onEvent: (event: StreamEnvelope) => void,
  afterSequence = 0,
): Promise<{ lastSequence: number; terminal: boolean }> {
  // TCP 分块不等于 SSE 事件边界：先累计到空行，再解析完整 data 块。
  // seq 是幂等边界，重连重放时必须丢弃已经消费过的事件。
  if (!response.ok || !response.body) throw new Error(`流连接失败 ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastSequence = afterSequence;
  let terminal = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data) continue;
      let event: StreamEnvelope;
      try {
        event = JSON.parse(data) as StreamEnvelope;
      } catch {
        continue;
      }
      if (event.seq <= lastSequence) continue;
      lastSequence = event.seq;
      terminal = ["run.completed", "run.cancelled", "run.interrupted", "run.error"].includes(event.event);
      onEvent(event);
    }
    if (done) break;
  }
  return { lastSequence, terminal };
}

const wait = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal.aborted) {
    reject(new DOMException("Aborted", "AbortError"));
    return;
  }
  const onAbort = () => {
    window.clearTimeout(timeout);
    reject(new DOMException("Aborted", "AbortError"));
  };
  const timeout = window.setTimeout(() => {
    signal.removeEventListener("abort", onAbort);
    resolve();
  }, milliseconds);
  signal.addEventListener("abort", onAbort, { once: true });
});

export async function consumeResumableEventStream(
  initialResponse: Response,
  runId: string,
  onEvent: (event: StreamEnvelope) => void,
  signal: AbortSignal,
): Promise<void> {
  // 断线只重建“订阅”，不会重新 POST 对话。服务端继续运行原 request_id，
  // 客户端从 lastSequence 之后补收，避免重复模型调用和重复渲染。
  let response = initialResponse;
  let lastSequence = 0;
  let lastError: Error | null = null;
  for (let attempt = 0; attempt <= 6; attempt += 1) {
    try {
      const result = await consumeEventStream(response, onEvent, lastSequence);
      lastSequence = result.lastSequence;
      if (result.terminal) return;
      lastError = new Error("流连接提前结束");
    } catch (error) {
      if ((error as Error).name === "AbortError") throw error;
      lastError = error as Error;
    }
    if (attempt === 6) break;
    await wait(Math.min(4000, 250 * 2 ** attempt), signal);
    response = await fetch(
      `/api/v1/runs/${encodeURIComponent(runId)}/stream?after=${lastSequence}`,
      { headers: { "Last-Event-ID": String(lastSequence) }, signal },
    );
  }
  throw lastError || new Error("流式回复恢复失败");
}
