import { defineConfig } from "@playwright/test";

/**
 * 현장 단말 e2e. Needs the frontend (default :5190) and backend (default :8010) already running; publishes with the demo token from .env.local.
 *   npx playwright test -c playwright.terminal.config.ts
 * Override with TERMINAL_BASE_URL / TERMINAL_BACKEND_URL / TERMINAL_OUTPUT_DIR.
 */
const DEFAULT_OUTPUT = "/private/tmp/claude-501/-Users-chloekang/6b2363f5-b4ea-4b68-9849-07d4b95773a3/scratchpad/terminal-e2e";
export default defineConfig({
  testDir: "./e2e",
  testMatch: /terminal\.spec\.ts/,
  outputDir: process.env.TERMINAL_OUTPUT_DIR ?? DEFAULT_OUTPUT,
  workers: 1,
  retries: 0,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { baseURL: process.env.TERMINAL_BASE_URL ?? "http://localhost:5190", locale: "ko-KR", timezoneId: "Asia/Seoul", trace: "retain-on-failure" },
});
