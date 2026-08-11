export interface SpeechQueueItem {
  id: string;
  text: string;
  voiceCue: string;
  prepared?: Promise<PCMStreamHandle>;
  retries?: number;
}

export interface PCMStreamHandle {
  sampleRate: number;
  chunks: ArrayBuffer[];
  done: boolean;
  error: Error | null;
  waiters: Set<() => void>;
  pump: Promise<void>;
  totalInputSamples: number;
  cancel: () => void;
}

export interface VoiceCaptureGraph {
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  worklet: AudioWorkletNode;
  monitor: GainNode;
}

export interface WarmVoiceCapture {
  graph: VoiceCaptureGraph;
  expiresAt: number;
  timer: number;
}
