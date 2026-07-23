import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  base: "/assets/",
  build: {
    outDir: resolve(__dirname, "../src/mindspace_graph/web"),
    emptyOutDir: true,
    assetsDir: ".",
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
