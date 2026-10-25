import { expect, test } from "@playwright/test";
import { openPlan, walkCitizen, walkThreeSteps } from "./heatFlow";

/** The demo against the real backend (dev proxy). Skipped until /api/heat/meta answers 200 with test years. */
test.beforeEach(async ({ request }) => {
  const res = await request.get("/api/heat/meta");
  const years = res.ok() ? ((await res.json()) as { years: number[] }).years : [];
  test.skip(years.length === 0, `real /api/heat/meta not ready (HTTP ${res.status()}); run make heat-eval and restart the backend`);
});

test("three-step flow on the real API", async ({ page }) => {
  await walkThreeSteps(page);
});

test("the largest-gap day is preselected and the hero compares the rule with ours", async ({ page }) => {
  await page.goto("/demo");
  const list = page.getByRole("region", { name: "날짜 목록" });
  const picked = list.getByRole("button", { pressed: true });
  await expect(picked).toHaveCount(1);
  await expect(list.getByRole("button").first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("차이 최대")).toBeVisible();
  const hero = page.getByRole("region", { name: /핵심 비교/ });
  await expect(hero).toContainText("특보 규칙 (B1)");
  await expect(hero).toContainText("우리 모형");
  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  await expect(page.getByText(/같은 경보 수 K=\d+/)).toBeVisible();
  const first = page.getByRole("list", { name: "시군구 목록" }).getByRole("button").first();
  const name = (await first.locator("span").first().innerText()).trim();
  await first.click();
  await expect(page.getByRole("heading", { name: "시군구 근거", level: 2 })).toBeVisible();
  await expect(page.locator("section[aria-labelledby=rp-title]")).toContainText(name);
});

test("results page renders hero, decomposition, forest and curve from the API", async ({ page }) => {
  await page.goto("/results");
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "엔진 구성 요소별 기여" })).toBeVisible();
  await expect(page.locator(".recharts-bar-rectangle").first()).toBeVisible();
  await expect(page.locator(".recharts-line").first()).toBeVisible();
  await expect(page.getByText("방법과 한계")).toBeVisible();
});

test("step 4 plan from the real /heat/plan", async ({ page }) => {
  await walkThreeSteps(page);
  await openPlan(page);
  await expect(page.getByText(/특보 기준 \d+명/).first()).toBeVisible();
  await expect(page.getByText(/우리 \d+명/).first()).toBeVisible();
});

test("citizen view on the real API", async ({ page }) => {
  await walkCitizen(page);
});

test("home shows the three API-driven module results and the build columns", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "같은 예산, 다른 결과" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "어떻게 만들었나" })).toBeVisible();
  for (const name of ["실버존 커버율", "폭염 환자 포착률", "낙상 감지 지연(중앙값)"]) await expect(page.getByRole("heading", { name, level: 3 })).toBeVisible();
  await expect(page.getByText("breast")).toHaveCount(0);
});

test("footer shows curated credits and one link to the full list, never raw dataset keys", async ({ page }) => {
  await page.goto("/");
  const footer = page.getByRole("contentinfo");
  await expect(footer.getByRole("link", { name: "데이터 출처 전체 보기 →" })).toBeVisible();
  await expect(footer.getByRole("link", { name: /Meteostat/ })).toBeVisible();
  await expect(footer).not.toContainText("heat_shelters");
  await expect(footer).not.toContainText("machine-verified");
  await expect(footer).not.toContainText("wdbc");
});

test("the sticky header stays at the top of the viewport while scrolling (full-page shots only drew it mid-page)", async ({ page }) => {
  await page.goto("/demo");
  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  await expect(page.getByRole("group", { name: /^지도:/ })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 700));
  const top = await page.locator("header").first().evaluate((el) => Math.round(el.getBoundingClientRect().top));
  expect(top).toBe(0);
  await page.screenshot({ path: "screenshots/heat-sticky-header-check.png" });
});
