import { defineConfig, devices } from "@playwright/test";

const FRONTEND_PORT = 5190;
const SERVER_START_MS = 120_000;

/** /app 영점 도시 운영 지도. Runs against `make dev` (backend :8010 + frontend :5190); an already running dev server is reused. */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /app\.(spec|shots)\.ts/,
  outputDir: "test-results-app",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 150_000,
  expect: { timeout: 45_000 },
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${FRONTEND_PORT}`, trace: "retain-on-failure", locale: "ko-KR", timezoneId: "Asia/Seoul", ...devices["Desktop Chrome"] },
  webServer: { command: "make dev", cwd: "..", url: `http://localhost:${FRONTEND_PORT}/api/health`, reuseExistingServer: true, timeout: SERVER_START_MS, stdout: "ignore", stderr: "pipe" },
});
