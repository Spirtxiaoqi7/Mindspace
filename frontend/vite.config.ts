import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";

const portRegistry = JSON.parse(readFileSync(resolve(__dirname, "../config/service-ports.json"), "utf8"));
const corePort = Number(process.env.MINDSPACE_PORT || portRegistry.services.core);
if (!Number.isInteger(corePort) || corePort < 1 || corePort > 65535) throw new Error("Invalid Mindspace Core port");

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: "/assets/",
  build: {
    // Build artifacts must never share a directory with importable Python code.
    outDir: resolve(__dirname, "../src/mindspace_graph/static/app"),
    emptyOutDir: true,
    assetsDir: ".",
    sourcemap: mode !== "production" && process.env.MINDSPACE_DEV_SOURCEMAP === "1",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": `http://127.0.0.1:${corePort}`,
    },
  },
}));
