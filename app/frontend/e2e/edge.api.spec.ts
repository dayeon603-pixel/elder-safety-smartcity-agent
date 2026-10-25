import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { stubShell } from "./heatFlow";
import { stubField } from "./fieldStubs";

/**
 * Edge audit (no backend): the real client against aborted / 429 / 422 / 503 responses and empty data.
 * Runs with playwright.edge.config.ts (plain vite, no mock flag) so the production error mapping is exercised.
 */

const REGIONS = [
  { code: "11620", sido: "서울특별시", name: "관악구", lat: 37.4784, lon: 126.9516, pop65: 70000 },
  { code: "42150", sido: "강원특별자치도", name: "강릉시", lat: 37.7519, lon: 128.8761, pop65: 50000 },
];
const META = { years: [2024, 2025], variants: ["F", "O"], methods: ["B1", "OURS"], season: { start: "05-20", end: "09-25" }, sources: [] };

async function stubRegions(page: Page): Promise<void> {
  await stubShell(page);
  await page.route("**/api/heat/meta", (r) => r.fulfill({ json: META }));
  await page.route("**/api/heat/regions", (r) => r.fulfill({ json: REGIONS }));
  await page.route(/\/api\/feedback(\/summary)?(\?.*)?$/, (r) => r.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.route("**/api/silver/**", (r) => r.fulfill({ status: 409, json: { detail: { code: "no_module_data", message: "" } } }));
}

test("API unreachable: briefing shows a connection error with retry, never a blank page", async ({ page }) => {
  await stubRegions(page);
  await page.route("**/api/briefing/**", (r) => r.abort("connectionrefused"));
  await page.route("**/api/actions/**", (r) => r.abort("connectionrefused"));
  await page.goto("/briefing");
  const alert = page.getByRole("alert").filter({ hasText: "브리핑을 만들지 못했습니다" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("서버에 연결할 수 없습니다");
  await expect(alert.getByRole("button", { name: "다시 시도" })).toBeVisible();
  const body = await page.locator("main").innerText();
  expect(body).not.toMatch(/\b(NaN|undefined|null)\b/);
});

test("429 with Retry-After is turned into a wait instruction in seconds", async ({ page }) => {
  await stubRegions(page);
  await page.route("**/api/briefing/**", (r) => r.fulfill({ status: 429, headers: { "Retry-After": "12" }, json: { detail: "rate limited" } }));
  await page.route("**/api/actions/**", (r) => r.fulfill({ status: 429, headers: { "Retry-After": "12" }, json: { detail: "rate limited" } }));
  await page.goto("/briefing");
  await expect(page.getByRole("alert").filter({ hasText: "브리핑을 만들지 못했습니다" })).toContainText("12초 후 다시 시도");
});

test("season edge: the backend's 422 for 05-19 is explained as outside the test season", async ({ page }) => {
  await stubRegions(page);
  await page.route("**/api/briefing/**", (r) => r.fulfill({ status: 422, json: { detail: "date is outside the heat season (05-20..09-25)" } }));
  await page.route("**/api/actions/**", (r) => r.fulfill({ status: 422, json: { detail: "date is outside the heat season (05-20..09-25)" } }));
  await page.goto("/briefing?date=2025-05-19");
  await expect(page.getByRole("alert").filter({ hasText: "브리핑을 만들지 못했습니다" })).toContainText("시험 시즌");
  // an impossible calendar date never reaches the API
  let called = false;
  await page.route("**/api/briefing/**", (r) => { called = true; return r.fulfill({ status: 500 }); });
  await page.goto("/briefing?date=2025-02-30");
  await expect(page.getByText("날짜 형식이 올바르지 않아 브리핑을 만들지 않았습니다.")).toBeVisible();
  expect(called).toBe(false);
});

async function stubEmptyOps(page: Page): Promise<void> {
  await stubRegions(page);
  await page.route("**/api/briefing/**", (r) => r.fulfill({ status: 503, json: { detail: { code: "data_unavailable", message: "x" } } }));
  await page.route("**/api/actions/**", (r) => r.fulfill({ status: 503, json: { detail: { code: "data_unavailable", message: "x" } } }));
  await page.route("**/ops/index.json", (r) => r.fulfill({ json: { meta: { generated_at: "t", sources: [], note: "" }, sidos: [{ sido: "서울특별시", file: "07.json", regions: { 11620: [0, 0, 0] } }] } }));
  await page.route("**/ops/07.json", (r) => r.fulfill({ json: { regions: {} } }));
}

test("ops map: a region with no crosswalk / AED / shelter rows says so and both module errors are visible", async ({ page }) => {
  await stubEmptyOps(page);
  await page.goto("/app?sgg=11620&h=23&day=weekday");
  await expect(page.getByTestId("hour-value")).toHaveText("23시");
  await expect(page.getByTestId("no-crosswalk")).toBeVisible();
  await expect(page.getByTestId("no-shelter")).toBeVisible();
  await expect(page.getByText("AED: 이 시군구의 개방시간 자료가 없습니다.")).toBeVisible();
  await expect(page.getByTestId("map-summary")).toContainText("AED 0대 중 0대 열림");
  // the two module failures are shown as errors, and the queue still has the not-evaluated signal row and the CAP link
  await expect(page.getByText(/브리핑을 불러오지 못했습니다/)).toBeVisible();
  await expect(page.getByTestId("queue-signal.extend")).toContainText("자료가 없어 판정하지 않았습니다");
  await expect(page.getByTestId("queue-cap.feed")).toBeVisible();
  const body = await page.locator("main").innerText();
  expect(body).not.toMatch(/\b(NaN|undefined|null)\b/);
});

test("ops map: the hour control wraps 23 -> 0, rejects 24, and the holiday column can be forced", async ({ page }) => {
  test.setTimeout(90_000); // three navigations of the heaviest page
  await stubEmptyOps(page);
  await page.goto("/app?sgg=11620&h=0&day=weekday");
  await expect(page.getByTestId("hour-value")).toHaveText("00시");
  await page.goto("/app?sgg=11620&h=24&day=weekday");
  await expect(page.getByTestId("hour-value")).not.toHaveText("24시"); // out of range falls back to the current KST hour
  await page.goto("/app?sgg=11620&day=hol");
  await expect(page.getByRole("radio", { name: "공휴일" })).toBeChecked();
});

test("ops map: the heat strip says '평가 대상 아님' when the model never scored the day", async ({ page }) => {
  await stubRegions(page);
  await page.route("**/ops/index.json", (r) => r.fulfill({ json: { meta: { generated_at: "t", sources: [], note: "" }, sidos: [] } }));
  await page.route("**/api/actions/**", (r) => r.fulfill({ status: 503, json: { detail: { code: "data_unavailable", message: "x" } } }));
  await page.route("**/api/briefing/**", (r) => r.fulfill({ json: {
    date: "2025-05-20", teams: 10, region: { code: "42150", name: "강릉시", sido: "강원특별자치도", pop65: 50000 },
    heat: { rank: null, evidence: {}, alerted_b1: null, alerted_ours: null, cases_actual: 0, plan_allocation_for_region: { teams: 0, teams_total: 10, expected_cases: null, reason: null }, nearby_open_shelters: { coverage: "none", items: [] } },
    silver: { current_zones_in_region: null, our_picks_in_region: null, hotspots_2024_in_region: null, hotspots_covered_current: null, hotspots_covered_ours: null, crosswalk_green_deficit_count: null, note: "서울 지도만 분석" },
    aed: null, aed_note: "서울만 분석",
    actions: [{ action_id: "heat.alert", module: "heat", text: "강릉시는 오늘 경보 대상이 아닙니다.", reason: "이 날 평가 대상 아님.", evidence: null }],
  } }));
  await page.goto("/app?sgg=42150&date=2025-05-20");
  await expect(page.getByTestId("heat-strip")).toContainText("평가 대상 아님");
  await expect(page.getByTestId("heat-strip")).toContainText("이 날은 평가 대상이 아닙니다");
  await expect(page.getByTestId("heat-strip")).not.toContainText("순위");
});

test("aed page: 409 before modules-load tells the operator what to run; empty 구 list is an empty state", async ({ page }) => {
  await stubShell(page);
  await page.route("**/api/aed/summary", (r) => r.fulfill({ status: 409, json: { detail: { code: "no_module_data", message: "" } } }));
  await page.route("**/api/aed/hourly", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/aed/gu", (r) => r.fulfill({ json: [] }));
  await page.goto("/aed");
  await expect(page.getByRole("alert").filter({ hasText: "요약을 불러오지 못했습니다" })).toContainText("make modules-load");
  await expect(page.getByText("구 자료가 없습니다.")).toBeVisible();
});

test("field page: agent 503 busy degrades to the local flow with the status in plain words", async ({ page }) => {
  await stubField(page);
  await page.route("**/api/agent/run", (r) => r.fulfill({ status: 503, headers: { "Retry-After": "5" }, json: { detail: { code: "busy", message: "3 agent runs already in progress" } } }));
  await page.goto("/field");
  const button = page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" });
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByTestId("alert-stamp")).toBeVisible();
  await expect(page.getByText(/에이전트 서버를 쓸 수 없어 이 화면의 로컬 흐름만 실행했습니다/)).toBeVisible();
  await expect(page.getByText(/서버 응답 503/)).toBeVisible();
  await expect(page.getByTestId("latency")).toContainText("감지→경보 생성");
});

test("field page: fall model file missing falls back to the rule baseline and says so", async ({ page }) => {
  await stubField(page);
  await page.route("**/models/fall_model.json", (r) => r.fulfill({ status: 404, body: "" }));
  await page.goto("/field");
  await expect(page.getByRole("alert").filter({ hasText: "AI 낙상 모델을 불러오지 못해 규칙 기준선" })).toBeVisible();
});

test("field page: a station gap (drivers.tmax null) must fall back to manual heat input, not crash the page", async ({ page }) => {
  test.setTimeout(90_000); // first load of /field in dev mode is slow; a timeout would not count as the expected failure
  await stubField(page);
  await page.route("**/api/heat/region/**", (r) => r.fulfill({ json: {
    region: { code: "11590", sido: "서울특별시", name: "동작구", lat: 37.5124, lon: 126.9393, pop: 300000, pop65: 60000 },
    cases_today: 0, scores: { b1: null, ours: 0.4, rank: 30 },
    drivers: { tmax: null, apparent_max: null, station: { id: "108", name: "서울", source: "kma_asos", dist_km: 5.1 }, hist_rate: 0.1, old_share: 0.2 },
    shelters: { coverage: "none", nearby: [] },
  } }));
  await page.goto("/field");
  // desired: the page renders (banner visible) and the heat picker falls back to a labelled manual choice
  await expect(page.getByText("시뮬레이션입니다 · 실측 성능이 아닙니다 · 입력값은 실제 공공데이터")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("alert").filter({ hasText: "화면을 표시하는 중 문제가 생겼습니다" })).toHaveCount(0);
  await expect(page.getByText(/직접 선택|자료 없음/).first()).toBeVisible();
});
