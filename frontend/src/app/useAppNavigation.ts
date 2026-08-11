import { useCallback, useState } from "react";

import type { AppView } from "../features/characters";
import { appViewFromHash } from "./viewState";

const hashForView = (view: AppView, sessionId = "") => {
  if (view === "modes") return "#/modes";
  if (view === "characters") return "#/characters";
  if (view === "draw") return "#/fate";
  if (view === "scenes") return sessionId ? `#/chat/${encodeURIComponent(sessionId)}/scenes` : "#/chat/scenes";
  if (sessionId) return `#/chat/${encodeURIComponent(sessionId)}`;
  return window.location.hash.replace(/\/scenes(?:\/.*)?$/, "") || "#/chat";
};

export function useAppNavigation() {
  const [appView, setAppView] = useState<AppView>(() => appViewFromHash());

  const navigate = useCallback((view: AppView, sessionId = "") => {
    setAppView(view);
    window.history.replaceState(null, "", hashForView(view, sessionId));
  }, []);

  return { appView, navigate, setAppView };
}
