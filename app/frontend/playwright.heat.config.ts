import { defineConfig, devices } from "@playwright/test";

const MOCK_PORT = 5191;
const STUB_PORT = 5192;
const SERVER_START_MS = 120_000;

/**
 * Heat-alert UI tests that need no backend.
 *  - "mock":  vite with VITE_HEAT_MOCK=1 (typed mocks in src/api/heatMocks.ts). Flow checks for when no backend is up.
 *  - "stub":  vite WITHOUT the flag; Playwright intercepts /api/heat/* so the real client, error mapping and
 *             response parsing run against hand-written responses.
 * Run: npm run e2e:heat (also runs the 영점 module specs zp.mock / zp.a11y)
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { trace: "retain-on-failure", locale: "ko-KR", timezoneId: "Asia/Seoul" },
  projects: [
    { name: "mock", testMatch: /heat\.mock\.spec\.ts|zp\.mock\.spec\.ts|zp\.a11y\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${MOCK_PORT}` } },
    { name: "zpshots", testMatch: /zp\.shots\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${MOCK_PORT}` } },
    { name: "stub", testMatch: /heat\.stub\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${STUB_PORT}` } },
  ],
  webServer: [
    {
      command: `npx vite --port ${MOCK_PORT}`,
      env: { VITE_HEAT_MOCK: "1" },
      url: `http://localhost:${MOCK_PORT}`,
      reuseExistingServer: false,
      timeout: SERVER_START_MS,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `npx vite --port ${STUB_PORT}`,
      url: `http://localhost:${STUB_PORT}`,
      reuseExistingServer: false,
      timeout: SERVER_START_MS,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
