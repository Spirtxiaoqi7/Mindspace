import { useEffect, useState } from "react";

import { getAudioStatus } from "../../shared/api";
import type { ProductSettings } from "../../types";

export function useAsrReadiness(enabled: boolean, settings: ProductSettings | null) {
  const [asrReady, setAsrReady] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const status = await getAudioStatus(controller.signal);
        if (!disposed) setAsrReady(Boolean(status.asr_ready));
      } catch {
        if (!disposed) setAsrReady(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [enabled, settings?.audio.asr_endpoint, settings?.audio.asr_model, settings?.audio.asr_provider]);

  return asrReady;
}
