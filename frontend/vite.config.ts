import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (npm run dev), proxy API/WS calls to the dockerized Nginx gateway
// so the browser sees everything as same-origin - no CORS setup needed,
// and it mirrors how the production build (served by that same Nginx)
// behaves.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/auth": "http://localhost:8000",
      "/users": "http://localhost:8000",
      "/messages": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
