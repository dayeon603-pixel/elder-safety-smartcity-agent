import { defineConfig, devices } from "@playwright/test";

/** /twin 시민 시점 e2e. Output goes outside the repo's test-results so parallel agents cannot wipe it. Needs `make dev` (backend :8010, frontend :5190). */
const OUT = process.env.BASEMAP_E2E_OUT ?? "/private/tmp/claude-501/-Users-chloekang/6b2363f5-b4ea-4b68-9849-07d4b95773a3/scratchpad/basemap-e2e";
export default defineConfig({
  testDir: "./e2e",
  testMatch: /basemap.spec.ts/,
  outputDir: OUT,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5190", locale: "ko-KR", timezoneId: "Asia/Seoul", trace: "off", launchOptions: { args: ["--ignore-gpu-blocklist"] } },
  projects: [{ name: "basemap", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
});
