// `vitest/config`'s defineConfig is a drop-in superset of vite's: it type-checks `test` below
// and still works unchanged for `vite dev` / `vite build` / `vite preview`.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // The Admin login talks to the real FastAPI backend (sales_app). Proxying keeps the
      // browser on one origin in dev, so the session cookie round-trips with no CORS setup.
      "/admin/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  preview: { port: 4173, strictPort: true },
  // The shell is 171 KB gzipped (budget: 250 KB); the raw-size warning is not the metric we track.
  build: { chunkSizeWarningLimit: 650 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/admin/test-setup.ts"],
    css: false,
  },
});
