import { expect, test } from "@playwright/test";
import { stubShell } from "./heatFlow";

/** The real client against hand-written /api/heat responses: error mapping and the empty states. */

test.beforeEach(async ({ page }) => stubShell(page));

test("409 no_completed_run tells the user to run make heat-eval", async ({ page }) => {
  await page.route("**/api/heat/meta", (r) => r.fulfill({ status: 409, json: { detail: "no_completed_run" } }));
  await page.goto("/demo");
  await expect(page.getByRole("alert")).toContainText("make heat-eval");
  await page.goto("/results");
  await expect(page.getByRole("alert")).toContainText("make heat-eval");
});

test("404 on a missing /heat route explains the backend is out of date", async ({ page }) => {
  await page.route("**/api/heat/meta", (r) => r.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.goto("/demo");
  await expect(page.getByRole("alert")).toContainText("백엔드");
});

const META = { years: [2025], variants: ["F", "O"], methods: ["B1", "OURS"], season: "05-20~09-15", sources: [] };
const DAY = { date: "2025-07-23", n_cases: 3, k_budget: 1, b1_captured: 0, ours_captured: 2 };
const REGION = { code: "11010", sido: "서울특별시", name: "종로구", lat: 37.57, lon: 126.98, pop65: 30000 };
const ALERTS = { date: "2025-07-23", k: 1, total_cases: 3, b1: [], ours: [{ code: "11010", score: 0.5, cases: 2 }], cases: [{ code: "11010", n: 2 }] };
const detail = (shelters: unknown) => ({
  region: REGION, cases_today: 2, scores: { b1: 31.2, ours: 0.5, rank: 1 },
  drivers: { tmax: 34.1, hist_rate: 0.12, old_share: 0.31 }, shelters,
});

test("an empty day list shows an empty state, and a failed alerts call can be retried", async ({ page }) => {
  let empty = true;
  await page.route("**/api/heat/meta", (r) => r.fulfill({ json: META }));
  await page.route("**/api/heat/days**", (r) => r.fulfill({ json: empty ? [] : [DAY] }));
  await page.route("**/api/heat/regions", (r) => r.fulfill({ json: [REGION] }));
  await page.route("**/api/heat/eval**", (r) => r.fulfill({ status: 409, json: { detail: "no_completed_run" } }));
  let failAlerts = true; // a flag, not a call count: React StrictMode runs effects twice in dev
  await page.route("**/api/heat/alerts**", (r) =>
    failAlerts ? r.fulfill({ status: 422, json: { detail: "date outside season" } }) : r.fulfill({ json: ALERTS }),
  );
  await page.goto("/demo");
  await expect(page.getByText("날짜 자료가 없습니다")).toBeVisible();

  empty = false;
  await page.getByRole("radio", { name: /O · 관측만/ }).check();
  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "시험 시즌" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "make heat-eval" }).first()).toBeVisible();
  failAlerts = false;
  await page.getByRole("alert").filter({ hasText: "시험 시즌" }).getByRole("button", { name: "다시 시도" }).click();
  await expect(page.getByText("같은 경보 수 K=1")).toBeVisible();
  await expect(page.getByRole("group", { name: /^지도:/ }).getByRole("button")).toHaveCount(1);
});

test("shelter panel: coverage none is not zero, and unknown flags are not closed", async ({ page }) => {
  let coverage = "none";
  await page.route("**/api/heat/meta", (r) => r.fulfill({ json: META }));
  await page.route("**/api/heat/days**", (r) => r.fulfill({ json: [DAY] }));
  await page.route("**/api/heat/regions", (r) => r.fulfill({ json: [REGION] }));
  await page.route("**/api/heat/eval**", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/heat/alerts**", (r) => r.fulfill({ json: ALERTS }));
  await page.route("**/api/heat/region/**", (r) =>
    r.fulfill({
      json: detail(
        coverage === "none"
          ? { coverage: "none", nearby: [] }
          : { coverage: "data", nearby: [{ name: "종로 경로당", dist_km: 0.4, night_open: 1, weekend_open: null }] },
      ),
    }),
  );
  await page.goto("/demo");
  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  const dot = page.getByRole("group", { name: /^지도:/ }).getByRole("button");
  await dot.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("쉼터 좌표 자료 없음.")).toBeVisible();
  await expect(page.getByText("쉼터가 0곳")).toBeVisible();

  coverage = "data";
  await dot.click();
  await page.getByRole("button", { name: "이전" }).click();
  await page.getByRole("group", { name: /^지도:/ }).getByRole("button").click();
  await expect(page.getByText("종로 경로당")).toBeVisible();
  await expect(page.getByText("야간 열림")).toBeVisible();
  await expect(page.getByText("주말 정보 없음")).toBeVisible();
});
