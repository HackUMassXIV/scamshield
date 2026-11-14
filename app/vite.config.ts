import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: `npm run dev` proxies everything to the edge on :8000 (which proxies the cloud).
const EDGE = process.env.EDGE_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    proxy: {
      "/api": EDGE,
      "/cloud": EDGE,
      "/ws": { target: EDGE, ws: true },
      "/cloud-ws": { target: EDGE, ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
