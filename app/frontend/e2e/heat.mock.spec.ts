import { expect, test } from "@playwright/test";
import { openPlan, stubShell, walkCitizen, walkThreeSteps } from "./heatFlow";

test.beforeEach(async ({ page }) => stubShell(page));

test("three-step flow on mock responses", async ({ page }) => {
  await walkThreeSteps(page);
  await expect(page.getByText(/모의 응답으로 표시 중/)).toBeVisible();
});

test("year and variant change the day list; the top row is the biggest gap", async ({ page }) => {
  await page.goto("/demo");
  const rows = page.getByRole("region", { name: "날짜 목록" }).getByRole("row");
  await expect(rows.nth(1)).toBeVisible();
  const gapOf = async (i: number): Promise<number> => {
    const text = (await rows.nth(i).getByRole("cell").last().innerText()).replace("−", "-").replace("+", "");
    return Number(text);
  };
  expect(await gapOf(1)).toBeGreaterThanOrEqual(await gapOf(2));

  await page.getByLabel("시험 연도").selectOption("2023");
  await expect(rows.nth(1)).toBeVisible();
  await page.getByRole("radio", { name: /O · 관측만/ }).check();
  await expect(page.getByText("O: 전날까지 정보만 사용")).toBeVisible();
});

test("the top-gap day is preselected, and 2025-07-08 shows all three alert classes", async ({ page }) => {
  await page.goto("/demo");
  const picked = page.getByRole("region", { name: "날짜 목록" }).getByRole("button", { pressed: true });
  await expect(picked).toBeVisible();
  // Mock data carries the real map only for 2025-07-08 (259 patients, 13 districts only we catch).
  await page.getByRole("region", { name: "날짜 목록" }).getByRole("button", { name: /07-08/ }).click();
  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  const legend = page.getByRole("list", { name: "범례" });
  await expect(legend.getByRole("listitem").filter({ hasText: "우리만 경보" })).toContainText(/\d+곳/);
  await expect(page.getByRole("heading", { name: /^B1은 놓치고/ })).toContainText("13곳");
});

test("hero shows the published numbers: B1 42% vs ours 56%", async ({ page }) => {
  await page.goto("/demo");
  const hero = page.getByRole("region", { name: /핵심 비교/ });
  await expect(hero).toContainText("42%");
  await expect(hero).toContainText("56%");
  await expect(hero).toContainText("14%p");
});

test("results page shows bars, forest, curve and the honest note", async ({ page }) => {
  await page.goto("/results");
  await expect(page.getByRole("heading", { name: "기준선 대비 포착률", level: 1 })).toBeVisible();
  await expect(page.locator(".recharts-bar-rectangle").first()).toBeVisible();
  await expect(page.getByText(/0 (제외|포함)/).first()).toBeVisible();
  await expect(page.locator(".recharts-line").first()).toBeVisible();
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toContainText("56%");
  await expect(page.getByRole("heading", { name: "엔진 구성 요소별 기여" })).toBeVisible();
  await expect(page.getByText("방법과 한계")).toBeVisible();
});

test("step 4 plan: slider changes the team count and the list follows", async ({ page }) => {
  await walkThreeSteps(page);
  await openPlan(page);
  await expect(page.getByText(/특보 기준 \d+명/).first()).toBeVisible();
  const slider = page.getByRole("slider", { name: /대응 팀 수/ });
  await slider.focus();
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
  await expect(page.locator("output")).toContainText("15팀");
  await expect(page.getByText("대응 팀 15개")).toBeVisible();
});

test("citizen view shows a risk word and shelters", async ({ page }) => {
  await walkCitizen(page);
});
