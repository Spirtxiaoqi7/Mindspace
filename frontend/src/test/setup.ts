import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

Object.defineProperty(globalThis, "crypto", {
  configurable: true,
  value: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
});

Object.defineProperty(navigator, "mediaDevices", {
  configurable: true,
  value: { getUserMedia: vi.fn().mockRejectedValue(new Error("microphone unavailable")) },
});

globalThis.requestAnimationFrame = vi.fn(() => 1);
globalThis.cancelAnimationFrame = vi.fn();
