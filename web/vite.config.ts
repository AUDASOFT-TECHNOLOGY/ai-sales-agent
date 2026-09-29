import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  // The shell is 171 KB gzipped (budget: 250 KB); the raw-size warning is not the metric we track.
  build: { chunkSizeWarningLimit: 650 },
});
