/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The API server (pipeline/src/sic/server.py) runs on :8787 by default.
// SIC_API lets the e2e suite point the proxy at the mock server instead.
const API = process.env.SIC_API ?? "http://localhost:8787";

const proxy = {
  "/api": { target: API, changeOrigin: true },
  "/media": { target: API, changeOrigin: true },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
