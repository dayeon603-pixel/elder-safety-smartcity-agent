import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fs from "node:fs";

const SHOTS = "screenshots";
const MIN_FPS = 45;
test.use({ viewport: { width: 1920, height: 1080 } });

async function open(page: Page): Promise<void> {
  await page.goto("/twin");
  await expect(page.getByTestId("building-count")).not.toHaveText("…");
  await page.waitForTimeout(4000);
}
const orbit = (page: Page, ms: number) => page.evaluate(async (t) => (window as unknown as { __twin: { orbit: (m: number) => Promise<{ fps: number; frameMsP50: number; frameMsP95: number }> } }).__twin.orbit(t), ms);

test("basemap: overview, fps, night, street", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await open(page);
  await page.screenshot({ path: `${SHOTS}/basemap-after-overview.png` });
  const day = await orbit(page, 5000);
  process.stdout.write(`BASEMAP FPS overview ${JSON.stringify(day)}\n`);
  expect(day.fps).toBeGreaterThan(MIN_FPS);
  await page.keyboard.press("6");
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${SHOTS}/basemap-after-night.png` });
  await page.keyboard.press("3");
  await expect(page.getByTestId("story-hud")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(5000);
  await page.mouse.move(900, 300);
  await page.screenshot({ path: `${SHOTS}/basemap-after-street.png` });
  const street = await orbit(page, 4000);
  process.stdout.write(`BASEMAP FPS street ${JSON.stringify(street)}\n`);
  expect(street.fps).toBeGreaterThan(MIN_FPS);
  expect(errors).toEqual([]);
});

const FAKE_TILE = process.env.FAKE_TILE ?? "";
test("satellite ground: tiles drawn, first-paint time, fps; 503 falls back to OSM", async ({ page }) => {
  test.skip(FAKE_TILE === "", "set FAKE_TILE=<png> to run against a stand-in tile proxy");
  const body = fs.readFileSync(FAKE_TILE);
  await page.route("**/api/twin/tiles/satellite/**", (r) => r.fulfill({ status: 200, contentType: "image/png", body }));
  const t0 = Date.now();
  await page.goto("/twin");
  await expect(page.getByTestId("map-attribution")).toHaveAttribute("data-ground", "satellite", { timeout: 30_000 });
  await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __basemap: { status: () => { firstTileMs: number | null } } }).__basemap.status().firstTileMs)) ?? -1, { timeout: 30_000 }).toBeGreaterThan(0);
  const first = await page.evaluate(() => (window as unknown as { __basemap: { status: () => { firstTileMs: number | null } } }).__basemap.status().firstTileMs);
  process.stdout.write(`SAT first tile ${first?.toFixed(0)} ms after navigation start; wall ${(Date.now() - t0) / 1000} s\n`);
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${SHOTS}/basemap-satellite-standin-overview.png` });
  const fps = await orbit(page, 5000);
  process.stdout.write(`SAT FPS ${JSON.stringify(fps)}\n`);
  expect(fps.fps).toBeGreaterThan(MIN_FPS);
  await page.unroute("**/api/twin/tiles/satellite/**");
  await page.route("**/api/twin/tiles/satellite/**", (r) => r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ code: "no_vworld_key" }) }));
  await page.reload();
  await expect(page.getByTestId("map-attribution")).toHaveAttribute("data-ground", "osm", { timeout: 30_000 });
});

test("real satellite proxy: first tile, fps (overview / night / street), screenshots", async ({ page }) => {
  const errs: string[] = [];
  page.on("pageerror", (e) => errs.push(`${e.message} :: ${(e.stack ?? "").split("\n").slice(0, 8).join(" | ")}`));
  const t0 = Date.now();
  await page.goto("/twin");
  await expect(page.getByTestId("map-attribution")).toHaveAttribute("data-ground", "satellite", { timeout: 30_000 });
  await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __basemap: { status: () => { firstTileMs: number | null } } }).__basemap.status().firstTileMs)) ?? -1, { timeout: 30_000 }).toBeGreaterThan(0);
  const first = await page.evaluate(() => (window as unknown as { __basemap: { status: () => { firstTileMs: number | null } } }).__basemap.status().firstTileMs);
  process.stdout.write(`REALSAT first tile ${first?.toFixed(0)} ms after page start (wall ${(Date.now() - t0) / 1000} s incl. scene)\n`);
  await page.waitForTimeout(6000);
  await page.mouse.move(900, 300);
  await page.screenshot({ path: `${SHOTS}/basemap-sat-overview.png` });
  const o = await orbit(page, 5000);
  process.stdout.write(`REALSAT FPS overview ${JSON.stringify(o)}\n`);
  expect(o.fps).toBeGreaterThan(MIN_FPS);
  await page.keyboard.press("6");
  await page.waitForTimeout(5000);
  await page.screenshot({ path: `${SHOTS}/basemap-sat-night.png` });
  const n = await orbit(page, 4000);
  process.stdout.write(`REALSAT FPS night ${JSON.stringify(n)}\n`);
  expect(n.fps).toBeGreaterThan(MIN_FPS);
  await page.keyboard.press("3");
  await expect(page.getByTestId("story-hud")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(6000);
  await page.mouse.move(900, 300);
  await page.screenshot({ path: `${SHOTS}/${process.env.STREET_SHOT ?? "basemap-sat-street"}.png` });
  const s = await orbit(page, 4000);
  process.stdout.write(`REALSAT FPS street ${JSON.stringify(s)}\n`);
  expect(s.fps).toBeGreaterThan(MIN_FPS);
  process.stdout.write(`REALSAT pageerrors ${errs.length} ${errs[0] ?? ""}\n`);
  expect(errs).toEqual([]);
});
