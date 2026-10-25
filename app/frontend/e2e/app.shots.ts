import { mkdirSync } from "node:fs";
import { test } from "@playwright/test";
import { stubShell } from "./zpFlow";

const OUT = "screenshots";
const SIZES = [1440, 768, 375] as const;
/** 동작구 has real crosswalk rows; 관악구 shows the "자료 없음" state. */
const CASES: readonly { name: string; query: string }[] = [
  { name: "dongjak", query: "sgg=11590&h=23&day=weekday&speed=0.7" },
  { name: "gwanak", query: "sgg=11620&h=14&day=sat&speed=0.7" },
];

test.beforeAll(() => mkdirSync(OUT, { recursive: true }));
test.beforeEach(async ({ page }) => stubShell(page));

for (const width of SIZES) {
  for (const c of CASES) {
    test(`shot app ${c.name} @${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/app?${c.query}`);
      await page.getByTestId("map-summary").waitFor();
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}/app-${c.name}-${width}.png`, fullPage: true });
    });
  }
}
