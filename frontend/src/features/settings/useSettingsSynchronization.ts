import { useEffect, type Dispatch, type SetStateAction } from "react";

import { request } from "../../shared/api";
import type { ProductSettings } from "../../types";

interface UseSettingsSynchronizationOptions {
  enabled: boolean;
  paused: boolean;
  setSettings: Dispatch<SetStateAction<ProductSettings | null>>;
}

export function useSettingsSynchronization({
  enabled,
  paused,
  setSettings,
}: UseSettingsSynchronizationOptions) {
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    const synchronizeAudioSelection = async () => {
      if (paused) return;
      try {
        const latest = await request<ProductSettings>("/api/v1/settings");
        if (disposed) return;
        setSettings((current) => {
          if (!current) return latest;
          const currentSelection = JSON.stringify({
            provider: current.audio.tts_provider,
            gpt: current.audio.tts_gpt_sovits_voice,
            qwen: current.audio.tts_qwen3_vllm_voice,
            auto: current.audio.auto_tts,
          });
          const latestSelection = JSON.stringify({
            provider: latest.audio.tts_provider,
            gpt: latest.audio.tts_gpt_sovits_voice,
            qwen: latest.audio.tts_qwen3_vllm_voice,
            auto: latest.audio.auto_tts,
          });
          return currentSelection === latestSelection
            ? current
            : { ...current, audio: { ...current.audio, ...latest.audio } };
        });
      } catch {
        // Core health and the existing error surfaces remain authoritative.
      }
    };
    const timer = window.setInterval(() => { void synchronizeAudioSelection(); }, 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [enabled, paused, setSettings]);
}
