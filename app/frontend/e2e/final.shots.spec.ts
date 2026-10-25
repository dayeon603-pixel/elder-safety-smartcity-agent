import { expect, test } from "@playwright/test";

/** Final screenshots of every page against the REAL API (no stubs). */
const PAGES: readonly { slug: string; path: string; ready: RegExp | string }[] = [
  { slug: "home", path: "/", ready: "같은 예산, 다른 결과" },
  { slug: "app", path: "/app", ready: /도시|지도/ },
  { slug: "evidence", path: "/evidence", ready: "공식 기준과 같은 예산으로 비교했습니다" },
  { slug: "briefing", path: "/briefing", ready: "행동 채널" },
  { slug: "field", path: "/field", ready: "관제 패널" },
  { slug: "silver", path: "/silver", ready: "실버존을 다시 긋습니다" },
  { slug: "demo", path: "/demo", ready: "핵심 비교" },
  { slug: "aed", path: "/aed", ready: "24시간 전환 후보" },
  { slug: "citizen", path: "/citizen", ready: "우리 동네" },
  { slug: "data", path: "/data", ready: "데이터" },
];
/** FRAMING=walk|combined names the files final-<framing>-<page>-<width>.png; the matching vite server is chosen by baseURL. */
const FRAMING = process.env.FRAMING ?? "combined";
const WIDTHS = [1440, 375] as const;
const TEST_TIMEOUT_MS = 120_000;
const SETTLE_MS = 1200;

for (const width of WIDTHS) {
  for (const p of PAGES) {
    test(`final ${FRAMING} ${p.slug} @${width}`, async ({ page }) => {
      test.setTimeout(TEST_TIMEOUT_MS);
      await page.setViewportSize({ width, height: 900 });
      await page.goto(new URL(p.path, process.env.SHOT_BASE ?? "http://localhost:5190").toString());
      await expect(page.getByText(p.ready).first()).toBeVisible({ timeout: 30_000 });
      if (p.slug === "citizen") {
        const district = page.getByLabel("시군구");
        await expect(district.locator("option").nth(1)).toBeAttached({ timeout: 30_000 });
        await district.selectOption({ label: "관악구" }).catch(() => district.selectOption({ index: 1 }));
        await expect(page.getByRole("heading", { name: /고령자가 건너는 길/ })).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("heading", { name: "쓰러진 분을 보면" })).toBeVisible({ timeout: 30_000 });
        await page.waitForLoadState("networkidle");
      }
      await page.waitForTimeout(SETTLE_MS);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "horizontal overflow px").toBeLessThanOrEqual(1);
      await page.screenshot({ path: `screenshots/final-${FRAMING}-${p.slug}-${width}.png`, fullPage: true });
    });
  }
}
