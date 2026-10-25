import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { stubShell, switchToPohang, walkAed, walkBriefingDefault, walkSilver } from "./zpFlow";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function scan(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

test.beforeEach(async ({ page }) => stubShell(page));

for (const width of [1440, 375]) {
  test.describe(`axe @${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("home", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("link", { name: "브리핑 열기" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "같은 예산, 다른 결과" })).toBeVisible();
      expect(await scan(page)).toEqual([]);
    });

    test("briefing, Seoul and non-Seoul", async ({ page }) => {
      await walkBriefingDefault(page);
      expect(await scan(page)).toEqual([]);
      await switchToPohang(page);
      expect(await scan(page)).toEqual([]);
    });

    test("silver, Q&A open", async ({ page }) => {
      await walkSilver(page);
      expect(await scan(page)).toEqual([]);
    });

    test("aed", async ({ page }) => {
      await walkAed(page);
      expect(await scan(page)).toEqual([]);
    });
  });
}
