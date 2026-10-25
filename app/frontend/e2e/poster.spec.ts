import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/** Hero renders of the real twin for the closing slide: no generated imagery, only the app's own scene. */
const OUT = process.env.POSTER_OUT ?? "/Users/chloekang/kcf/deck/assets/hero";
const GOLDEN_HOUR = 18.5;
const CREDIT = "실제 화면 · 국토교통부 GIS건물통합정보 · ⓒ 브이월드 · 서울 생활인구";
const SETTLE_MS = 16000;

interface Variant { name: string; x: number; y: number; zoom: number; pitch: number; bearing: number; k: number; tw: number; terminal: boolean; follow?: boolean }
const VARIANTS: Variant[] = JSON.parse(process.env.POSTER_VARIANTS ?? "null") ?? [
  { name: "hero-wide", x: -250, y: 120, zoom: 13.6, pitch: 46, bearing: 180, k: 9, tw: 22, terminal: false },
  { name: "hero-mid", x: -1300, y: 2500, zoom: 15.4, pitch: 62, bearing: 170, k: 4, tw: 5, terminal: true },
  { name: "hero-stop", x: 0, y: 0, zoom: 18.3, pitch: 60, bearing: 0, k: 2.4, tw: 2, terminal: true, follow: true },
];

async function setHour(page: Page, hour: number): Promise<void> {
  await page.evaluate((h) => {
    const el = document.querySelector<HTMLInputElement>('[data-testid="hour-slider"]');
    if (el === null) throw new Error("no hour slider");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(el, String(h));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, hour);
}

test("poster renders", async ({ page }) => {
  await page.goto("/twin?poster=1");
  await expect(page.getByTestId("view-citizen")).toBeEnabled({ timeout: 60_000 });
  await expect(page.getByTestId("map-attribution")).toHaveAttribute("data-ground", "satellite", { timeout: 30_000 });
  await setHour(page, GOLDEN_HOUR);
  await page.waitForTimeout(1500);
  await page.evaluate(() => (window as unknown as { __story: { start: () => boolean } }).__story.start());
  await expect.poll(async () => page.evaluate(() => (window as unknown as { __story: { snapshot: () => { phase: string } } }).__story.snapshot().phase), { timeout: 60_000 }).toBe("arrived");
  await expect(page.getByTestId("story-terminal-title")).toBeVisible({ timeout: 30_000 });
  // UI off: the deck host fills the window; only the stop terminal and the figure's label stay
  await page.addStyleTag({ content: `
    [data-testid="twin-root"] { background: #000 !important; }
    [data-testid="twin-root"] * { box-shadow: none; }
    [data-testid="deck-host"] { position: fixed !important; inset: 0 !important; z-index: 100000 !important; width: 100vw !important; height: 100vh !important; }
    [data-testid="deck-host"] ~ *:not(canvas):not([data-testid="story-root"]) { display: none !important; }
    [data-testid="story-root"] { position: fixed !important; inset: 0 !important; z-index: 100001 !important; }
    [data-testid="story-root"] > *:not([data-testid="stop-anchor"]):not([data-testid="label-person"]) { display: none !important; }
    [data-testid="deck-host"] { background: linear-gradient(#35537d 0%, #8f8aa0 38%, #e9a86f 72%, #f7dbab 100%) !important; }
    [data-testid="stop-anchor"] > div { transform: translateY(-64px); }
    [data-testid="twin-root"] > *:not(:has([data-testid="deck-host"])) { visibility: hidden !important; }
  ` });
  await page.evaluate((c) => {
    const d = document.createElement("div");
    d.textContent = c;
    d.setAttribute("style", "position:fixed;left:28px;bottom:20px;z-index:100002;font:500 15px 'Pretendard Variable',Pretendard,system-ui,sans-serif;color:rgba(255,255,255,.88);text-shadow:0 1px 4px rgba(0,0,0,.8);letter-spacing:.2px");
    document.body.appendChild(d);
  }, CREDIT);
  await page.mouse.move(1900, 1070);
  for (const v of VARIANTS) {
    await page.evaluate((cfg) => (window as unknown as { __poster: { set: (c: unknown) => void } }).__poster.set(cfg), { x: v.x, y: v.y, zoom: v.zoom, pitch: v.pitch, bearing: v.bearing, k: v.k, tw: v.tw, follow: v.follow });
    await page.evaluate((show) => { for (const id of ["stop-anchor", "label-person"]) { const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`); if (el !== null) el.style.setProperty("display", show ? "" : "none", "important"); } }, v.terminal);
    await page.waitForTimeout(SETTLE_MS);
    await page.screenshot({ path: `${OUT}/${v.name}.png` });
  }
});
