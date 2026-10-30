import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const API_TARGET = "http://127.0.0.1:8000";
const API_PREFIXES = [
  "/chat",
  "/documents",
  "/retrieve",
  "/conversations",
  "/health",
  "/auth",
  "/admin",
  "/usage",
];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        {
          target: API_TARGET,
          changeOrigin: true,
          // A browser navigation (GET + Accept: text/html) to a SPA route
          // must be served by Vite's own shell, never proxied to the
          // backend: uvicorn would answer with the BUILT dist/index.html,
          // whose hashed /assets/*.js don't exist on the dev origin (Vite
          // would fall back to serving HTML for them) -> blank page on
          // refresh/deep link. Returning the URL unchanged skips the proxy
          // and lets Vite's own html fallback answer.
          //
          // Scope mirrors the backend's SPA fallback exactly: the top-level
          // routes plus nested /admin pages. Anything else under a proxied
          // prefix — notably /documents/{id}/file, which iframes load with
          // Accept: text/html — must keep flowing to the API. fetch() calls
          // send Accept: */* (or JSON) and proxy regardless.
          bypass: (req) => {
            if (req.method !== "GET") return undefined;
            if (!(req.headers.accept ?? "").includes("text/html")) {
              return undefined;
            }
            const path = (req.url ?? "/").split("?")[0].replace(/\/+$/, "");
            const spaRoutes = ["/chat", "/documents", "/login", "/register"];
            return spaRoutes.includes(path) || path.startsWith("/admin")
              ? (req.url ?? "/")
              : undefined;
          },
        },
      ]),
    ),
  },
  test: {
    environment: "node",
  },
});
