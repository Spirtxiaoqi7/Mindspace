import { useCallback, useRef, useState } from "react";

import { request } from "../shared/api";
import {
  DEFAULT_AVATARS,
  normalizeAvatarConfig,
  type AvatarConfig,
  type CharacterSummary,
} from "../features/characters";
import type { ProductSettings } from "../features/settings";
import type { SessionSummary } from "../types";

interface InitializeApplicationDataOptions {
  loadSessions: () => Promise<SessionSummary[]>;
  loadCharacters: () => Promise<CharacterSummary[]>;
  selectPreferredSession: (
    sessions: SessionSummary[],
    characters: CharacterSummary[],
    routeSessionId: string,
    rememberedSessionId: string | null,
  ) => SessionSummary | undefined;
  openSession: (sessionId: string) => Promise<void>;
  onNoSession: (characters: CharacterSummary[]) => void;
}

const routeSessionId = () => {
  const match = window.location.hash.match(/^#\/chat\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
};

export function useApplicationData() {
  const [settings, setSettings] = useState<ProductSettings | null>(null);
  const [avatars, setAvatars] = useState<AvatarConfig>(DEFAULT_AVATARS);
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const initializationStartedRef = useRef(false);

  const initialize = useCallback(async ({
    loadSessions,
    loadCharacters,
    selectPreferredSession,
    openSession,
    onNoSession,
  }: InitializeApplicationDataOptions) => {
    if (initializationStartedRef.current) return;
    initializationStartedRef.current = true;
    try {
      const [loadedSettings, loadedSessions, loadedAvatars, loadedCharacters] = await Promise.all([
        request<ProductSettings>("/api/v1/settings"),
        loadSessions(),
        request<AvatarConfig>("/api/v1/avatar/config"),
        loadCharacters(),
      ]);
      setSettings(loadedSettings);
      setAvatars(normalizeAvatarConfig(loadedAvatars));
      const preferred = selectPreferredSession(
        loadedSessions,
        loadedCharacters,
        routeSessionId(),
        localStorage.getItem("mindspace.session"),
      );
      if (preferred) await openSession(preferred.session_id);
      else onNoSession(loadedCharacters);
    } finally {
      setInitialDataLoaded(true);
    }
  }, []);

  return {
    avatars,
    initialDataLoaded,
    initialize,
    setAvatars,
    setSettings,
    settings,
  };
}
