import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { walkCitizen } from "./heatFlow";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function scan(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

for (const width of [1440, 375]) {
  test.describe(`axe @${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("demo step 1", async ({ page }) => {
      await page.goto("/demo");
      await expect(page.getByRole("region", { name: "날짜 목록" })).toBeVisible();
      expect(await scan(page)).toEqual([]);
    });

    test("demo steps 2 and 3", async ({ page }) => {
      await page.goto("/demo");
      await page.getByRole("button", { name: "다음: 지도 보기" }).click();
      await expect(page.getByRole("group", { name: /^지도:/ })).toBeVisible();
      expect(await scan(page)).toEqual([]);
      await page.getByRole("group", { name: /^지도:/ }).getByRole("button").first().focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { name: "점수의 근거" })).toBeVisible();
      expect(await scan(page)).toEqual([]);
    });

    test("demo step 4 plan", async ({ page }) => {
      await page.goto("/demo");
      await page.getByRole("button", { name: "다음: 지도 보기" }).click();
      await expect(page.getByRole("group", { name: /^지도:/ })).toBeVisible();
      await page.getByRole("button", { name: "다음: 오늘의 대응안" }).click();
      await expect(page.getByRole("heading", { name: "팀 배치" })).toBeVisible();
      expect(await scan(page)).toEqual([]);
    });

    test("citizen", async ({ page }) => {
      await walkCitizen(page);
      expect(await scan(page)).toEqual([]);
    });

    test("results", async ({ page }) => {
      await page.goto("/results");
      await expect(page.locator(".recharts-line").first()).toBeVisible();
      expect(await scan(page)).toEqual([]);
    });
  });
}
