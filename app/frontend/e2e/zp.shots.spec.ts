import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { stubShell, switchToPohang, walkAed, walkBriefingDefault, walkSilver } from "./zpFlow";

const OUT = "screenshots";

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => document.fonts.ready);
}

test.beforeAll(() => mkdirSync(OUT, { recursive: true }));
test.beforeEach(async ({ page }) => stubShell(page));

for (const width of [1440, 375]) {
  test.describe(`@${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("shot home", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("link", { name: "브리핑 열기" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "같은 예산, 다른 결과" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/zp-home-${width}.png`, fullPage: true });
    });

    test("shot briefing", async ({ page }) => {
      await walkBriefingDefault(page);
      await settle(page);
      await page.screenshot({ path: `${OUT}/zp-briefing-${width}.png`, fullPage: true });
      await switchToPohang(page);
      await settle(page);
      await page.screenshot({ path: `${OUT}/zp-briefing-pohang-${width}.png`, fullPage: true });
    });

    test("shot silver and aed", async ({ page }) => {
      await walkSilver(page);
      await settle(page);
      await page.screenshot({ path: `${OUT}/zp-silver-${width}.png`, fullPage: true });
      await walkAed(page);
      await settle(page);
      await page.screenshot({ path: `${OUT}/zp-aed-${width}.png`, fullPage: true });
    });
  });
}

test("shot briefing print", async ({ page }) => {
  await page.setViewportSize({ width: 794, height: 1123 });
  await walkBriefingDefault(page);
  await page.emulateMedia({ media: "print" });
  await settle(page);
  await page.screenshot({ path: `${OUT}/zp-briefing-print.png`, fullPage: true });
});
