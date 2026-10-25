import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/** Footer/ApiStatus call endpoints unrelated to the 에이전트 pages; stub them so runs are deterministic. */
export async function stubShell(page: Page): Promise<void> {
  await page.route("**/api/info", (r) => r.fulfill({ json: { version: "test", schema_version: 3, datasets: [], last_run: null } }));
  await page.route("**/api/datasets", (r) => r.fulfill({ json: [] }));
}

export const DEFAULT_DATE = "2025-07-22";

/** /briefing with defaults: 관악구, 2025-07-22, three panels and a numbered action list. */
export async function walkBriefingDefault(page: Page): Promise<void> {
  await page.goto("/briefing");
  await expect(page.getByRole("heading", { name: "오늘의 에이전트 브리핑", level: 1 })).toBeVisible();
  await expect(page.getByLabel("시도")).toHaveValue("서울특별시");
  await expect(page.getByLabel("시군구").locator("option:checked")).toHaveText("관악구");
  await expect(page.getByLabel("날짜")).toHaveValue(DEFAULT_DATE);
  for (const name of ["길", "폭염", "AED 밤 공백", "오늘의 행동"]) {
    await expect(page.getByRole("heading", { name, level: 2 })).toBeVisible();
  }
  await expect(page.getByText("우리 모형 위험 순위")).toBeVisible();
  await expect(page.getByText("현행 실버존 → 에이전트 추천")).toBeVisible();
  await expect(page.getByRole("list", { name: "낮과 밤에 열린 AED 비율" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^24시간 전환 후보/ })).toBeVisible();
  await expect(page.getByText("AI는 추천, 결정은 담당자")).toBeVisible();
  const why = page.getByText("왜?").first();
  await why.click();
  await expect(page.getByText(/^출처:/).first()).toBeVisible();
  const decide = page.getByRole("group", { name: "담당자 판단" }).first();
  await expect(decide.getByRole("button", { name: "채택" })).toBeVisible();
  const channels = page.getByRole("region", { name: "행동 채널" });
  await expect(channels).toBeVisible();
  await expect(channels).toContainText("권고 초안 · 발송 안 함");
  await expect(channels.getByRole("heading", { name: "재난문자 초안", level: 3 })).toBeVisible();
  await expect(channels).toContainText(/\d+\/157자/);
  await expect(channels.getByRole("heading", { name: "방문팀 동선", level: 3 })).toBeVisible();
}

/** Switch to 경북 포항시 남구: Seoul-only AED panel must say so, the other two panels stay. */
export async function switchToPohang(page: Page): Promise<void> {
  await page.getByLabel("시도").selectOption("경상북도");
  const sgg = page.getByLabel("시군구");
  const option = sgg.locator("option", { hasText: /포항시\s?남구/ });
  await sgg.selectOption({ value: (await option.getAttribute("value")) ?? "" });
  await expect(page.getByText("서울만 분석했습니다.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "폭염", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "길", level: 2 })).toBeVisible();
}

export async function walkSilver(page: Page): Promise<void> {
  await page.goto("/silver");
  await expect(page.getByRole("heading", { name: "실버존을 다시 긋습니다", level: 1 })).toBeVisible();
  const hero = page.getByRole("region", { name: /핵심 비교/ });
  await expect(hero).toContainText("현행 노인보호구역");
  await expect(hero).toContainText("에이전트 분석");
  await expect(hero).toContainText(/신규 다발지/);
  const bars = page.getByRole("list", { name: "방법별 커버율" });
  const labels = await bars.getByRole("listitem").locator("span").first().allInnerTexts();
  expect(labels.length).toBeGreaterThan(0);
  const items = bars.getByRole("listitem");
  await expect(items.first()).toContainText("현행 노인보호구역");
  await expect(items.last()).toContainText("에이전트 분석");
  await expect(page.getByRole("img", { name: /^서울 지도/ })).toBeVisible();
  // Q&A section starts closed.
  const toggle = page.getByRole("button", { name: /방법과 한계/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
}

export async function walkAed(page: Page): Promise<void> {
  await page.goto("/aed");
  await expect(page.getByRole("heading", { name: /밤에도 열려 있는 AED/, level: 1 })).toBeVisible();
  await expect(page.getByText("서울 2024 · 시군구×시간 단위 분석")).toBeVisible();
  await expect(page.getByRole("region", { name: /핵심 비교/ })).toContainText("밤에 열린 AED");
  await expect(page.locator(".recharts-bar-rectangle").first()).toBeVisible();
  const ranking = page.getByRole("list", { name: "밤에 열린 AED 비율이 낮은 구" });
  const first = ranking.getByRole("button").first();
  await expect(first).toHaveAttribute("aria-pressed", "true");
  const gu = (await first.innerText()).split("\n").map((t) => t.trim()).find((t) => /구$/.test(t)) ?? "";
  await expect(page.getByRole("heading", { name: `${gu} 24시간 전환 후보`, level: 2 })).toBeVisible();
  await expect(page.getByRole("table", { name: `${gu} 24시간 전환 후보` })).toBeVisible();
  const second = ranking.getByRole("button").nth(1);
  await second.click();
  await expect(second).toHaveAttribute("aria-pressed", "true");
}
