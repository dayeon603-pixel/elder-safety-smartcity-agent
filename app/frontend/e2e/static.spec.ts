import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/** Public 시연판: dist-pages served under the repo subpath, backend port blocked, every /api call must be answered by recorded fixtures. */
const BASE = "/elder-safety-smartcity-agent/";
const SHOTS = "screenshots";
const DEAD_BACKEND = /(127\.0\.0\.1|localhost):(8010|8000)\b/;

async function arm(page: Page): Promise<{ netErrors: string[]; apiLeaks: string[] }> {
  const netErrors: string[] = [];
  const apiLeaks: string[] = [];
  await page.route(DEAD_BACKEND, (r) => r.abort());
  page.on("response", (res) => { const u = new URL(res.url()); if (res.status() >= 400 && !u.pathname.includes("/tiles/")) netErrors.push(`${res.status()} ${u.pathname}${u.search}`); });
  page.on("request", (req) => { const u = new URL(req.url()); if (u.pathname.startsWith("/api/")) apiLeaks.push(u.pathname); });
  return { netErrors, apiLeaks };
}

async function open(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(BASE);
  await expect(page).toHaveURL(/#\/twin$/);
  await expect(page.getByTestId("building-count")).not.toHaveText("…");
  await expect(page.getByTestId("action-card").first()).toBeVisible();
  await expect(page.getByTestId("static-badge")).toContainText("공개 시연판 · 팀 제작 시연 (서버·발송 없음)");
}

test("공무원의 하루 (static): 6 steps, fall via button, agent playback, night, rain", async ({ page }) => {
  const net = await arm(page);
  await open(page);
  await expect(page.getByTestId("ref-date")).toContainText("2025-07-22");
  await page.screenshot({ path: `${SHOTS}/static-0-open.png` });

  await page.keyboard.press("1");
  await expect(page.getByTestId("scn-s1")).toHaveAttribute("data-done", "1");
  await page.keyboard.press("2");
  await expect(page.getByTestId("clock")).toContainText("17:00");
  await expect(page.getByTestId("effect-chip")).toContainText(/\d+(\.\d+)?%/);
  await expect(page.getByTestId("pv-to")).not.toContainText("확인 중");
  await page.getByTestId("adopt").first().click();
  await expect(page.getByTestId("audit-line").first()).toContainText("변조 탐지 기록");
  await page.getByTestId("approve").first().click();
  await expect(page.getByTestId("approval-chip").first()).toHaveAttribute("data-stage", "3");
  await page.getByTestId("hold").nth(1).click();
  await page.getByTestId("hold-reason").fill("현장 확인 후 재검토");
  await page.getByTestId("hold-save").click();
  await expect(page.getByTestId("hold-line")).toContainText("현장 확인 후 재검토");
  await page.getByTestId("tab-spec").click();
  await expect(page.getByTestId("doc-body")).toContainText("urn:oasis:names:tc:emergency:cap:1.2");
  await page.screenshot({ path: `${SHOTS}/static-2-heat-spec.png` });
  await page.getByTestId("tab-action").click();

  await page.keyboard.press("3");
  await expect(page.getByTestId("clock")).toContainText("17:30");
  await page.waitForTimeout(8000);
  await page.screenshot({ path: `${SHOTS}/static-3-deliver.png` });

  await page.keyboard.press("4");
  await expect(page.getByTestId("mode-ours")).toHaveAttribute("aria-pressed", "true", { timeout: 8000 });
  await expect(page.getByTestId("scoreboard")).toContainText("현행");

  await page.keyboard.press("5");
  await page.getByTestId("fall-button-demo").click();
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "pending");
  await expect(page.getByTestId("fall-alert-text")).toContainText(/낙상 의심 감지 · 17:40/, { timeout: 15_000 });
  await expect(page.getByTestId("alert-panel")).toContainText(/가까운 열린 AED · .+ \d+ m/, { timeout: 20_000 });
  await page.screenshot({ path: `${SHOTS}/static-4a-confirm.png` });
  await page.getByTestId("confirm-fall").click();
  await expect(page.getByTestId("static-badge")).toContainText("기록된 실행");
  await page.getByTestId("tab-spec").click();
  await expect(page.getByTestId("doc-body")).toContainText("Bundle", { timeout: 60_000 });
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  await page.screenshot({ path: `${SHOTS}/static-4-fall.png` });

  await page.keyboard.press("6");
  await expect(page.getByTestId("action-cards")).toHaveAttribute("data-night", "1");
  await expect(page.getByTestId("action-card").first()).toContainText("24시간 개방 요청");
  await page.getByTestId("mode-ours").click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SHOTS}/static-5-night.png` });
  await page.keyboard.press("7");
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${SHOTS}/static-6-rain.png` });

  expect(net.apiLeaks, "no request may reach a real /api").toEqual([]);
  expect(net.netErrors.filter((e) => !/\/(favicon)/.test(e)), "fixture misses").toEqual([]);
  const misses = await page.evaluate(() => (window as unknown as { __staticBackend: { misses: string[] } }).__staticBackend.misses);
  expect(misses).toEqual([]);
});

test("공개 낙상 영상 offline from bundled files", async ({ page }) => {
  await arm(page);
  await open(page);
  await page.getByTestId("tab-camera").click();
  await page.getByRole("button", { name: "공개 낙상 영상" }).click();
  await expect(page.getByTestId("live-fps")).toContainText("실시간 계산", { timeout: 45_000 });
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "pending", { timeout: 45_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/static-7-urfall.png` });
});

test("basemap falls back to OSM (no satellite, no key)", async ({ page }) => {
  await arm(page);
  await open(page);
  await page.waitForTimeout(3000);
  const mode = await page.evaluate(() => document.body.innerText);
  expect(mode).not.toMatch(/V-?World/i);
  await page.screenshot({ path: `${SHOTS}/static-8-osm.png` });
});

test("QR points at the public /terminal URL; same-browser message flows /twin -> /terminal", async ({ context, page }) => {
  await arm(page);
  await open(page);
  await page.getByTestId("qr-button").click();
  await expect(page.getByTestId("qr-url")).toContainText(`${BASE}#/terminal`);
  await page.screenshot({ path: `${SHOTS}/static-qr.png` });
  await page.keyboard.press("Escape");

  const term = await context.newPage();
  await arm(term);
  await term.goto(`${BASE}#/terminal`);
  await expect(term.getByTestId("terminal-root")).toBeVisible();
  await expect(term.getByTestId("static-badge")).toContainText("시연판");
  await term.getByRole("button").first().click();
  await term.screenshot({ path: `${SHOTS}/static-terminal-before.png` });

  await page.bringToFront();
  await page.keyboard.press("2");
  await page.getByTestId("adopt").first().click();
  await page.getByTestId("approve").first().click();
  await expect(term.getByTestId("terminal-root")).toHaveAttribute("data-kind", "heat", { timeout: 15_000 });
  await term.screenshot({ path: `${SHOTS}/static-terminal-heat.png` });
});

test("deep links: /twin and /terminal via 404 fallback and hash", async ({ page }) => {
  await arm(page);
  await page.goto(`${BASE}twin`);
  await expect(page).toHaveURL(/#\/twin$/);
  await expect(page.getByTestId("twin-root")).toBeVisible();
  await page.goto(`${BASE}terminal`);
  await expect(page).toHaveURL(/#\/terminal$/);
  await expect(page.getByTestId("terminal-root")).toBeVisible();
});
