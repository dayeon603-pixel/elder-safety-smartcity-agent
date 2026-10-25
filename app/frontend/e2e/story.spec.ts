import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const MIN_FPS = 50;
const MAX_WALK_S = 16;
const MAX_FALL_S = 10;
const SHOTS = "screenshots";
const API = "/api";
const CLIP = { x: 320, y: 56, width: 1200, height: 952 };
test.use({ viewport: { width: 1920, height: 1080 } });

interface Snap { phase: string; speedup: number | null; terminal: { id: number | null; title: string; body: string } | null; pop: { status: string; dots: number | null; perDot: number | null }; run: { meters: number; totalRealSec: number } | null }
const snap = (page: Page): Promise<Snap> => page.evaluate(() => (window as unknown as { __story: { snapshot: () => unknown } }).__story.snapshot() as never);

async function open(page: Page): Promise<void> {
  await page.goto("/twin");
  await expect(page.getByTestId("building-count")).not.toHaveText("…");
  await expect(page.getByTestId("view-citizen")).toBeEnabled({ timeout: 60_000 });
}

test("시민 시점: 걷기 -> 정류장 단말 = /terminal/last -> 낙상(버튼 시연) -> AED 경로 -> 단말", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => { errors.push(e.message); if (errors.length <= 2) process.stdout.write(`PAGEERROR ${e.message} :: ${(e.stack ?? "").split("\n").slice(0, 10).join(" | ")}\n`); });
  await open(page);
  await expect.poll(async () => (await snap(page)).pop.status, { timeout: 30_000 }).toMatch(/ok|pending/);

  const tWalk = Date.now();
  await page.keyboard.press("3");
  await expect(page.getByTestId("view-citizen")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("story-hud")).toContainText("배속");
  await expect(page.getByTestId("story-hud")).toContainText("시연 인물");
  await expect(page.getByTestId("label-person")).toBeVisible();
  await page.waitForTimeout(5000);
  await page.screenshot({ path: `${SHOTS}/story-1-walking.png`, clip: CLIP });
  const lb = await page.getByTestId("label-person").boundingBox();
  if (lb !== null) await page.screenshot({ path: `${SHOTS}/story-1b-person-closeup.png`, clip: { x: lb.x - 120, y: lb.y + 10, width: 300, height: 220 } });

  // arrival: in-scene terminal text equals /terminal/last
  await expect.poll(async () => (await snap(page)).phase, { timeout: 60_000 }).toBe("arrived");
  const walkSec = (Date.now() - tWalk) / 1000;
  process.stdout.write(`DURATION step3 start->arrived ${walkSec.toFixed(1)} s\n`);
  expect(walkSec).toBeLessThan(MAX_WALK_S);
  await expect(page.getByTestId("story-terminal-title")).toBeVisible({ timeout: 20_000 });
  const last = (await (await request.get(`${API}/terminal/last`)).json()) as { message: { title: string; body: string } };
  await expect(page.getByTestId("story-terminal-title")).toHaveText(last.message.title);
  await expect(page.getByTestId("story-terminal-body")).toHaveText(last.message.body);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${SHOTS}/story-2-stop-terminal.png`, clip: CLIP });

  // fall by button (labelled), crosswalk signal board with real numbers
  const tFall = Date.now();
  await page.getByTestId("story-fall-btn").click();
  await page.mouse.move(900, 300);
  await expect(page.getByTestId("story-title")).toContainText("버튼 시연");
  await expect(page.getByTestId("signal-board")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("signal-text")).toContainText(/녹색|신호 자료 없음/);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/story-3-crosswalk-signal.png`, clip: CLIP });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/story-3b-person-down.png`, clip: CLIP });
  await expect(page.getByTestId("story-run")).toContainText("÷ 1.2 m/s", { timeout: 20_000 });
  await expect(page.getByTestId("story-confirm-btn")).toBeVisible();
  const fallSec = (Date.now() - tFall) / 1000;
  process.stdout.write(`DURATION step4 fall->route+confirm ${fallSec.toFixed(1)} s\n`);
  expect(fallSec).toBeLessThan(MAX_FALL_S);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/story-4-fall-route.png`, clip: CLIP });

  await page.getByTestId("story-confirm-btn").click();
  await page.mouse.move(900, 300);
  await expect(page.getByTestId("story-terminal-title")).toHaveText(/낙상/, { timeout: 90_000 });
  const last2 = (await (await request.get(`${API}/terminal/last`)).json()) as { message: { title: string; body: string } };
  await expect(page.getByTestId("story-terminal-title")).toHaveText(last2.message.title);
  await expect(page.getByTestId("story-terminal-body")).toHaveText(last2.message.body);
  await page.screenshot({ path: `${SHOTS}/story-5-fall-terminal.png`, clip: CLIP });

  // back to 관제 시점
  await page.getByTestId("story-exit").click();
  await expect(page.getByTestId("view-control")).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await snap(page)).phase, { timeout: 10_000 }).toBe("idle");
  expect(errors).toEqual([]);
});

test("fps: 38,488 buildings + 생활인구 dots, orbit 6 s", async ({ page }) => {
  await open(page);
  await expect.poll(async () => (await snap(page)).pop.status, { timeout: 30_000 }).toBe("ok");
  const st = await page.evaluate(async () => (window as unknown as { __twin: { orbit: (ms: number) => Promise<{ fps: number; frameMsP50: number; frameMsP95: number; buildings: number }> } }).__twin.orbit(6000));
  process.stdout.write(`FPS dots-on ${JSON.stringify(st)}\n`);
  await page.getByTestId("motion-toggle").uncheck();
  const off = await page.evaluate(async () => (window as unknown as { __twin: { orbit: (ms: number) => Promise<{ fps: number; frameMsP50: number; frameMsP95: number }> } }).__twin.orbit(6000));
  process.stdout.write(`FPS dots-off ${JSON.stringify(off)}\n`);
  await page.getByTestId("motion-toggle").check();
  await page.getByTestId("view-citizen").click();
  await page.waitForTimeout(3500);
  const street = await page.evaluate(async () => (window as unknown as { __twin: { orbit: (ms: number) => Promise<{ fps: number; frameMsP50: number; frameMsP95: number }> } }).__twin.orbit(5000));
  process.stdout.write(`FPS street ${JSON.stringify(street)}\n`);
  expect(street.fps).toBeGreaterThan(MIN_FPS);
  expect(st.fps).toBeGreaterThan(MIN_FPS);
});

test("관제 시점: 도시의 움직임 켜짐/꺼짐 + 시점 전환 후 복귀", async ({ page }) => {
  await open(page);
  await expect.poll(async () => (await snap(page)).pop.status, { timeout: 30_000 }).toBe("ok");
  await expect(page.getByTestId("pop-legend-text")).toContainText("인원 분포: 서울 생활인구 실측 · 이동 경로: 시각화");
  await expect(page.getByTestId("pop-legend-text")).toContainText(/1점 = \d+명/);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/story-0-city-in-motion.png`, clip: CLIP });
  await page.getByTestId("view-citizen").click();
  await page.waitForTimeout(3000);
  await page.getByTestId("view-control").click();
  await expect.poll(async () => (await snap(page)).phase, { timeout: 10_000 }).toBe("idle");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/story-6-back-to-control.png`, clip: CLIP });
  // scroll check: the layout is unchanged (no scroll)
  const m = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, ih: innerHeight, sw: document.documentElement.scrollWidth, iw: innerWidth }));
  expect(m.sh).toBeLessThanOrEqual(m.ih);
  expect(m.sw).toBeLessThanOrEqual(m.iw);
});

test("단계 ⑤ 낙상 (키 5): 관제 시점에서 바로 횡단보도 낙상 장면으로 들어간다", async ({ page }) => {
  await open(page);
  await page.keyboard.press("5");
  await page.getByRole("button", { name: "버튼 시연" }).first().click();
  await expect(page.getByTestId("view-citizen")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("story-title")).toContainText("사고 대응");
  await expect(page.getByTestId("signal-board")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await snap(page)).phase, { timeout: 10_000 }).toBe("idle");
});

test("heading check: camera 6 m in front of the walking person looking back", async ({ page }) => {
  await open(page);
  await page.keyboard.press("3");
  await expect.poll(async () => (await snap(page)).phase, { timeout: 20_000 }).toBe("walk");
  await page.evaluate(() => (window as unknown as { __story: { frontCam: (on: boolean) => void } }).__story.frontCam(true));
  await page.waitForTimeout(3500);
  await page.mouse.move(900, 300);
  await page.screenshot({ path: `${SHOTS}/story-heading-check.png`, clip: CLIP });
  await page.evaluate(() => (window as unknown as { __story: { frontCam: (on: boolean) => void } }).__story.frontCam(false));
});
