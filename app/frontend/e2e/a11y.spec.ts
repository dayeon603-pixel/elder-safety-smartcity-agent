import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const PAGES = ["/", "/demo", "/results", "/runs", "/data", "/login", "/register"] as const;
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

for (const path of PAGES) {
  for (const width of [1440, 375]) {
    test(`axe: ${path} @${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
    });
  }
}
