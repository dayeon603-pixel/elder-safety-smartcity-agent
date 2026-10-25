import { defineConfig, devices } from "@playwright/test";

const FRONTEND_PORT = 5190;
const BASE_URL = `http://localhost:${FRONTEND_PORT}`;
const SERVER_START_MS = 120_000;

/**
 * Runs against `make dev` (backend :8010, frontend :5190). If the dev servers are already up they are reused.
 * The comparison page needs one experiment in the DB: run `make exp` once.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Argon2id register + login is deliberately slow (~1s each, more under load).
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: { baseURL: BASE_URL, trace: "retain-on-failure", locale: "ko-KR", timezoneId: "Asia/Seoul" },
  projects: [
    { name: "chromium", testMatch: /flow\.spec\.ts|a11y\.spec\.ts|heat\.real\.spec\.ts|zp\.real\.spec\.ts|heat\.a11y\.spec\.ts|field\.spec\.ts|field\.real\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "shots", testMatch: /final\.shots.*\.spec\.ts|shots\.spec\.ts|heat\.shots\.spec\.ts|field\.shots\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "make dev",
    cwd: "..",
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: true,
    timeout: SERVER_START_MS,
    stdout: "ignore",
    stderr: "pipe",
  },
});
