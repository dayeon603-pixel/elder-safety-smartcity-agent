import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const SHOTS = "screenshots";
const SIZES = [{ width: 1920, height: 1080 }, { width: 1440, height: 900 }] as const;

/** TWIN_SIZE=1440 runs the workday flow at 1440x900 and suffixes the screenshots. */
const SMALL = process.env.TWIN_SIZE === "1440";
const SIZE = SMALL ? { width: 1440, height: 900 } : { width: 1920, height: 1080 };
const SUF = SMALL ? "-1440" : "";

async function open(page: Page): Promise<void> {
  await page.goto("/twin");
  await expect(page.getByTestId("building-count")).not.toHaveText("…");
  await expect(page.getByTestId("clock")).toBeVisible();
  await expect(page.getByTestId("action-card").first()).toBeVisible();
}

for (const size of SIZES) {
  test(`no scroll at ${size.width}x${size.height}; building count equals API`, async ({ page, request }) => {
    await page.setViewportSize(size);
    await open(page);
    const m = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, ih: innerHeight, sw: document.documentElement.scrollWidth, iw: innerWidth, bh: document.body.scrollHeight }));
    expect(m.sh).toBeLessThanOrEqual(m.ih);
    expect(m.bh).toBeLessThanOrEqual(m.ih);
    expect(m.sw).toBeLessThanOrEqual(m.iw);
    const api = await request.get("/api/twin/11620/scene?meta=1");
    expect(api.ok()).toBe(true);
    const meta = (await api.json()) as { buildings?: number };
    const apiCount = meta.buildings;
    expect(apiCount).toBeGreaterThan(0);
    await expect(page.getByTestId("building-count")).toHaveText(String(apiCount));
    // 주제도 counts come from /twin/11620/infra
    const infra = (await (await request.get("/api/twin/11620/infra")).json()) as { counts: Record<string, number | null> };
    await expect(page.getByTestId("layer-count-aed")).toHaveText(String(infra.counts.aed));
    await expect(page.getByTestId("layer-count-stop")).toHaveText(String(infra.counts.bus_stop));
    await expect(page.getByTestId("layer-count-signal")).toHaveText(String(infra.counts.signal_crosswalk));
    await expect(page.getByTestId("layer-sub-stop")).toContainText(String(infra.counts.bus_stop_recommended_bit));
    await page.screenshot({ path: `${SHOTS}/twin-${size.width}x${size.height}.png` });
  });
}

test("공무원의 하루: 6 steps, approval chain, hold reason, preview and spec pane, fall confirm", async ({ page }) => {
  await page.setViewportSize(SIZE);
  await open(page);
  await expect(page.getByTestId("ref-date")).toContainText("2025-07-22");

  // step 1: 09:00 briefing
  await page.keyboard.press("1");
  await expect(page.getByTestId("clock")).toContainText("09:00");
  await expect(page.getByTestId("scn-s1")).toHaveAttribute("data-done", "1");
  await page.screenshot({ path: `${SHOTS}/twin-1-brief${SUF}.png` });

  // step 2: 17:00 heat response; plain wording; preview is the default view of zone 3
  await page.keyboard.press("2");
  await expect(page.getByTestId("clock")).toContainText("17:00");
  await expect(page.getByTestId("result-cards")).toContainText("현행 특보 기준");
  await expect(page.getByTestId("result-cards")).toContainText("에이전트 권고");
  await expect(page.getByTestId("effect-chip")).toContainText(/\d+(\.\d+)?%/); // figures come from /twin/effects
  await expect(page.getByTestId("preview")).toContainText("수신");
  await expect(page.getByTestId("pv-to")).not.toContainText("확인 중");
  await expect(page.getByTestId("pv-evidence")).not.toContainText(/true|false|λ/);
  await expect(page.getByTestId("destination").first()).toContainText("전달 대상:");
  // selecting the voice card never shows the 재난문자 letter title (one card, one document)
  await page.getByTestId("select-card").filter({ hasText: "음성 문안" }).click();
  await expect(page.getByTestId("pv-title")).toContainText("음성 문안");
  await expect(page.getByTestId("pv-title")).not.toContainText("재난문자 초안 송출");
  await page.getByTestId("select-card").first().click();

  // adopt -> chip stage 2, tamper-evident record, step 2 done; then 과장 결재(시연) -> stage 3, step 3 done
  await page.getByTestId("adopt").first().click();
  await expect(page.getByTestId("audit-line").first()).toContainText("변조 탐지 기록");
  await expect(page.getByTestId("approval-chip").first()).toHaveAttribute("data-stage", "2");
  await expect(page.getByTestId("scn-s2")).toHaveAttribute("data-done", "1");
  await page.getByTestId("approve").first().click();
  await expect(page.getByTestId("approval-chip").first()).toHaveAttribute("data-stage", "3");
  await expect(page.getByTestId("scn-s3")).toHaveAttribute("data-done", "1");

  // hold with a reason
  await page.getByTestId("hold").nth(1).click();
  await page.getByTestId("hold-reason").fill("현장 확인 후 재검토");
  await page.getByTestId("hold-save").click();
  await expect(page.getByTestId("hold-line")).toContainText("현장 확인 후 재검토");
  await expect(page.getByTestId("tally")).toContainText("오늘 채택 1");
  await expect(page.getByTestId("tally")).toContainText("보류 1");

  // spec pane: CAP with measured ms and the drill annotation
  await page.getByTestId("tab-spec").click();
  await expect(page.getByTestId("doc-body")).toContainText("urn:oasis:names:tc:emergency:cap:1.2");
  await expect(page.getByTestId("terminal")).toContainText(/CAP 1\.2 생성\s+[\d.]+ ms/);
  await expect(page.getByTestId("doc-note")).toContainText("Exercise");
  await page.screenshot({ path: `${SHOTS}/twin-2-heat-spec${SUF}.png` });
  await page.getByTestId("tab-action").click();
  await page.screenshot({ path: `${SHOTS}/twin-2-heat${SUF}.png` });

  // step 3: 17:30, text-only chip
  await page.keyboard.press("3");
  await expect(page.getByTestId("clock")).toContainText("17:30");
  await expect(page.getByTestId("effect-chip")).toContainText("휴대폰 없이도");
  await page.screenshot({ path: `${SHOTS}/twin-3-deliver${SUF}.png` });

  // step 4 (어디에 · 실버존): starts at [현행 기준], flips to [에이전트]; big scoreboard
  await page.keyboard.press("4");
  await expect(page.getByTestId("clock")).toContainText("17:30");
  await expect(page.getByTestId("mode-asis")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("mode-ours")).toHaveAttribute("aria-pressed", "true", { timeout: 6000 });
  await expect(page.getByTestId("scoreboard")).toContainText("현행");
  await expect(page.getByTestId("effect-chip")).toContainText(/\d+곳/);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/twin-4-silver${SUF}.png` });

  // step 5 (무엇을 · 낙상): 17:40 on the same day; fall from the last-resort button here (the clip has its own test)
  await page.keyboard.press("5");
  await expect(page.getByTestId("clock")).toContainText("17:40");
  await page.getByTestId("fall-button-demo").click();
  await expect(page.getByTestId("ref-date")).toContainText("2025-07-22");
  await expect(page.getByTestId("clock")).toContainText("17:40");
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "pending");
  await expect(page.getByTestId("button-demo")).toBeVisible();
  await expect(page.getByTestId("fall-alert-text")).toContainText(/시연 카메라 #\d.*낙상 의심 감지 · 17:40/, { timeout: 15_000 });
  await expect(page.getByTestId("camera-label")).toContainText("시연 영상");
  await expect(page.getByTestId("alert-panel")).toContainText(/가까운 열린 AED · .+ \d+ m/, { timeout: 20_000 });
  await expect(page.getByTestId("state-loading")).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByTestId("action-cards")).toHaveAttribute("data-night", "0");
  await page.screenshot({ path: `${SHOTS}/twin-4a-confirm${SUF}.png` });
  await page.getByTestId("confirm-fall").click();
  await page.getByTestId("tab-spec").click();
  await expect(page.getByTestId("doc-body")).toContainText("Bundle", { timeout: 120_000 });
  await expect(page.getByTestId("doc-note")).toContainText("HTEST");
  await expect(page.locator("[data-mark='1']").first()).toBeVisible();
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/twin-4-fall${SUF}.png` });

  // step 6 (밤 · AED): Zone 2 swaps to AED conversion actions; mode returns to [현행 기준] so the AEDs visibly switch off
  await page.keyboard.press("6");
  await expect(page.getByTestId("clock")).toContainText("23:00");
  await expect(page.getByTestId("mode-asis")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("action-cards")).toHaveAttribute("data-night", "1");
  await expect(page.getByTestId("action-card").first()).toContainText("24시간 개방 요청");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/twin-5-night${SUF}.png` });
  await page.getByTestId("mode-ours").click();
  await expect(page.getByTestId("mode-ours")).toHaveAttribute("aria-pressed", "true");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/twin-5b-ours${SUF}.png` });
  await page.getByTestId("mode-asis").click();

  // 비 오는 밤 (real observed rain day), key 7 only
  await page.keyboard.press("7");
  await expect(page.getByTestId("clock")).toBeVisible();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${SHOTS}/twin-6-rain${SUF}.png` });

  // wording: never claim anything was sent; no internal identifiers anywhere on screen
  const all = await page.locator("[data-testid=twin-root]").innerText();
  expect(all.replace(/발송 안 함/g, "").replace(/영상 저장·전송 안 함/g, "").replace(/승인 흐름:[^\n]*/g, "").replace(/재난문자 송출[^\n]*/g, "")).not.toMatch(/발송|송출|전송/);
  const visible = (await page.getByTestId("zone-city").innerText()) + (await page.getByTestId("zone-actions").innerText());
  expect(visible).not.toMatch(/experiments\/|promoted|_k\d+|fold/i);
});

test("공개 낙상 영상: recorded UR Fall clip through the live pipeline triggers the confirm card", async ({ page }) => {
  await page.setViewportSize(SIZES[0]);
  await open(page);
  await page.getByTestId("tab-camera").click();
  await page.getByRole("button", { name: "공개 낙상 영상" }).click();
  await expect(page.getByTestId("live-fps")).toContainText("실시간 계산", { timeout: 30_000 });
  await expect(page.getByTestId("alert-panel")).toHaveAttribute("data-status", "pending", { timeout: 30_000 });
  await expect(page.getByTestId("stopwatch")).toContainText("ms");
  await expect(page.getByTestId("button-demo")).toHaveCount(0);
  await expect(page.getByTestId("urfall-credit")).toContainText("CC BY-NC-SA 4.0");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/twin-7-urfall-midfall.png` });
});

test("nav and overview link to /twin", async ({ page }) => {
  await page.setViewportSize(SIZES[0]);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "디지털 트윈" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /디지털 트윈 열기/ })).toBeVisible();
});

test("단말 연결 QR: button and key Q open the overlay with a QR canvas; Esc closes", async ({ page }) => {
  await page.setViewportSize(SIZES[0]);
  // /twin/lan-url is loopback-only on the backend; the test supplies a LAN-looking URL so the QR can render
  await page.route("**/api/twin/lan-url", (r) => r.fulfill({ json: { terminal_url: "http://192.168.0.12:5190/terminal" } }));
  await open(page);
  await page.getByTestId("qr-button").click();
  await expect(page.getByTestId("qr-overlay")).toBeVisible();
  const box = await page.getByTestId("qr-canvas").boundingBox();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(320);
  await expect(page.getByTestId("qr-url")).toContainText("/terminal");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/twin-qr.png` });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("qr-overlay")).toHaveCount(0);
  await page.keyboard.press("q");
  await expect(page.getByTestId("qr-overlay")).toBeVisible();
  await page.keyboard.press("Escape");
});
