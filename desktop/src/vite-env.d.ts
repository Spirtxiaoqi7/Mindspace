/// <reference types="vite/client" />

interface Window {
  launcher: {
    snapshot(): Promise<LauncherSnapshot>;
    action(service: string, action: string): Promise<ActionResult>;
    all(action: string): Promise<ActionResult>;
    open(kind: string): Promise<ActionResult>;
    external(url: string): Promise<ActionResult>;
    maintenance(action: string): Promise<ActionResult>;
    selectRoot(): Promise<LauncherSnapshot>;
    selectStorage(): Promise<LauncherSnapshot>;
    shortcut(): Promise<ActionResult>;
    update(action: string, options?: { updateUrl?: string; channel?: string }): Promise<UpdateSnapshot>;
    component(action: string, id?: string): Promise<ComponentSnapshot>;
    voice(action: "snapshot" | "install" | "select", id?: string): Promise<TtsVoiceSnapshot>;
    runtime(action: "snapshot" | "install" | "install-all" | "cancel" | "retry" | "repair", id?: string): Promise<RuntimeSnapshot>;
    diagnostics(): Promise<ActionResult>;
    source(source: "china" | "official"): Promise<RuntimeSnapshot>;
    proxy(proxy?: string): Promise<ActionResult & { proxy?: string }>;
  };
}

interface ActionResult { ok: boolean; error?: string; warnings?: string[]; pid?: number; log?: string; path?: string }
interface ServiceReport { online: boolean; detail: Record<string, unknown> }
interface ModelReport { id: string; name: string; path: string; ready: boolean; optional?: boolean }
interface ComponentReport {
  id: string; name: string; description: string; path: string; ready: boolean; missing: string[];
  partial?: boolean;
  provider: string;
  optional?: boolean;
  status: string; progress: number; downloadedBytes: number; totalBytes: number; estimatedBytes: number; displayEstimatedBytes?: boolean;
  speedBps: number; message: string; error: string;
}
interface ComponentSnapshot { active: string; items: ComponentReport[] }
interface TtsVoiceReport {
  id: string; label: string; engine: string; componentId: string; modelDirectory: string;
  character: string; franchise: string; family: "v4" | "v2ProPlus"; releaseYear: number; sourceUrl: string; verified: boolean;
  estimatedBytes: number; ready: boolean; status: string; progress: number;
  downloadedBytes: number; totalBytes: number; speedBps: number; message: string; error: string;
}
interface TtsVoiceSnapshot { provider: string; current: string; items: TtsVoiceReport[]; ok?: boolean; error?: string; warning?: string }
type RuntimeInstallPhase = "idle" | "checking" | "downloading" | "verifying" | "installing" | "ready" | "cancelled" | "error";
interface RuntimeComponentState {
  id: string; name: string; description: string; version?: string; kind: string;
  required: boolean; optional?: boolean; ready: boolean; executable?: string;
  partial?: boolean;
  bundled?: boolean; downloadRequired?: boolean;
  displayEstimatedBytes?: boolean;
  hardwareAvailable?: boolean; unavailableReason?: string;
  category?: "base" | "voice" | string;
  status: RuntimeInstallPhase | string; progress: number; downloadedBytes: number;
  totalBytes: number; speedBps: number; message: string; error: string;
  operationId?: string; errorCode?: string; errorStage?: string; startedAt?: string; updatedAt?: string;
}
interface RuntimeManifest { schema_version: string; runtime_version: string; platform: "win32"; arch: "x64"; components: RuntimeComponentState[] }
interface RuntimeSnapshot {
  schemaVersion?: string; runtimeVersion?: string; active: string; ready: boolean;
  downloadSource?: "china" | "official";
  system: { supported?: boolean; writable?: boolean; freeBytes?: number; nvidia?: boolean; nvidiaDetail?: string; windowsRelease?: string };
  items: RuntimeComponentState[];
  pipeline?: { status: string; currentId: string; currentName: string; completed: number; total: number; progress: number; operationId?: string; errorCode?: string; error?: string };
}
interface UpdateSnapshot {
  status: string; progress: number; message: string; latestVersion: string; currentVersion: string;
  launcherVersion: string; releaseNotes: string; mandatory: boolean; downloaded: boolean;
  releaseTitle: string; releaseHistory: ReleaseAnnouncement[];
  rollbackAvailable: boolean; configured: boolean; updateUrl: string; channel: string; error: string;
  updateKind: "none" | "launcher" | "core"; coreAvailable: boolean; launcherAvailable: boolean;
  downloadedBytes: number; totalBytes: number; speedBps: number; remainingSeconds: number;
  releaseId: string; sequence: number; rolloutEligible: boolean;
  launcher?: { status: string; currentVersion: string; latestVersion: string; progress: number; downloaded: boolean; message: string; error: string } | null;
}
interface ReleaseAnnouncement { version: string; published_at: string; title: string; summary: string[] }
interface LauncherSnapshot {
  root: string; workspace: { ready: boolean; created: boolean; message: string; error: string };
  home: string;
  storage?: { active: boolean; progress: number; message: string; error: string };
  ps7: string; ps7Ready: boolean; ttsProvider: string;
  services: Record<string, ServiceReport>; models: ModelReport[]; components: ComponentSnapshot;
  voices: TtsVoiceSnapshot;
  runtime: RuntimeSnapshot;
}
