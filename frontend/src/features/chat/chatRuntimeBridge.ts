import {
  clearActiveRun,
  hydrateTurnRequestSnapshots,
  modelAttemptInspectorEvent,
  modelSummaryInspectorEvent,
  parseModelDiagnostics,
  providerToolCapability,
  publicRunError,
} from "../../chat-contract";
import type { ProviderToolCapabilityState } from "../../chat-contract";
import { asRecord, num, str } from "../../shared/formatters";
import type { InspectorEvent, Message, ProviderHttpAttempt } from "../../types";

export const clearPersistedChatRun = (runId: string) => clearActiveRun(runId);

export const restoreSessionMessages = (sessionId: string, messages: Message[]) =>
  hydrateTurnRequestSnapshots(sessionId, messages);

export const getProviderToolCapability = (state?: ProviderToolCapabilityState | null) => providerToolCapability(state);

export const getPublicRunError = (internalError: unknown, errorCode: unknown) =>
  publicRunError(internalError, errorCode);

export function createModelAttemptInspectorEvent(
  value: unknown,
  timestamp: string,
  sequence: number,
): InspectorEvent {
  const data = asRecord(value);
  const rawStatus = str(data.status);
  const status: ProviderHttpAttempt["status"] = [
    "success",
    "http_error",
    "transport_error",
    "empty",
    "error",
  ].includes(rawStatus) ? rawStatus as ProviderHttpAttempt["status"] : "error";
  return modelAttemptInspectorEvent({
    attempt: Math.max(1, num(data.attempt, 1)),
    request_kind: str(data.request_kind),
    status,
    elapsed_ms: Math.max(0, num(data.elapsed_ms)),
    http_status: data.http_status == null ? null : num(data.http_status),
    compatibility_variant: str(data.compatibility_variant),
    retry_reason: str(data.retry_reason),
    error: str(data.error),
  }, timestamp, sequence);
}

export function createModelSummaryInspectorEvent(
  value: unknown,
  fallbackLogicalCalls: number,
  timestamp: string,
  failed = false,
): InspectorEvent {
  return modelSummaryInspectorEvent(
    parseModelDiagnostics(value, fallbackLogicalCalls),
    timestamp,
    failed,
  );
}
