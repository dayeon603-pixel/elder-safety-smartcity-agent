import { expect, test } from "@playwright/test";
import { stubShell, switchToPohang, walkAed, walkBriefingDefault, walkSilver } from "./zpFlow";

test.beforeEach(async ({ page }) => stubShell(page));

test("briefing: 관악구 default, then 포항시 남구 shows 서울만 분석", async ({ page }) => {
  await walkBriefingDefault(page);
  await expect(page.getByText(/모의 응답으로 표시 중/)).toBeVisible();
  await switchToPohang(page);
  await expect(page.getByRole("list", { name: "낮과 밤에 열린 AED 비율" })).toHaveCount(0);
});

test("briefing: every action has a reason, and an invalid date blocks the request", async ({ page }) => {
  await walkBriefingDefault(page);
  const list = page.getByRole("region", { name: "오늘의 행동" }).getByRole("listitem");
  const n = await list.count();
  if (n > 0) for (let i = 0; i < n; i++) await expect(list.nth(i)).toContainText("이유:");
  await page.goto("/briefing?date=2025-13-40");
  await expect(page.getByText("날짜 형식이 올바르지 않아 브리핑을 만들지 않았습니다.")).toBeVisible();
});

test("briefing: selection lives in the URL", async ({ page }) => {
  await walkBriefingDefault(page);
  await page.getByLabel("방문팀 수").fill("12");
  await expect(page).toHaveURL(/teams=12/);
  await page.reload();
  await expect(page.getByLabel("방문팀 수")).toHaveValue("12");
});

test("silver: headline, baseline-first bars, map, closed Q&A", async ({ page }) => {
  await walkSilver(page);
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toContainText("15%");
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toContainText("89%");
});

test("module pages carry the topic badges", async ({ page }) => {
  for (const [path, tag] of [["/silver", "AI 최적화"], ["/aed", "AI 예측"], ["/briefing", "공공데이터 연동"]] as const) {
    await page.goto(path);
    await expect(page.getByRole("list", { name: "사용한 기술" }).first().getByText(tag)).toBeVisible();
  }
});

test("silver: map scenario switch changes the covered count", async ({ page }) => {
  await page.goto("/silver");
  const live = page.locator("p[aria-live=polite]");
  await expect(live).toContainText("에이전트 추천 기준");
  await page.getByRole("radio", { name: "현행 구역 기준" }).check();
  await expect(live).toContainText("현행 구역 기준");
});

test("aed: night-gap chart, ranking selects the candidates table", async ({ page }) => {
  await walkAed(page);
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toContainText("49%");
});

test("home: problem strip, module cards and the briefing CTA", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "고령자 안전 스마트도시 에이전트", level: 1 })).toBeVisible();
  await expect(page.getByText("도시의 길과 더위와 응급을 한 지도에 올리고").first()).toBeVisible();
  await expect(page.getByText("AI와 디지털 기술로 해결하는 우리 주변의 생활문제")).toBeVisible();
  await expect(page.getByRole("heading", { name: "네 부분으로 이루어져 있습니다" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "어떻게 만들었나" })).toBeVisible();
  for (const col of ["생활문제", "디지털 기술", "AI"]) await expect(page.getByRole("heading", { name: col, level: 3 })).toBeVisible();
  for (const part of ["도시 운영 지도", "현장 에이전트", "에이전트 분석", "시민 화면"]) await expect(page.getByRole("heading", { name: part, level: 3 })).toBeVisible();
  await expect(page.getByText("실버존은 시설 둘레에 그어져 있습니다")).toBeVisible();
  const cards = page.getByRole("heading", { name: "같은 예산, 다른 결과" });
  await expect(cards).toBeVisible();
  await expect(page.getByRole("heading", { name: "폭염 환자 포착률", level: 3 })).toBeVisible();
  await page.getByRole("link", { name: "브리핑 열기" }).click();
  await expect(page).toHaveURL(/\/briefing/);
});

test("nav lists the eight destinations in order", async ({ page }) => {
  await page.goto("/");
  const labels = await page.getByRole("navigation", { name: "주 메뉴" }).getByRole("link").allInnerTexts();
  const want = ["개요", "도시 운영 지도", "현장 에이전트", "담당자 브리핑", "근거", "시민 화면", "데이터"];
  // Other agents may add items (e.g. an extra demo); ours must all be present, in this order.
  expect(labels.map((l) => l.trim()).filter((l) => want.includes(l))).toEqual(want);
});

test("print media hides the controls and keeps the panels", async ({ page }) => {
  await walkBriefingDefault(page);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("form", { name: "브리핑 조건" })).toBeHidden();
  await expect(page.getByRole("navigation", { name: "주 메뉴" })).toBeHidden();
  await expect(page.getByRole("heading", { name: "오늘의 행동", level: 2 })).toBeVisible();
});

/** Matches /api/feedback only, not the dev server's /src/api/feedback.ts module. */
const FEEDBACK_URL = /\/api\/feedback(\/summary)?(\?.*)?$/;

test("feedback: disabled with 준비 중 when the route is missing, records only after the server accepts", async ({ page }) => {
  await page.route(FEEDBACK_URL, (r) => r.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.goto("/briefing");
  const group = page.getByRole("group", { name: "담당자 판단" }).first();
  await expect(group.getByRole("button", { name: "채택" })).toBeDisabled();
  await expect(page.getByText(/준비 중/).first()).toBeVisible();
  await expect(page.getByText("현장 판단 기록됨")).toHaveCount(0);

  const posted: unknown[] = [];
  await page.unroute(FEEDBACK_URL);
  await page.route(FEEDBACK_URL, async (r) => {
    if (r.request().method() === "POST") {
      posted.push(r.request().postDataJSON());
      return r.fulfill({ status: 201, json: { ok: true } });
    }
    return r.fulfill({ json: { by_decision: { 채택: 3, 보류: 1, 부적합: 0 } } });
  });
  await page.reload();
  const live = page.getByRole("group", { name: "담당자 판단" }).first();
  await expect(live.getByRole("button", { name: "채택" })).toBeEnabled();
  await expect(page.getByTestId("feedback-summary")).toContainText("채택 3");
  await live.getByRole("button", { name: "채택" }).click();
  await expect(page.getByText("현장 판단 기록됨 · 다음 학습에 반영 예정").first()).toBeVisible();
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({ decision: "채택", action_id: expect.stringMatching(/^[a-z]+\.\d+$/), sgg_code: expect.stringMatching(/^\d{5}$/) });
});
