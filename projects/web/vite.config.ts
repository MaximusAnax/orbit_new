import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the gateway, so the same URLs work in
// development and in the built app that the gateway serves itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
