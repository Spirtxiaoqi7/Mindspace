import type { AudioStatus, ChatTurnRequest, StreamEnvelope } from "./types";

type DesktopSettingsSaveResult = {
  ok: boolean;
  status?: number;
  payload?: unknown;
  error?: string;
  phase?: string;
  core_applied?: boolean;
  secret_persisted?: boolean | null;
  retryable?: boolean;
};

type DesktopLauncherBridge = {
  saveSettings?: (payload: Record<string, unknown>) => Promise<DesktopSettingsSaveResult>;
  getSettings?: () => Promise<DesktopSettingsSaveResult>;
};

const desktopLauncher = (): DesktopLauncherBridge | undefined => {
  if (typeof window === "undefined") return undefined;
  return (window as Window & { launcher?: DesktopLauncherBridge }).launcher;
};

const isSettingsMutation = (url: string, init: RequestInit) => {
  const method = String(init.method || "GET").toUpperCase();
  if (!['PUT', 'PATCH'].includes(method)) return false;
  try {
    return new URL(url, typeof location === "undefined" ? "http://localhost" : location.origin).pathname === "/api/v1/settings";
  } catch {
    return false;
  }
};

const isSettingsRead = (url: string, init: RequestInit) => {
  if (String(init.method || "GET").toUpperCase() !== "GET") return false;
  try {
    return new URL(url, typeof location === "undefined" ? "http://localhost" : location.origin).pathname === "/api/v1/settings";
  } catch {
    return false;
  }
};

async function desktopSettingsRequest<T>(url: string, init: RequestInit): Promise<T | undefined> {
  const launcher = desktopLauncher();
  if (launcher?.getSettings && isSettingsRead(url, init)) {
    const result = await launcher.getSettings();
    if (!result.ok) throw new HttpError(result.status || 500, { detail: result.error || "桌面设置读取失败" });
    return result.payload as T;
  }
  const bridge = launcher?.saveSettings;
  if (!bridge || !isSettingsMutation(url, init)) return undefined;
  if (typeof init.body !== "string") throw new HttpError(400, { detail: "桌面设置保存只接受 JSON" });
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(init.body) as Record<string, unknown>;
  } catch {
    throw new HttpError(400, { detail: "桌面设置 JSON 无效" });
  }
  const result = await bridge(payload);
  if (!result.ok) {
    throw new HttpError(result.status || 500, {
      detail: result.error || "桌面设置保存失败",
      phase: result.phase,
      core_applied: result.core_applied,
      secret_persisted: result.secret_persisted,
      retryable: result.retryable,
    });
  }
  return result.payload as T;
}

export class HttpError extends Error {
  readonly status: number;
  readonly payload: Record<string, unknown>;

  constructor(status: number, payload: Record<string, unknown>) {
    super(String(payload.detail || payload.error || `请求失败 ${status}`));
    this.name = "HttpError";
    this.status = status;
    this.payload = payload;
  }
}

export async function rawRequest(url: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, {
    ...init,
    headers:
      init.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const payload = await response.clone().json().catch(() => ({}));
    throw new HttpError(response.status, payload);
  }
  return response;
}

export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const desktopResult = await desktopSettingsRequest<T>(url, init);
  if (desktopResult !== undefined) return desktopResult;
  const response = await rawRequest(url, init);
  const payload = await response.json().catch(() => ({}));
  return payload as T;
}

export const apiV1Request = <T>(path: string, init: RequestInit = {}) =>
  request<T>(`/api/v1${path.startsWith("/") ? path : `/${path}`}`, init);

export const openChatStream = (payload: ChatTurnRequest, requestId: string, signal: AbortSignal) =>
  rawRequest("/api/v1/chat/stream", {
    method: "POST",
    headers: { "X-Request-ID": requestId },
    body: JSON.stringify(payload),
    signal,
  });

export const openRunEventStream = (runId: string, after: number, signal: AbortSignal) =>
  rawRequest(`/api/v1/runs/${encodeURIComponent(runId)}/stream?after=${after}`, {
    headers: { "Last-Event-ID": String(after) },
    signal,
  });

export const cancelRunRequest = (runId: string) =>
  rawRequest(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });

export const getAudioStatus = (signal?: AbortSignal) =>
  request<AudioStatus>("/api/v1/audio/status", { signal });

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
    response = await openRunEventStream(runId, lastSequence, signal);
  }
  throw lastError || new Error("流式回复恢复失败");
}
