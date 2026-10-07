import { defineConfig, devices } from "@playwright/test";

// Runs against the REAL stack: `cd pipeline && uv run python -m sic.server` and `bun run dev`.
// Usage: bunx playwright test -c playwright.real.config.ts
export default defineConfig({
  testDir: "./e2e-real",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.SIC_WEB ?? "http://localhost:5173",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
  },
});
