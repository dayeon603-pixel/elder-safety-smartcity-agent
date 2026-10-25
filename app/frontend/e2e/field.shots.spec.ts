import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { stubField } from "./fieldStubs";

const OUT = "screenshots";
test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

for (const width of [1440, 375]) {
  test.describe(`@${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("shot field", async ({ page }) => {
      await stubField(page, "ok");
      await page.goto("/field");
      await expect(page.getByRole("button", { name: "카메라 켜기" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${OUT}/field-idle-${width}.png`, fullPage: true });

      const option = await page.getByLabel("횡단보도", { exact: true }).locator("option", { hasText: "25.0 m" }).first().getAttribute("value");
      await page.getByLabel("횡단보도", { exact: true }).selectOption(option);
      await page.locator("#scenario-a").getByLabel(/경과 시간/).fill("20");
      await page.locator("#scenario-a").getByRole("button", { name: "시작", exact: true }).click();
      await page.locator("#scenario-a").getByRole("button", { name: "일시정지" }).click();
      await page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" }).click();
      await expect(page.getByTestId("alert-stamp")).toBeVisible();
      await expect(page.getByRole("region", { name: "로컬 에이전트 결과" })).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `${OUT}/field-alert-${width}.png`, fullPage: true });

      await page.getByRole("button", { name: "발표 모드 켜기" }).click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `${OUT}/field-present-${width}.png`, fullPage: true });
    });
  });
}
