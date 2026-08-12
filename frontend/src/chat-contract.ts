import type {
  ChatAttachment,
  ChatTurnRequest,
  InspectorEvent,
  Message,
  ModelDiagnostics,
  ProviderHttpAttempt,
  ToolExecution,
} from "./types";

export const ACTIVE_RUN_STORAGE_KEY = "mindspace.active_run";
const TURN_REQUEST_STORAGE_PREFIX = "mindspace.turn_requests.";
const MAX_ATTACHMENT_BYTES = 1_048_576;
const MAX_ATTACHMENT_CHARS = 20_000;
const MAX_ATTACHMENTS = 5;

export interface ActiveRunRecord {
  schema_version: 1;
  run_id: string;
  session_id: string;
  round: number;
  request: ChatTurnRequest;
  started_at: string;
}

export interface AttachmentMergeResult {
  attachments: ChatAttachment[];
  feedback: string[];
}

export interface RegenerationPreparation {
  request: ChatTurnRequest | null;
  stagedAttachments: ChatAttachment[];
  missingAttachments: ChatAttachment[];
}

const mediaTypeFor = (file: File) => {
  if (file.type) return file.type;
  if (/\.json$/i.test(file.name)) return "application/json";
  if (/\.csv$/i.test(file.name)) return "text/csv";
  if (/\.md$/i.test(file.name)) return "text/markdown";
  return "text/plain";
};

const attachmentKey = (value: Pick<ChatAttachment, "name" | "size" | "media_type">) =>
  `${value.name.toLocaleLowerCase()}\u0000${value.size}\u0000${value.media_type.toLocaleLowerCase()}`;

const fileKey = (file: File) => attachmentKey({
  name: file.name,
  size: file.size,
  media_type: mediaTypeFor(file),
});

export const requestAttachments = (attachments: ChatAttachment[]): ChatAttachment[] =>
  attachments.map(({ attachment_id, name, media_type, size, content }) => ({
    attachment_id,
    name,
    media_type,
    size,
    ...(content == null ? {} : { content }),
  }));

export const hasMissingAttachmentContent = (attachments: ChatAttachment[]) =>
  attachments.some((item) => item.content_missing || item.content == null);

export async function mergeAttachmentFiles(
  existing: ChatAttachment[],
  files: Iterable<File>,
  reattachOnly = false,
): Promise<AttachmentMergeResult> {
  const next = [...existing];
  const feedback: string[] = [];
  for (const file of Array.from(files).slice(0, MAX_ATTACHMENTS)) {
    const mediaType = mediaTypeFor(file);
    if (file.size <= 0) {
      feedback.push(`${file.name} 是空文件，未添加`);
      continue;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      feedback.push(`${file.name} 超过 1 MiB，未添加`);
      continue;
    }
    if (!(mediaType.startsWith("text/") || mediaType === "application/json" || /\.(txt|md|json|csv)$/i.test(file.name))) {
      feedback.push(`${file.name} 格式不支持，当前仅支持文本、Markdown、JSON 或 CSV`);
      continue;
    }
    const matchingMissing = next.findIndex((item) => item.content_missing && attachmentKey(item) === fileKey(file));
    if (matchingMissing < 0 && next.some((item) => !item.content_missing && attachmentKey(item) === fileKey(file))) {
      feedback.push(`${file.name} 已添加，已跳过重复文件`);
      continue;
    }
    if (reattachOnly && matchingMissing < 0) {
      feedback.push(`${file.name} 与待重附附件的名称、大小或格式不一致`);
      continue;
    }
    if (matchingMissing < 0 && next.length >= MAX_ATTACHMENTS) {
      feedback.push(`最多添加 ${MAX_ATTACHMENTS} 个附件，${file.name} 未添加`);
      continue;
    }
    let content: string;
    try {
      content = (await file.text()).slice(0, MAX_ATTACHMENT_CHARS);
    } catch {
      feedback.push(`${file.name} 读取失败，请重新选择文件`);
      continue;
    }
    if (matchingMissing >= 0) {
      const original = next[matchingMissing];
      next[matchingMissing] = { ...original, content, content_missing: false };
      feedback.push(`${file.name} 已重新附加`);
    } else {
      next.push({
        attachment_id: crypto.randomUUID(),
        name: file.name,
        media_type: mediaType,
        size: file.size,
        content,
        content_missing: false,
      });
      feedback.push(`${file.name} 已添加`);
    }
  }
  return { attachments: next, feedback };
}

export function sanitizeTurnRequest(request: ChatTurnRequest): ChatTurnRequest {
  return {
    ...structuredClone(request),
    interactions: request.interactions.map((item) => ({ ...item })),
    attachments: request.attachments.map(({ attachment_id, name, media_type, size }) => ({
      attachment_id,
      name,
      media_type,
      size,
      content_missing: true,
    })),
  };
}

export function createActiveRunRecord(runId: string, request: ChatTurnRequest): ActiveRunRecord {
  return {
    schema_version: 1,
    run_id: runId,
    session_id: request.session_id,
    round: request.round,
    request: sanitizeTurnRequest(request),
    started_at: request.client_sent_at,
  };
}

export function writeActiveRun(record: ActiveRunRecord) {
  localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify(record));
}

export function readActiveRun(): ActiveRunRecord | null {
  try {
    const value = JSON.parse(localStorage.getItem(ACTIVE_RUN_STORAGE_KEY) || "null") as ActiveRunRecord | null;
    if (value?.schema_version !== 1 || !value.run_id || !value.session_id || !value.request) return null;
    return value;
  } catch {
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    return null;
  }
}

export function clearActiveRun(runId = "") {
  const active = readActiveRun();
  if (!runId || active?.run_id === runId) localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
}

const turnStorageKey = (sessionId: string) => `${TURN_REQUEST_STORAGE_PREFIX}${sessionId}`;

export function saveTurnRequestSnapshot(request: ChatTurnRequest) {
  const key = turnStorageKey(request.session_id);
  let rows: Record<string, ChatTurnRequest> = {};
  try {
    rows = JSON.parse(localStorage.getItem(key) || "{}") as Record<string, ChatTurnRequest>;
  } catch {
    rows = {};
  }
  rows[String(request.round)] = sanitizeTurnRequest(request);
  const ordered = Object.entries(rows).sort((left, right) => Number(left[0]) - Number(right[0])).slice(-200);
  localStorage.setItem(key, JSON.stringify(Object.fromEntries(ordered)));
}

export function readTurnRequestSnapshot(sessionId: string, round: number): ChatTurnRequest | null {
  try {
    const rows = JSON.parse(localStorage.getItem(turnStorageKey(sessionId)) || "{}") as Record<string, ChatTurnRequest>;
    return rows[String(round)] || null;
  } catch {
    return null;
  }
}

export function clearTurnRequestSnapshots(sessionId: string) {
  localStorage.removeItem(turnStorageKey(sessionId));
}

export function hydrateTurnRequestSnapshots(sessionId: string, messages: Message[]): Message[] {
  return messages.map((message) => message.role === "user"
    ? { ...message, request_snapshot: readTurnRequestSnapshot(sessionId, message.round) || undefined }
    : message);
}

export function recoveredUserMessage(active: ActiveRunRecord): Message {
  return {
    role: "user",
    content: active.request.message,
    round: active.round,
    status: "complete",
    timestamp: active.started_at,
    reply_to_message_id: active.request.reply_to_message_id || undefined,
    interactions: active.request.interactions,
    attachments: active.request.attachments,
    request_snapshot: active.request,
  };
}

export function prepareRegeneration(request: ChatTurnRequest): RegenerationPreparation {
  const stagedAttachments = request.attachments.map((item) => ({ ...item }));
  const missingAttachments = stagedAttachments.filter((item) => item.content_missing || item.content == null);
  if (missingAttachments.length) return { request: null, stagedAttachments, missingAttachments };
  return {
    request: { ...structuredClone(request), mode: "regenerate", attachments: requestAttachments(stagedAttachments) },
    stagedAttachments,
    missingAttachments: [],
  };
}

export const shouldRenderToolExecution = (tool: ToolExecution | null | undefined): tool is ToolExecution =>
  Boolean(tool?.tool && tool.call_id);

export const composerAction = (generating: boolean, hasPayload: boolean, asrReady: boolean) =>
  generating ? "cancel" : hasPayload ? "send" : asrReady ? "voice" : "voice-disabled";

const PUBLIC_RUN_ERRORS: Record<string, string> = {
  model_timeout: "模型服务响应超时，请重试。",
  model_connection_failed: "无法连接模型服务，请检查网络和 API 配置后重试。",
  model_upstream_error: "模型服务暂时无法完成请求，请重试；若持续失败，请检查当前模型是否支持工具调用。",
  generation_failed: "生成失败，请重试；详细原因已记录在运行日志。",
};

export function publicRunError(_internalError: unknown, errorCode: unknown) {
  const code = typeof errorCode === "string" ? errorCode.trim() : "";
  return PUBLIC_RUN_ERRORS[code] || PUBLIC_RUN_ERRORS.generation_failed;
}

export function closeOpenMenusOutside(target: Element, root: ParentNode = document) {
  let closed = 0;
  root.querySelectorAll<HTMLDetailsElement>(
    "details.message-more[open], details.composer-add-menu[open], details.model-quick-menu[open]",
  ).forEach((details) => {
    if (!details.contains(target)) {
      details.removeAttribute("open");
      closed += 1;
    }
  });
  return closed;
}

export type ProviderToolCapabilityState =
  | "unknown"
  | "probing"
  | "supported"
  | "unsupported"
  | "transient_failure";

export function providerToolCapability(state: ProviderToolCapabilityState | string | null | undefined) {
  switch (state) {
    case "supported":
      return { native: true, state, label: "工具能力：可用" };
    case "unsupported":
      return { native: false, state, label: "工具能力：不可用" };
    case "probing":
      return { native: null, state, label: "工具能力：探测中" };
    case "transient_failure":
      return { native: null, state: "unknown" as const, label: "工具能力：未知（探测暂时失败）" };
    default:
      return { native: null, state: "unknown" as const, label: "工具能力：未知" };
  }
}

export function modelAttemptInspectorEvent(attempt: ProviderHttpAttempt, timestamp: string, sequence: number): InspectorEvent {
  return {
    event: `model.attempt:${sequence}`,
    label: `模型 HTTP 尝试 ${attempt.attempt} · ${attempt.status}`,
    timestamp,
    data: attempt,
    state: attempt.status === "success" ? "done" : "error",
  };
}

export function modelSummaryInspectorEvent(model: ModelDiagnostics, timestamp: string, failed = false): InspectorEvent {
  return {
    event: "model.summary",
    label: `模型调用：逻辑 ${model.total_calls} · HTTP ${model.total_http_attempts}`,
    timestamp,
    data: model,
    state: failed ? "error" : "done",
  };
}

export function parseModelDiagnostics(value: unknown, fallbackLogicalCalls = 0): ModelDiagnostics {
  const row = value && typeof value === "object" ? value as Partial<ModelDiagnostics> : {};
  const attempts = Array.isArray(row.provider_attempts) ? row.provider_attempts : [];
  return {
    call_summary: Array.isArray(row.call_summary) ? row.call_summary : [],
    total_calls: Number.isFinite(Number(row.total_calls)) ? Number(row.total_calls) : fallbackLogicalCalls,
    provider_attempts: attempts,
    total_http_attempts: Number.isFinite(Number(row.total_http_attempts)) ? Number(row.total_http_attempts) : attempts.length,
  };
}
