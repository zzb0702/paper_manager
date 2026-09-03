import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Dev proxy so the SPA talks to the FastAPI backend same-origin
// (npm run dev serves on :5173, the API lives on :8830).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { dedupe: ["three"] },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8830",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 4096,
  },
});
