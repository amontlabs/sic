import { defineConfig, devices } from "@playwright/test";

const MOCK_PORT = Number(process.env.MOCK_PORT ?? 8797);
const WEB_PORT = Number(process.env.WEB_PORT ?? 5199);

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false, // the mock server keeps in-memory state
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /screenshots\.spec/ },
    { name: "screenshots", use: { ...devices["Desktop Chrome"] }, testMatch: /screenshots\.spec/ },
  ],
  webServer: [
    {
      command: `bun e2e/mock-server.ts`,
      env: { PORT: String(MOCK_PORT) },
      url: `http://localhost:${MOCK_PORT}/api/debates`,
      reuseExistingServer: !process.env.CI,
      stdout: "ignore",
    },
    {
      command: `bunx vite --port ${WEB_PORT} --strictPort`,
      env: { SIC_API: `http://localhost:${MOCK_PORT}` },
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
