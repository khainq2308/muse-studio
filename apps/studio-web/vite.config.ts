import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

const orchestratorTarget = process.env.ORCHESTRATOR_PROXY_TARGET ?? "http://127.0.0.1:8000";
// The dev/preview servers proxy an admin API, so they bind to loopback by default. Only the
// Compose dev container passes `--host 0.0.0.0` explicitly; Compose then publishes the port
// on the host's 127.0.0.1 only. Never default this to a LAN-reachable address.
export const DEV_SERVER_HOST = "127.0.0.1";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: DEV_SERVER_HOST,
    strictPort: true,
    proxy: {
      "/api": {
        target: orchestratorTarget,
      },
      "/healthz": {
        target: orchestratorTarget,
      },
    },
  },
  preview: {
    host: DEV_SERVER_HOST,
  },
});
