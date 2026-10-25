import { expect, test } from "@playwright/test";

const AGENT_WAIT_MS = 60_000;
const SHOT = "screenshots/final-field-agent-1440.png";

/** Button-triggered fall against the REAL backend (/agent/run, local Ollama, simulators). No stubs. */
test("real /agent/run: instant alert first, planning state, then measured steps and receipts", async ({ page, request }) => {
  const health = await request.get("/api/health");
  test.skip(!health.ok(), "backend not up");
  test.setTimeout(AGENT_WAIT_MS + 30_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/field");
  await expect(page.getByLabel(/장면 설명\(VLM\) 사용/)).not.toBeChecked();
  await expect(page.getByText(/일 최고기온 [\d.]+℃/)).toBeVisible();
  await page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" }).click();
  await expect(page.getByTestId("alert-stamp")).toBeVisible();
  await expect(page.getByTestId("latency")).toContainText(/감지→경보 생성 \d+\.\d ms/);
  const planningOrDone = page.getByTestId("agent-planning").or(page.getByTestId("agent-result")).or(page.getByText(/에이전트 서버를 쓸 수 없어/));
  await expect(planningOrDone.first()).toBeVisible();
  const result = page.getByTestId("agent-result");
  await expect(result).toBeVisible({ timeout: AGENT_WAIT_MS });
  await expect(result.getByText(/\d+\.\d ms/).first()).toBeVisible();
  await expect(result.locator("ul li").filter({ hasText: /119 시뮬레이터 수신/ })).toContainText("202");
  const link = result.getByRole("link", { name: "에이전트 기록" });
  const href = await link.getAttribute("href");
  expect(href).toMatch(/^\/api\/agent\/runs\/run-[0-9a-f]{12}$/);
  const rec = await request.get(href ?? "");
  expect(rec.status()).toBe(200);
  await result.scrollIntoViewIfNeeded();
  await page.screenshot({ path: SHOT, fullPage: true });
});
