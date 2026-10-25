import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { walkCitizen } from "./heatFlow";

const OUT = "screenshots";

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  // Sticky header: a full-page capture draws it at the current scroll offset, so shoot from the top.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => document.fonts.ready);
}

test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

for (const width of [1440, 375]) {
  test.describe(`@${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("shot demo steps", async ({ page }) => {
      await page.goto("/demo");
      await expect(page.getByRole("region", { name: "날짜 목록" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-step1-${width}.png`, fullPage: true });

      await page.getByRole("button", { name: "다음: 지도 보기" }).click();
      await expect(page.getByRole("group", { name: /^지도:/ })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-step2-${width}.png`, fullPage: true });

      await page.getByRole("list", { name: "시군구 목록" }).getByRole("button").first().click();
      await expect(page.getByRole("heading", { name: "점수의 근거" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-step3-${width}.png`, fullPage: true });
    });

    test("shot plan and citizen", async ({ page }) => {
      await page.goto("/demo");
      await page.getByRole("button", { name: "다음: 지도 보기" }).click();
      await expect(page.getByRole("group", { name: /^지도:/ })).toBeVisible();
      await page.getByRole("button", { name: "다음: 오늘의 대응안" }).click();
      await expect(page.getByRole("heading", { name: "팀 배치" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-step4-${width}.png`, fullPage: true });
      await walkCitizen(page);
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-citizen-${width}.png`, fullPage: true });
    });

    test("shot home", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "같은 예산, 다른 결과" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "어떻게 만들었나" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-home-${width}.png`, fullPage: true });
    });

    test("shot results", async ({ page }) => {
      await page.goto("/results");
      await expect(page.locator(".recharts-line").first()).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${OUT}/heat-results-${width}.png`, fullPage: true });
    });
  });
}
