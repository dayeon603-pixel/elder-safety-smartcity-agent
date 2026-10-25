import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/** Footer/ApiStatus call endpoints that are unrelated to the heat flow; stub them so runs are deterministic. */
export async function stubShell(page: Page): Promise<void> {
  await page.route("**/api/info", (r) => r.fulfill({ json: { version: "test", schema_version: 3, datasets: [], last_run: null } }));
  await page.route("**/api/datasets", (r) => r.fulfill({ json: [] }));
}

/**
 * The demo, end to end: pick a day (step 1), read the map (step 2), open a district (step 3).
 * Works against mocks and against the real API because it asserts structure, not numbers.
 */
export async function walkThreeSteps(page: Page): Promise<void> {
  await page.goto("/demo");
  await expect(page.getByRole("heading", { name: "날짜 고르기", level: 2 })).toBeVisible();
  const list = page.getByRole("region", { name: "날짜 목록" });
  await expect(list.getByRole("button").first()).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "다음: 지도 보기" }).click();
  await expect(page.getByRole("heading", { name: "경보 지도", level: 2 })).toBeVisible();
  await expect(page.getByText(/같은 경보 수 K=\d+/)).toBeVisible();
  const legend = page.getByRole("list", { name: "범례" });
  for (const label of ["B1만 경보", "우리만 경보", "둘 다 경보", "경보 없음, 환자 발생"]) {
    await expect(legend.getByText(label)).toBeVisible();
  }
  await expect(page.getByText("우리 모형", { exact: true }).first()).toBeVisible();

  const map = page.getByRole("group", { name: /^지도:/ });
  const dot = map.getByRole("button").first();
  await expect(dot).toBeVisible();
  await dot.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "시군구 근거", level: 2 })).toBeVisible();
  const panel = page.locator("section[aria-labelledby=rp-title]");
  await expect(panel.getByRole("heading", { name: "점수의 근거" })).toBeVisible();
  await expect(panel.getByText("최고기온")).toBeVisible();
  await expect(panel.getByRole("heading", { name: "가까운 무더위쉼터" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "진행 단계" }).locator("[aria-current=step]")).toContainText("시군구 근거");
}

/** Step 4 from wherever the flow currently is (steps 2 or 3 show the button). */
export async function openPlan(page: Page): Promise<void> {
  await page.getByRole("button", { name: "다음: 오늘의 대응안" }).click();
  await expect(page.getByRole("heading", { name: "오늘의 대응안", level: 2 })).toBeVisible();
  await expect(page.getByText("같은 인력으로 만난 환자").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "팀 배치" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "운영 연장을 권하는 쉼터" })).toBeVisible();
}

/** Citizen view: pick a district, get a risk word (text, not colour alone) and the shelter section. */
export async function walkCitizen(page: Page): Promise<void> {
  await page.goto("/citizen");
  await expect(page.getByRole("heading", { name: "우리 동네 생활 안전", level: 1 })).toBeVisible();
  const district = page.getByLabel("시군구");
  await expect(district.locator("option").nth(1)).toBeAttached();
  await district.selectOption({ index: 1 });
  const risk = page.locator("section[aria-labelledby=risk-title]");
  await expect(risk).toContainText(/높음|주의|낮음/);
  await expect(page.getByRole("heading", { name: "가까운 무더위쉼터" })).toBeVisible();
}
