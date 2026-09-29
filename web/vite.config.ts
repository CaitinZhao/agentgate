import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Dev server proxies /api to the platform started via `agentgate web` (default :8030).
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8030", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
