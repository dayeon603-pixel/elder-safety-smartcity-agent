import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const DEV_PORT = 5190;
/** Demo token for /agent/run and /sim/*: injected by the dev proxy from KCF_DEMO_TOKEN so it never lands in the bundle. */
const DEMO_TOKEN_HEADER = process.env.KCF_DEMO_TOKEN_HEADER ?? "X-Demo-Token";
const DEMO_TOKEN = process.env.KCF_DEMO_TOKEN;
const BACKEND = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 700 },
  server: {
    // LAN: the phone/tablet terminal opens http://<laptop-ip>:5190/terminal; the browser only ever talks to this origin (/api proxy).
    host: true,
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, ""),
        ...(DEMO_TOKEN === undefined || DEMO_TOKEN === "" ? {} : { headers: { [DEMO_TOKEN_HEADER]: DEMO_TOKEN } }),
      },
    },
  },
  preview: { host: true, port: DEV_PORT, strictPort: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
