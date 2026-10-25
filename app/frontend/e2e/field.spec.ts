import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubField } from "./fieldStubs";

/** Camera/webcam path is deliberately not exercised here (no device in CI). */

test("banner, pipeline, and heat stage from the API", async ({ page }) => {
  await stubField(page);
  await page.goto("/field");
  await expect(page.getByText("시뮬레이션입니다 · 실측 성능이 아닙니다 · 입력값은 실제 공공데이터")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "처리 흐름" }).getByRole("listitem")).toHaveCount(7);
  await expect(page.getByText(/일 최고기온 35\.2℃ → 폭염경보 수준/)).toBeVisible();
  await expect(page.getByText("영상은 브라우저 안에서만 처리 · 저장·전송 없음")).toBeVisible();
  await expect(page.getByTestId("fall-evidence")).toHaveText("낙상 감지율 규칙 27% → AI 93%, 감지 지연 2.25초 → 0.70초 (UR Fall 공개 데이터, 교차검증)");
  await expect(page.getByText("우리 동네 횡단보도에서, AI와 디지털 기술로")).toBeVisible();
});

test("the camera permission is not requested until the button is pressed", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __gum: number }).__gum = 0;
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: () => { (window as unknown as { __gum: number }).__gum += 1; return Promise.reject(new DOMException("denied", "NotAllowedError")); } },
      configurable: true,
    });
  });
  await stubField(page);
  await page.goto("/field");
  await expect(page.getByRole("button", { name: "카메라 켜기" })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __gum: number }).__gum)).toBe(0);
  await page.getByRole("button", { name: "카메라 켜기" }).click();
  await expect(page.getByText(/카메라 권한이 거부되었습니다/)).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __gum: number }).__gum)).toBe(1);
});

test("scenario A: deficit shows arithmetic, VMS text and the extension card", async ({ page }) => {
  await stubField(page);
  await page.goto("/field");
  const option = await page.getByLabel("횡단보도", { exact: true }).locator("option", { hasText: "25.0 m" }).first().getAttribute("value");
  await page.getByLabel("횡단보도", { exact: true }).selectOption(option);
  await page.getByRole("button", { name: "시작", exact: true }).click();
  await expect(page.getByText("= 25.0 m ÷ 0.70 m/s = 35.7 초").or(page.getByText(/25\.0 m ÷ 0\.70 m\/s = 35\.7 초/))).toBeVisible();
  await expect(page.getByText("부족 = 35.7 − 27.0 = 8.7 초")).toBeVisible();
  const scenario = page.locator("#scenario-a");
  await expect(scenario.getByTestId("vms-text")).toHaveText("⚠ 폭염 · 고령 보행자 횡단 중 · 서행");
  await expect(scenario.getByText("신호 연장 +9초 권고")).toBeVisible();
  await scenario.getByRole("button", { name: "일시정지" }).click().catch(() => undefined);
  await scenario.getByLabel(/경과 시간/).fill("33");
  await expect(scenario.getByText("도로 위에 남음")).toBeVisible();
  await scenario.getByRole("button", { name: "처음으로" }).click();
  await expect(scenario.getByText("시작을 누르면 엔진이 판정합니다.").first()).toBeVisible();
});

test("scenario A: a slow-enough crossing at a short crosswalk shows no warning", async ({ page }) => {
  await stubField(page);
  await page.goto("/field");
  await page.locator("#scenario-a").getByRole("button", { name: "시작", exact: true }).click();
  await expect(page.locator("#scenario-a").getByTestId("vms-text")).toHaveText("표시 없음");
});

test("scenario B: button event runs the workflow, measures latency, and sends nothing", async ({ page }) => {
  await stubField(page, "missing");
  await page.goto("/field");
  await page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" }).click();
  await expect(page.getByTestId("alert-stamp")).toHaveText("에이전트 긴급 대응");
  await expect(page.getByTestId("latency")).toContainText(/감지→경보 생성 \d+\.\d ms/);
  await expect(page.getByTestId("speech-text")).toContainText("근처에 쓰러진 분이 있습니다. 119에 신고하고,");
  await expect(page.getByText("가장 가까운 AED 검색")).toBeVisible();
  await expect(page.getByText("CAP 1.2 경보 생성")).toBeVisible();
  await expect(page.getByText("FHIR R4 Bundle 생성")).toBeVisible();
  await expect(page.getByText("생성됨 · 전송 안 함(훈련)").first()).toBeVisible();
  await page.getByText(/^CAP 1\.2 XML/).click();
  await expect(page.locator("pre", { hasText: "<status>Exercise</status>" })).toBeVisible();
  await expect(page.getByText(/에이전트 서버를 쓸 수 없어/)).toBeVisible();
  await expect(page.getByText(/SUCCESS|SENT|200 OK/)).toHaveCount(0);
  await page.getByRole("button", { name: "경보 해제(시뮬레이션)" }).click();
  await expect(page.getByTestId("alert-stamp")).toHaveCount(0);
});

test("scenario B: agent result renders with truthful receipt wording", async ({ page }) => {
  await stubField(page, "ok");
  await page.goto("/field");
  await page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" }).click();
  await expect(page.getByTestId("alert-stamp")).toBeVisible();
  await expect(page.getByTestId("agent-planning")).toContainText("에이전트 계획 중");
  const block = page.getByRole("region", { name: "로컬 에이전트 결과" });
  await expect(block.getByText("stub-model")).toBeVisible();
  await expect(block.getByRole("link", { name: "에이전트 기록" })).toHaveAttribute("href", "/api/agent/runs/run-0123456789ab");
  await expect(block.getByText("숫자 검증 통과")).toBeVisible();
  await expect(block.getByText(/119 시뮬레이터 수신/)).toContainText("202");
  await expect(block.getByText("참고용")).toBeVisible();
});

test("VLM toggle is off by default; presentation mode hides dev details", async ({ page }) => {
  await stubField(page);
  await page.goto("/field");
  await expect(page.getByLabel(/장면 설명\(VLM\) 사용/)).not.toBeChecked();
  await expect(page.getByText(/전국횡단보도표준데이터 \(/)).toBeVisible();
  await page.getByRole("button", { name: "발표 모드 켜기" }).click();
  await expect(page.getByText(/전국횡단보도표준데이터 \(/)).toBeHidden();
  await expect(page.getByRole("button", { name: "발표 모드 끄기" })).toHaveAttribute("aria-pressed", "true");
});

for (const width of [1440, 375]) {
  test(`axe @${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await stubField(page, "ok");
    await page.goto("/field");
    await expect(page.getByRole("button", { name: "카메라 켜기" })).toBeVisible();
    await page.getByRole("button", { name: "쓰러짐 이벤트 발생(시뮬레이션)" }).click();
    await expect(page.getByTestId("alert-stamp")).toBeVisible();
    await page.waitForLoadState("networkidle");
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
  });
}
