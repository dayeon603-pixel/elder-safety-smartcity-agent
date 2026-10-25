import { defineConfig, devices } from "@playwright/test";

const EDGE_PORT = 5193;
const SERVER_START_MS = 120_000;

/**
 * Edge-case / chaos UI tests that need no backend: plain vite (no mock flag) so the real clients, error mapping and
 * parsers run against Playwright-intercepted responses (aborted connections, 429, 422, 503, empty data).
 * Run: npx playwright test -c playwright.edge.config.ts
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /edge\..*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${EDGE_PORT}`, trace: "retain-on-failure", locale: "ko-KR", timezoneId: "Asia/Seoul" },
  webServer: {
    command: `npx vite --port ${EDGE_PORT}`,
    url: `http://localhost:${EDGE_PORT}`,
    reuseExistingServer: false,
    timeout: SERVER_START_MS,
    stdout: "ignore",
    stderr: "pipe",
  },
});
