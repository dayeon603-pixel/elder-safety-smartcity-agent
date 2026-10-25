import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const SHOTS = "screenshots";
const MIN_FONT_PX = 14;

/** Every visible text node's computed font size (px), with the element for diagnostics. */
async function smallText(page: Page): Promise<string[]> {
  return page.evaluate((min) => {
    const bad: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) {
      const t = (n.textContent ?? "").trim();
      const el = n.parentElement;
      if (t === "" || el === null) continue;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (cs.display === "none" || cs.visibility === "hidden" || r.width === 0 || r.height === 0) continue;
      if (parseFloat(cs.fontSize) < min) bad.push(`${parseFloat(cs.fontSize)}px "${t.slice(0, 30)}"`);
    }
    return bad;
  }, MIN_FONT_PX);
}

test("phone root lands on the mobile page: no errors, no overflow, readable, all actions reachable", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let bytes = 0;
  page.on("response", async (r) => { if (!/demo\.mp4/.test(r.url())) { const h = r.headers()["content-length"]; bytes += h === undefined ? 0 : Number(h); } });
  const t0 = Date.now();
  await page.goto("./");
  await expect(page.getByTestId("mobile-root")).toBeVisible();
  const loadMs = Date.now() - t0;
  info.annotations.push({ type: "first-load-ms", description: String(loadMs) });
  expect(loadMs).toBeLessThan(8000);
  await page.waitForLoadState("networkidle");
  info.annotations.push({ type: "transferred-MB", description: (bytes / 1e6).toFixed(2) });
  expect(bytes).toBeLessThan(1_500_000);
  expect(page.url()).toMatch(/#\/m$/);
  await expect(page.getByTestId("m-training")).toContainText("훈련");
  await expect(page.getByTestId("m-video")).toHaveAttribute("src", /demo\.mp4$/);
  await expect(page.getByTestId("m-play")).toBeVisible();
  await expect(page.getByTestId("m-func")).toHaveCount(6);
  await expect(page.getByTestId("m-disclaimer")).toContainText("정부기관과 무관");
  await expect(page.getByTestId("m-result").first()).toBeVisible({ timeout: 15_000 });
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
  expect(m.sw).toBeLessThanOrEqual(m.iw);
  expect(await smallText(page)).toEqual([]);
  // actions are reachable by touch
  for (const id of ["m-play", "m-terminal", "m-twin", "m-pc"]) {
    const el = page.getByTestId(id);
    await el.scrollIntoViewIfNeeded();
    const b = await el.boundingBox();
    expect(b?.height ?? 0).toBeGreaterThanOrEqual(36);
    expect(b?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect((b?.x ?? 0) + (b?.width ?? 0)).toBeLessThanOrEqual(m.iw + 1);
  }
  await page.getByTestId("m-video-card").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/mobile-${info.project.name}-top.png` });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 2));
  await page.screenshot({ path: `${SHOTS}/mobile-${info.project.name}-mid.png` });
  expect(errors).toEqual([]);
});

test("the terminal experience opens from the phone page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./#/m");
  await page.getByTestId("m-terminal").click();
  await expect(page).toHaveURL(/#\/terminal/);
  await expect(page.getByTestId("terminal-root")).toBeVisible({ timeout: 20_000 });
  expect(errors).toEqual([]);
});

test("manual switch: 3D twin opens (even on a phone) and links back to the mobile page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./#/m");
  await page.getByTestId("m-pc").click();
  await expect(page).toHaveURL(/#\/twin/);
  await expect(page.getByTestId("twin-root")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("more-menu").locator("summary").click();
  await page.getByTestId("to-mobile").click();
  await expect(page.getByTestId("mobile-root")).toBeVisible();
  expect(errors).toEqual([]);
});

test("WebGL disabled: the 3D page still renders its panels (no blank page)", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") return null;
      return (orig as (...a: unknown[]) => unknown).call(this, type, ...rest) as never;
    } as typeof orig;
  });
  await page.goto("./#/twin");
  await expect(page.getByTestId("twin-root")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("body")).not.toBeEmpty();
  await ctx.close();
});
