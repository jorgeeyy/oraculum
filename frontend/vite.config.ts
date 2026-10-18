import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const API_TARGET = "http://127.0.0.1:8000";
const API_PREFIXES = ["/chat", "/documents", "/retrieve", "/health"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        { target: API_TARGET, changeOrigin: true },
      ]),
    ),
  },
  test: {
    environment: "node",
  },
});
