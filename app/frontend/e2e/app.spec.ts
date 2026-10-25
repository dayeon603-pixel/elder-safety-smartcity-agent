import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { stubShell } from "./zpFlow";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const DONGJAK = "/app?sgg=11590&h=23&day=weekday&speed=0.7";
const GWANAK = "/app?sgg=11620&h=14&day=sat";

async function ready(page: Page, url: string): Promise<void> {
  await page.goto(url);
  await expect(page.getByTestId("map-summary")).toContainText("횡단보도");
  await expect(page.getByTestId("queue-cbs")).toBeVisible({ timeout: 90_000 }); // /actions is slow on a cold backend cache
}

async function scan(page: Page): Promise<string[]> {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

test.beforeEach(async ({ page }) => stubShell(page));

for (const width of [1440, 768, 375]) {
  test.describe(`@${width}`, () => {
    test.use({ viewport: { width, height: 1000 } });

    test("operations map: layers, speed, hour and queue, axe clean", async ({ page }) => {
      await ready(page, DONGJAK);
      await expect(page.getByRole("heading", { name: "도시 운영 지도", level: 1 })).toBeVisible();
      await expect(page.getByRole("img", { name: /^동작구 운영 지도/ })).toBeVisible();
      await expect(page.getByTestId("heat-strip")).toContainText("에이전트 순위");
      const queue = page.getByRole("region", { name: "오늘의 인프라 조치" });
      const signal = queue.getByTestId("queue-signal.extend");
      await expect(signal).toContainText("걸음 0.7 m/s 기준");
      await expect(signal.getByTestId("channels")).toContainText("초안 · 발송 안 함");
      await expect(signal.getByRole("button", { name: /웹훅.*준비 중/ })).toBeDisabled();
      const [download] = await Promise.all([page.waitForEvent("download"), signal.getByRole("button", { name: "공문 초안 다운로드" }).click()]);
      expect(download.suggestedFilename()).toMatch(/^signals_11590_.*\.docx$/);
      await expect(queue.getByTestId("queue-cbs")).toContainText(/\d+\/157자/);
      // 근거 opens with numbers and a link to the evidence page
      await signal.getByText("근거", { exact: true }).click();
      await expect(signal).toContainText("판정한 횡단보도");
      await expect(signal.getByRole("link", { name: /근거 화면/ })).toBeVisible();

      // a faster walker: fewer short crosswalks
      const before = await page.getByTestId("map-summary").innerText();
      await page.goto("/app?sgg=11590&h=23&day=weekday&speed=1.2");
      await expect(page.getByTestId("map-summary")).not.toHaveText(before);
      await expect(page.getByTestId("speed-value")).toHaveText("1.20 m/s");

      // hour and day change the open count
      await page.goto("/app?sgg=11590&h=3&day=weekday");
      const night = await page.getByTestId("map-summary").innerText();
      await page.goto("/app?sgg=11590&h=11&day=weekday");
      await expect(page.getByTestId("map-summary")).not.toHaveText(night);

      // layers toggle through the URL
      await page.getByRole("checkbox", { name: "신호가 모자란 횡단보도" }).uncheck();
      await expect(page).toHaveURL(/layers=/);
      await expect(page.locator('[data-layer="crosswalk-short"]')).toHaveCount(0);

      expect(await scan(page)).toEqual([]);
    });

    test("a 시군구 without crosswalk rows says so and does not invent a verdict", async ({ page }) => {
      await page.goto(GWANAK);
      await expect(page.getByTestId("no-crosswalk")).toBeVisible();
      await expect(page.getByTestId("queue-signal.extend")).toContainText("신호 자료가 없어 판정하지 않았습니다");
      expect(await scan(page)).toEqual([]);
    });
  });
}

test("invalid date blocks the map", async ({ page }) => {
  await page.goto("/app?date=2025-13-40");
  await expect(page.getByText("날짜 형식이 올바르지 않아 지도를 만들지 않았습니다.")).toBeVisible();
});

test("who has to act: summary counts come from the action list", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await ready(page, DONGJAK);
  const summary = page.getByTestId("actor-summary");
  await expect(summary).toContainText(/오늘 조치 \d+건 중 \d+건은 고령자가 아무것도 하지 않아도 작동합니다/);
  await expect(page.getByTestId("queue-signal.extend")).toContainText("고령자 조작 불필요");
  await expect(page.getByTestId("queue-cbs")).toContainText("휴대폰 기본 수신");
});
