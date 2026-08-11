import type { AppView } from "../features/characters";

export type ModalName =
  | "settings"
  | "knowledge"
  | "memory"
  | "profile"
  | "diagnostics"
  | "voice-entry"
  | null;

export function appViewFromHash(hash = window.location.hash): AppView {
  if (hash.startsWith("#/characters")) return "characters";
  if (hash.startsWith("#/fate")) return "draw";
  if (hash.startsWith("#/modes")) return "modes";
  if (/^#\/chat\/[^/]+\/scenes/.test(hash)) return "scenes";
  return "chat";
}
