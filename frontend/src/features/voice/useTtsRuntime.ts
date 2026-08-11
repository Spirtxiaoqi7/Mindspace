import {
  useCallback,
  useRef,
  type MutableRefObject,
} from "react";

import { rawRequest, request } from "../../shared/api";
import { num, str } from "../../shared/formatters";
import {
  estimateDeliveredPrefix,
  hasSpeakableContent,
  SpeechSegmenter,
  stripLeadingTtsFiller,
} from "../../speech";
import type {
  ProductSettings,
  VoiceDeliveryState,
  VoiceSessionState,
} from "../../types";
import type { PCMStreamHandle, SpeechQueueItem } from "./types";

const TTS_RESPONSE_TIMEOUT_MS = 15_000;
const TTS_FIRST_PCM_TIMEOUT_MS = 8_000;
const TTS_PLAYBACK_START_TIMEOUT_MS = 1_500;
const TTS_STREAM_IDLE_TIMEOUT_MS = 15_000;
const TTS_PLAYBACK_END_GRACE_MS = 8_000;
const TTS_SAFE_OUTPUT_GAIN = 0.72;
const TTS_READY_WAIT_LIMIT_MS = 90_000;
const TTS_READY_POLL_MS = 2_000;

const VOICE_CUES = new Set([
  "neutral",
  "thoughtful",
  "warm",
  "firm",
  "playful",
  "intimate",
  "reflective",
  "tender",
  "teasing",
  "lively",
  "dramatic",
  "breathy",
  "laughing",
  "sighing",
  "seductive",
  "alluring",
  "moaning",
  "satisfied",
]);

export function alignPCM16Chunk(carry: Uint8Array, incoming: Uint8Array): {
  pcm: Uint8Array<ArrayBuffer>;
  remainder: Uint8Array<ArrayBuffer>;
} {
  const merged = new Uint8Array(carry.byteLength + incoming.byteLength);
  merged.set(carry);
  merged.set(incoming, carry.byteLength);
  const alignedLength = merged.byteLength - (merged.byteLength % 2);
  const pcm = new Uint8Array(alignedLength);
  const remainder = new Uint8Array(merged.byteLength - alignedLength);
  pcm.set(merged.subarray(0, alignedLength));
  remainder.set(merged.subarray(alignedLength));
  return { pcm, remainder };
}

export function shouldBufferQwenReplyForSinglePass(
  settings: ProductSettings | null | undefined,
  voiceOpen: boolean,
): boolean {
  return settings?.audio.tts_provider === "qwen3-vllm" && voiceOpen;
}

export function shouldAutomaticallyQueueSpeech(voiceOpen: boolean): boolean {
  return voiceOpen;
}

export function shouldSkipSpeechSegmentFailure(text: string, error: unknown): boolean {
  if (!hasSpeakableContent(text)) return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /没有可朗读的正文内容|请输入有效文本/.test(message);
}

async function requestWithTimeout<T>(
  url: string,
  init: RequestInit = {},
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const callerSignal = init.signal;
  const cancelFromCaller = () => controller.abort("cancelled");
  if (callerSignal?.aborted) cancelFromCaller();
  else callerSignal?.addEventListener("abort", cancelFromCaller, { once: true });
  const timeout = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    return await request<T>(url, { ...init, signal: controller.signal });
  } catch (error) {
    if ((error as Error).name === "AbortError" || controller.signal.aborted) {
      if (callerSignal?.aborted) throw new DOMException("Cancelled", "AbortError");
      throw new Error("语音服务响应超时，请重试或关闭语音入口");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", cancelFromCaller);
  }
}

function waitWithSignal(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export interface TtsRuntimeCallbacks {
  isVoiceOpen: () => boolean;
  isGenerating: () => boolean;
  getRunId: () => string;
  getVisibleReply: () => string;
  getLastVoiceRunId: () => string;
  getAssistantMessageId: () => string;
  publishPlaybackState: (playing: boolean, playbackText: string) => void;
  updateVoice: (updater: (current: VoiceSessionState) => VoiceSessionState) => void;
  setVoiceInputLocked: (locked: boolean, reason: string) => void;
  onPlaybackComplete: (playbackFailed: boolean) => void;
  notify: (message: string) => void;
}

interface UseTtsRuntimeOptions {
  settings: ProductSettings | null;
  callbacksRef: MutableRefObject<TtsRuntimeCallbacks>;
}

export function useTtsRuntime({ settings, callbacksRef }: UseTtsRuntimeOptions) {
  const ttsControllersRef = useRef<Set<AbortController>>(new Set());
  const playbackContextRef = useRef<AudioContext | null>(null);
  const audioQueueRef = useRef<SpeechQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const currentPlaybackNodeRef = useRef<AudioWorkletNode | null>(null);
  const currentPlaybackGainRef = useRef<GainNode | null>(null);
  const currentPlaybackDoneRef = useRef<(() => void) | null>(null);
  const currentSpeechRef = useRef<{
    item: SpeechQueueItem;
    playedMs: number;
    totalMs: number;
    complete: boolean;
  } | null>(null);
  const completedSpeechRef = useRef<string[]>([]);
  const voiceDeliveryRef = useRef<VoiceDeliveryState | null>(null);
  const ttsVoiceCueRef = useRef("neutral");
  const ttsWorkletLoadedRef = useRef(false);
  const playbackGenerationRef = useRef(0);
  const speechSegmenterRef = useRef(new SpeechSegmenter());
  const ttsResponseStartedRef = useRef(false);
  const qwenFullReplySubmittedRef = useRef(false);
  const voiceLevelRenderRef = useRef(0);

  const isAudioPlaying = useCallback(() => audioPlayingRef.current, []);
  const hasQueuedAudio = useCallback(() => audioQueueRef.current.length > 0, []);
  const getVoiceDelivery = useCallback(() => voiceDeliveryRef.current, []);
  const clearVoiceDelivery = useCallback(() => {
    voiceDeliveryRef.current = null;
  }, []);
  const getVoiceCue = useCallback(() => ttsVoiceCueRef.current, []);
  const setVoiceCue = useCallback((cue: string) => {
    const normalized = cue.toLowerCase();
    ttsVoiceCueRef.current = VOICE_CUES.has(normalized) ? normalized : "neutral";
    return ttsVoiceCueRef.current;
  }, []);
  const resetSpeechSegmentation = useCallback(() => {
    speechSegmenterRef.current.reset();
  }, []);
  const hasSubmittedQwenReply = useCallback(
    () => qwenFullReplySubmittedRef.current,
    [],
  );
  const markQwenReplySubmitted = useCallback(() => {
    qwenFullReplySubmittedRef.current = true;
  }, []);
  const shouldBufferQwenReply = useCallback(
    () => shouldBufferQwenReplyForSinglePass(
      settings,
      callbacksRef.current.isVoiceOpen(),
    ),
    [callbacksRef, settings],
  );

  const publishPlaybackState = useCallback((playing: boolean) => {
    const playbackText = playing
      ? currentSpeechRef.current?.item.text || callbacksRef.current.getVisibleReply()
      : "";
    callbacksRef.current.publishPlaybackState(playing, playbackText);
  }, [callbacksRef]);

  const setPlaybackDucked = useCallback((ducked: boolean) => {
    const gain = currentPlaybackGainRef.current;
    const context = playbackContextRef.current;
    if (!gain || !context) return;
    gain.gain.cancelScheduledValues(context.currentTime);
    gain.gain.setTargetAtTime(
      ducked ? TTS_SAFE_OUTPUT_GAIN * 0.25 : TTS_SAFE_OUTPUT_GAIN,
      context.currentTime,
      0.035,
    );
  }, []);

  const captureVoiceInterruption = useCallback((cause = "confirmed_user_speech") => {
    const current = currentSpeechRef.current;
    const completed = completedSpeechRef.current.join("");
    const progress = current?.totalMs
      ? Math.min(1, current.playedMs / current.totalMs)
      : 0;
    const currentPrefix = current
      ? estimateDeliveredPrefix(current.item.text, progress)
      : "";
    const heardText = `${completed}${currentPrefix}`;
    const spokenText = [
      ...completedSpeechRef.current,
      ...(current ? [current.item.text] : []),
      ...audioQueueRef.current.map((item) => item.text),
    ].join("");
    const visibleText = callbacksRef.current.getVisibleReply().trim();
    const visibleHeardIndex = heardText ? visibleText.indexOf(heardText) : 0;
    const unheardText = visibleText && visibleHeardIndex >= 0
      ? visibleText.slice(visibleHeardIndex + heardText.length).trim()
      : spokenText.slice(Math.min(heardText.length, spokenText.length));
    voiceDeliveryRef.current = {
      mode: "voice",
      run_id: callbacksRef.current.getLastVoiceRunId(),
      assistant_message_id: callbacksRef.current.getAssistantMessageId(),
      delivery_status: "interrupted",
      current_segment_id: current?.item.id || "",
      played_audio_ms: Math.max(0, Math.round(current?.playedMs || 0)),
      heard_text: heardText,
      unheard_text: unheardText || (heardText ? "" : visibleText),
      full_text_visible: Boolean(visibleText),
      position_confidence: current ? (current.complete ? 0.86 : 0.66) : 0.95,
      interruption_cause: cause,
    };
  }, [callbacksRef]);

  const stopAudio = useCallback(() => {
    publishPlaybackState(false);
    playbackGenerationRef.current += 1;
    audioQueueRef.current = [];
    speechSegmenterRef.current.reset();
    ttsResponseStartedRef.current = false;
    qwenFullReplySubmittedRef.current = false;
    ttsControllersRef.current.forEach((controller) => controller.abort());
    ttsControllersRef.current.clear();
    currentPlaybackNodeRef.current?.port.postMessage({ type: "stop" });
    currentPlaybackNodeRef.current?.disconnect();
    currentPlaybackNodeRef.current = null;
    currentPlaybackGainRef.current?.disconnect();
    currentPlaybackGainRef.current = null;
    currentPlaybackDoneRef.current?.();
    currentPlaybackDoneRef.current = null;
    audioPlayingRef.current = false;
    currentSpeechRef.current = null;
    completedSpeechRef.current = [];
    callbacksRef.current.updateVoice((current) => ({ ...current, level: 0 }));
  }, [callbacksRef, publishPlaybackState]);

  const resetResponseState = useCallback(() => {
    speechSegmenterRef.current.reset();
    ttsVoiceCueRef.current = "neutral";
    completedSpeechRef.current = [];
  }, []);

  const closePlaybackContext = useCallback(() => {
    const context = playbackContextRef.current;
    playbackContextRef.current = null;
    ttsWorkletLoadedRef.current = false;
    if (context) {
      context.onstatechange = null;
      if (context.state !== "closed") void context.close().catch(() => undefined);
    }
  }, []);

  const playbackAudioContext = useCallback(async () => {
    let context = playbackContextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext({ latencyHint: "interactive" });
      playbackContextRef.current = context;
      ttsWorkletLoadedRef.current = false;
      context.onstatechange = () => {
        if (
          audioPlayingRef.current
          && context?.state !== "running"
          && callbacksRef.current.isVoiceOpen()
        ) {
          currentPlaybackDoneRef.current?.();
          callbacksRef.current.updateVoice((current) => ({
            ...current,
            phase: "listening",
            error: "系统暂停了声音播放，请点击“恢复语音”",
            level: 0,
          }));
        }
      };
    }
    if (context.state !== "running") await context.resume();
    if (context.state !== "running") {
      throw new Error("声音播放尚未解锁，请重新点击开始通话");
    }
    return context;
  }, [callbacksRef]);

  const playbackContext = useCallback(async () => {
    const context = await playbackAudioContext();
    if (!ttsWorkletLoadedRef.current) {
      await context.audioWorklet.addModule("/assets/tts-playback-worklet.js");
      ttsWorkletLoadedRef.current = true;
    }
    return context;
  }, [playbackAudioContext]);

  const prepareSpeech = useCallback((item: SpeechQueueItem) => {
    if (item.prepared) return item.prepared;
    const controller = new AbortController();
    ttsControllersRef.current.add(controller);
    item.prepared = (async () => {
      const speed = num(settings?.audio.tts_speed, 1);
      const readyStartedAt = performance.now();
      while (true) {
        const status = await requestWithTimeout<{
          tts_ready?: boolean;
          tts_error?: string;
        }>("/api/v1/audio/status", { signal: controller.signal }, 3_000);
        if (status.tts_ready) break;
        if (performance.now() - readyStartedAt >= TTS_READY_WAIT_LIMIT_MS) {
          throw new Error(str(status.tts_error || "语音合成服务启动超时"));
        }
        if (callbacksRef.current.isVoiceOpen()) {
          callbacksRef.current.updateVoice((current) => ({
            ...current,
            error: str(status.tts_error || "回复已生成，语音播放正在准备"),
          }));
        }
        await waitWithSignal(TTS_READY_POLL_MS, controller.signal);
      }
      const responseTimeout = window.setTimeout(
        () => controller.abort("tts_response_timeout"),
        TTS_RESPONSE_TIMEOUT_MS,
      );
      let response: Response;
      try {
        response = await rawRequest("/api/v1/audio/tts/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: item.text,
            request_id: callbacksRef.current.getRunId() || item.id,
            speed,
            ...(settings?.audio.tts_provider === "qwen3-vllm"
              ? { voice_cue: item.voiceCue }
              : {}),
          }),
          signal: controller.signal,
        });
      } catch (error) {
        if (controller.signal.aborted) {
          if (controller.signal.reason === "tts_response_timeout") {
            throw new Error("语音合成响应超时");
          }
          throw new DOMException("Cancelled", "AbortError");
        }
        throw error;
      } finally {
        window.clearTimeout(responseTimeout);
      }
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(str((detail as { detail?: unknown }).detail || "语音合成失败"));
      }
      if (!response.body) throw new Error("浏览器不支持流式语音响应");
      const handle: PCMStreamHandle = {
        sampleRate: num(response.headers.get("X-Audio-Sample-Rate"), 24000),
        chunks: [],
        done: false,
        error: null,
        waiters: new Set(),
        pump: Promise.resolve(),
        totalInputSamples: 0,
        cancel: () => controller.abort("tts_playback_cancelled"),
      };
      const wake = () => {
        handle.waiters.forEach((resolve) => resolve());
        handle.waiters.clear();
      };
      const reader = response.body.getReader();
      handle.pump = (async () => {
        let pcmRemainder = new Uint8Array(0);
        try {
          while (true) {
            let idleTimer: number | null = null;
            const stalled = new Promise<never>((_resolve, reject) => {
              idleTimer = window.setTimeout(() => {
                controller.abort("tts_stream_idle_timeout");
                reject(new Error("语音合成流长时间没有继续返回数据"));
              }, TTS_STREAM_IDLE_TIMEOUT_MS);
            });
            let packet: ReadableStreamReadResult<Uint8Array>;
            try {
              packet = await Promise.race([reader.read(), stalled]);
            } finally {
              if (idleTimer != null) window.clearTimeout(idleTimer);
            }
            const { value, done } = packet;
            if (done) break;
            if (value?.byteLength) {
              const aligned = alignPCM16Chunk(pcmRemainder, value);
              pcmRemainder = aligned.remainder;
              if (aligned.pcm.byteLength) {
                handle.totalInputSamples += aligned.pcm.byteLength / 2;
                handle.chunks.push(aligned.pcm.buffer);
                wake();
              }
            }
          }
        } catch (error) {
          handle.error = error as Error;
        } finally {
          handle.done = true;
          wake();
          ttsControllersRef.current.delete(controller);
        }
      })();
      return handle;
    })().catch((error) => {
      ttsControllersRef.current.delete(controller);
      throw error;
    });
    return item.prepared;
  }, [callbacksRef, settings]);

  const playPCMStream = useCallback(async (
    item: SpeechQueueItem,
    handle: PCMStreamHandle,
    generation: number,
  ) => {
    if (!handle.chunks.length && !handle.done) {
      await new Promise<void>((resolve, reject) => {
        const onActivity = () => {
          window.clearTimeout(timeout);
          handle.waiters.delete(onActivity);
          resolve();
        };
        const timeout = window.setTimeout(() => {
          handle.waiters.delete(onActivity);
          handle.cancel();
          reject(new Error("语音合成已连接，但长时间没有返回音频"));
        }, TTS_FIRST_PCM_TIMEOUT_MS);
        handle.waiters.add(onActivity);
      });
    }
    if (handle.done && handle.totalInputSamples <= 0) {
      throw new Error("语音合成返回了空音频");
    }
    const context = await playbackContext();
    if (generation !== playbackGenerationRef.current) return;
    const node = new AudioWorkletNode(context, "mindspace-tts-playback", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    currentPlaybackNodeRef.current = node;
    const gain = context.createGain();
    currentPlaybackGainRef.current = gain;
    gain.gain.value = TTS_SAFE_OUTPUT_GAIN;
    node.connect(gain);
    gain.connect(context.destination);
    node.port.postMessage({
      type: "configure",
      sampleRate: handle.sampleRate,
      prebufferMs: 120,
    });
    let resolveEnded: () => void = () => undefined;
    const ended = new Promise<void>((resolve) => { resolveEnded = resolve; });
    currentPlaybackDoneRef.current = resolveEnded;
    currentSpeechRef.current = { item, playedMs: 0, totalMs: 0, complete: false };
    let playbackStarted = false;
    let playbackStartError: Error | null = null;
    let playbackStartTimer: number | null = null;
    node.port.onmessage = (event: MessageEvent<{
      type: string;
      value?: number;
      playedFrames?: number;
      outputSampleRate?: number;
    }>) => {
      if (event.data.type === "started") {
        playbackStarted = true;
        if (playbackStartTimer != null) window.clearTimeout(playbackStartTimer);
        if (callbacksRef.current.isVoiceOpen()) {
          publishPlaybackState(true);
          callbacksRef.current.updateVoice((current) => ({
            ...current,
            phase: "assistant-speaking",
            error: "",
          }));
        }
      } else if (event.data.type === "level") {
        const playedMs = num(event.data.playedFrames)
          / Math.max(1, num(event.data.outputSampleRate, context.sampleRate)) * 1000;
        const receivedMs = handle.totalInputSamples
          / Math.max(1, handle.sampleRate) * 1000;
        const estimatedMs = Math.max(receivedMs, item.text.length * 180);
        currentSpeechRef.current = {
          item,
          playedMs,
          totalMs: estimatedMs,
          complete: handle.done,
        };
        const now = performance.now();
        if (now - voiceLevelRenderRef.current >= 50) {
          voiceLevelRenderRef.current = now;
          callbacksRef.current.updateVoice((current) => ({
            ...current,
            level: num(event.data.value),
          }));
        }
      } else if (event.data.type === "ended") {
        resolveEnded();
      }
    };
    try {
      while (generation === playbackGenerationRef.current) {
        while (handle.chunks.length) {
          const chunk = handle.chunks.shift()!;
          node.port.postMessage({ type: "push", pcm: chunk }, [chunk]);
          if (playbackStartTimer == null && !playbackStarted) {
            playbackStartTimer = window.setTimeout(() => {
              if (playbackStarted || generation !== playbackGenerationRef.current) return;
              playbackStartError = new Error("音频数据已到达，但播放器没有启动");
              handle.cancel();
              resolveEnded();
            }, TTS_PLAYBACK_START_TIMEOUT_MS);
          }
        }
        if (handle.done) break;
        await new Promise<void>((resolve) => handle.waiters.add(resolve));
      }
      if (generation !== playbackGenerationRef.current) return;
      if (playbackStartError) throw playbackStartError;
      if (handle.error) throw handle.error;
      if (handle.totalInputSamples <= 0) throw new Error("语音合成返回了空音频");
      node.port.postMessage({ type: "end" });
      const expectedPlaybackMs = handle.totalInputSamples
        / Math.max(1, handle.sampleRate) * 1000;
      let endTimer: number | null = null;
      try {
        await Promise.race([
          ended,
          new Promise<never>((_resolve, reject) => {
            endTimer = window.setTimeout(
              () => reject(new Error("语音播放器结束等待超时")),
              Math.max(
                TTS_PLAYBACK_END_GRACE_MS,
                expectedPlaybackMs + TTS_PLAYBACK_END_GRACE_MS,
              ),
            );
          }),
        ]);
      } finally {
        if (endTimer != null) window.clearTimeout(endTimer);
      }
      if (playbackStartError) throw playbackStartError;
      if (!playbackStarted) throw new Error("音频播放器未能启动");
    } finally {
      if (playbackStartTimer != null) window.clearTimeout(playbackStartTimer);
      node.port.postMessage({ type: "stop" });
      node.disconnect();
      gain.disconnect();
      if (currentPlaybackNodeRef.current === node) currentPlaybackNodeRef.current = null;
      if (currentPlaybackGainRef.current === gain) currentPlaybackGainRef.current = null;
      if (currentPlaybackDoneRef.current === resolveEnded) currentPlaybackDoneRef.current = null;
    }
  }, [callbacksRef, playbackContext, publishPlaybackState]);

  const playQueue = useCallback(async () => {
    if (audioPlayingRef.current || !audioQueueRef.current.length) return;
    audioPlayingRef.current = true;
    const generation = playbackGenerationRef.current;
    let playbackFailed = false;
    while (audioQueueRef.current.length) {
      const item = audioQueueRef.current[0];
      if (!hasSpeakableContent(item.text)) {
        audioQueueRef.current.shift();
        continue;
      }
      try {
        const stream = await prepareSpeech(item);
        if (generation !== playbackGenerationRef.current) return;
        audioQueueRef.current.shift();
        await playPCMStream(item, stream, generation);
        if (generation === playbackGenerationRef.current) {
          completedSpeechRef.current.push(item.text);
        }
      } catch (error) {
        if (shouldSkipSpeechSegmentFailure(item.text, error)) {
          if (audioQueueRef.current[0] === item) audioQueueRef.current.shift();
          continue;
        }
        if (
          generation === playbackGenerationRef.current
          && (item.retries || 0) < 1
        ) {
          item.retries = (item.retries || 0) + 1;
          item.prepared = undefined;
          continue;
        }
        playbackFailed = true;
        if (audioQueueRef.current[0] === item) audioQueueRef.current.shift();
        if ((error as Error).name !== "AbortError") {
          const message = (error as Error).message;
          callbacksRef.current.setVoiceInputLocked(false, "tts_failed");
          if (callbacksRef.current.isVoiceOpen()) {
            callbacksRef.current.updateVoice((current) => ({
              ...current,
              phase: callbacksRef.current.isGenerating() ? "thinking" : "listening",
              error: `语音播放失败：${message}（仍在监听）`,
              level: 0,
            }));
          }
          callbacksRef.current.notify(message);
        }
        break;
      }
    }
    audioPlayingRef.current = false;
    if (
      generation === playbackGenerationRef.current
      && callbacksRef.current.isVoiceOpen()
    ) {
      publishPlaybackState(false);
      currentSpeechRef.current = null;
      voiceDeliveryRef.current = null;
      callbacksRef.current.updateVoice((current) => current.phase === "error"
        ? current
        : { ...current, phase: "listening", level: 0 });
      callbacksRef.current.onPlaybackComplete(playbackFailed);
    }
  }, [callbacksRef, playPCMStream, prepareSpeech, publishPlaybackState]);

  const enqueueSpeech = useCallback((
    text: string,
    force = false,
    voiceCue = ttsVoiceCueRef.current,
  ) => {
    if (
      (!force && !shouldAutomaticallyQueueSpeech(callbacksRef.current.isVoiceOpen()))
      || !text.trim()
    ) return;
    const speech = ttsResponseStartedRef.current
      ? text.trim()
      : stripLeadingTtsFiller(text);
    if (!hasSpeakableContent(speech)) return;
    ttsResponseStartedRef.current = true;
    audioQueueRef.current.push({
      id: crypto.randomUUID(),
      text: speech,
      voiceCue,
    });
    if (!audioPlayingRef.current) void playQueue();
  }, [callbacksRef, playQueue]);

  const acceptSpeechDelta = useCallback((delta: string, flush = false) => {
    const sentences = speechSegmenterRef.current.feed(
      delta,
      flush,
      callbacksRef.current.isVoiceOpen(),
    );
    sentences.forEach((sentence) => enqueueSpeech(sentence));
  }, [callbacksRef, enqueueSpeech]);

  const speak = useCallback((text: string, voiceCue = "neutral") => {
    stopAudio();
    enqueueSpeech(text, true, voiceCue);
  }, [enqueueSpeech, stopAudio]);

  return {
    acceptSpeechDelta,
    captureVoiceInterruption,
    clearVoiceDelivery,
    closePlaybackContext,
    enqueueSpeech,
    getVoiceCue,
    getVoiceDelivery,
    hasQueuedAudio,
    hasSubmittedQwenReply,
    isAudioPlaying,
    markQwenReplySubmitted,
    playbackAudioContext,
    publishPlaybackState,
    provider: str(settings?.audio.tts_provider),
    resetResponseState,
    resetSpeechSegmentation,
    setPlaybackDucked,
    setVoiceCue,
    shouldBufferQwenReply,
    speak,
    stopAudio,
  };
}
