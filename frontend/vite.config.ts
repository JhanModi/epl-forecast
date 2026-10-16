/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the dashboard runs on :5173 and proxies API calls to the FastAPI server on :8000.
// In production FastAPI serves the built files itself, so the app always calls a relative /api path.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { sourcemap: true, chunkSizeWarningLimit: 600 },
  test: { environment: "jsdom", globals: false, setupFiles: ["./src/test/setup.ts"] },
});
