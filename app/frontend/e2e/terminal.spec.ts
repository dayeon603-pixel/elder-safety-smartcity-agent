import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const BACKEND = process.env.TERMINAL_BACKEND_URL ?? "http://127.0.0.1:8010";
const TOKEN_HEADER = "X-Demo-Token";
const SHOT_DIR = resolve(HERE, "../screenshots");
const VIEWPORTS = [
  { name: "phone", width: 390, height: 844 },
  { name: "tablet", width: 1180, height: 820 },
  { name: "phone-landscape", width: 844, height: 390 },
] as const;
// A silent 8-sample WAV so the audio request succeeds offline.
const WAV = Buffer.from("UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=", "base64");

function demoToken(): string {
  const env = readFileSync(resolve(HERE, "../.env.local"), "utf8");
  const m = /^VITE_DEMO_TOKEN=(.+)$/m.exec(env);
  if (m === null) throw new Error("VITE_DEMO_TOKEN missing in .env.local");
  return m[1]!.trim();
}

async function publish(request: APIRequestContext, body: Record<string, unknown>): Promise<void> {
  const res = await request.post(`${BACKEND}/terminal/publish`, { headers: { [TOKEN_HEADER]: demoToken() }, data: body });
  expect(res.ok(), `publish ${res.status()}`).toBeTruthy();
}

async function expectNoScroll(page: Page): Promise<void> {
  const m = await page.evaluate(() => ({
    sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth, ih: window.innerHeight, iw: window.innerWidth,
    bh: document.body.scrollHeight,
    rootOverflow: (() => { const r = document.querySelector<HTMLElement>('[data-testid="terminal-root"]')!; return r.scrollHeight - r.clientHeight + (r.scrollWidth - r.clientWidth); })(),
  }));
  expect(m.sh).toBeLessThanOrEqual(m.ih);
  expect(m.sw).toBeLessThanOrEqual(m.iw);
  expect(m.bh).toBeLessThanOrEqual(m.ih);
  expect(m.rootOverflow).toBeLessThanOrEqual(0);
}

/** In every idle panel the label sits fully above the value, and nothing is clipped by the panel. */
async function expectPanelsStacked(page: Page): Promise<void> {
  const rows = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>(".tm-panel")].map((p) => {
    const kids = [...p.children].map((c) => c.getBoundingClientRect());
    const pr = p.getBoundingClientRect();
    return { gaps: kids.slice(1).map((k, i) => k.top - kids[i]!.bottom), top: kids[0]!.top - pr.top, bottom: pr.bottom - kids[kids.length - 1]!.bottom, n: kids.length };
  }));
  expect(rows.length).toBe(3);
  for (const r of rows) {
    expect(r.n).toBeGreaterThanOrEqual(2);
    for (const g of r.gaps) expect(g).toBeGreaterThanOrEqual(0);
    expect(r.top).toBeGreaterThanOrEqual(0);
    expect(r.bottom).toBeGreaterThanOrEqual(0);
  }
}

for (const vp of VIEWPORTS) {
  test.describe(`terminal ${vp.name} ${vp.width}x${vp.height}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height }, hasTouch: vp.name !== "tablet" });

    test("start tap, idle data, published alerts, clear, reconnect", async ({ page, request }) => {
      let audioRequests = 0;
      await page.route("**/e2e-audio/**", (r) => { audioRequests += 1; return r.fulfill({ status: 200, contentType: "audio/wav", body: WAV }); });
      await page.addInitScript(() => { (window as unknown as { __vib: number[][] }).__vib = []; navigator.vibrate = ((p: unknown) => { (window as unknown as { __vib: number[][] }).__vib.push(p as number[]); return true; }) as typeof navigator.vibrate; });

      await publish(request, { kind: "clear", title: "안전합니다", body: "이제 괜찮습니다" }); // channel may hold a last message from an earlier run
      await page.goto("/terminal");
      const start = page.getByTestId("terminal-start");
      await expect(start).toBeVisible();
      await expectNoScroll(page);
      await page.screenshot({ path: `${SHOT_DIR}/terminal-${vp.name}-start.png` });
      await start.click();

      // Idle: stop name from the API, clock, numbers from the API (shown or an explicit "자료 없음").
      await expect(page.getByTestId("terminal-idle")).toBeVisible();
      await expect(page.getByTestId("terminal-conn")).toHaveAttribute("data-state", "open");
      await expect(page.getByTestId("terminal-temp")).not.toContainText("확인 중");
      await expect(page.getByTestId("terminal-aed")).not.toContainText("확인 중");
      await expectNoScroll(page);
      await expectPanelsStacked(page);
      await page.screenshot({ path: `${SHOT_DIR}/terminal-${vp.name}-idle.png` });

      const stopName = await page.locator(".tm-stop").innerText();
      expect(stopName.length).toBeGreaterThan(0);

      // Heat alert with audio.
      await publish(request, { kind: "heat", title: "폭염 경보", body: "시원한 쉼터로 가세요", audio_url: "/e2e-audio/heat.wav" });
      const alert = page.getByTestId("terminal-alert");
      await expect(alert).toHaveAttribute("data-kind", "heat");
      await expect(page.getByTestId("terminal-title")).toHaveText("폭염 경보");
      await expect(page.getByTestId("terminal-body")).toHaveText("시원한 쉼터로 가세요");
      await expect(page.getByText("훈련 · 실제 발송 아님")).toBeVisible();
      await expect.poll(() => audioRequests).toBeGreaterThan(0);
      expect(await page.evaluate(() => (window as unknown as { __vib: number[][] }).__vib.length)).toBeGreaterThan(0);
      await expectNoScroll(page);
      await page.screenshot({ path: `${SHOT_DIR}/terminal-${vp.name}-heat.png` });

      // Every other kind.
      for (const kind of ["fall", "rain"] as const) {
        await publish(request, { kind, title: `${kind} 제목`, body: `${kind} 본문` });
        await expect(page.getByTestId("terminal-alert")).toHaveAttribute("data-kind", kind);
        await expectNoScroll(page);
        await page.screenshot({ path: `${SHOT_DIR}/terminal-${vp.name}-${kind}.png` });
      }
      await publish(request, { kind: "clear", title: "안전합니다", body: "이제 괜찮습니다" });
      await expect(page.getByTestId("terminal-alert")).toHaveAttribute("data-kind", "clear");
      await page.screenshot({ path: `${SHOT_DIR}/terminal-${vp.name}-clear.png` });

      // Reconnect: drop the stream, the dot must go amber and return to open; last message is shown again.
      await publish(request, { kind: "fall", title: "재접속 확인", body: "마지막 메시지" });
      await expect(page.getByTestId("terminal-title")).toHaveText("재접속 확인");
      await page.context().setOffline(true);
      await page.context().setOffline(false);
      await page.reload();
      await page.getByTestId("terminal-start").click();
      await expect(page.getByTestId("terminal-title")).toHaveText("재접속 확인");
      await expect(page.getByTestId("terminal-conn")).toHaveAttribute("data-state", "open");
    });

    test("stream drops are retried with backoff and the dot recovers", async ({ page, request }) => {
      let aborted = 0;
      const ABORTS = 2;
      await page.route("**/terminal/stream**", (r) => { if (aborted < ABORTS) { aborted += 1; return r.abort(); } return r.continue(); });
      await publish(request, { kind: "fall", title: "끊김 후 복구", body: "마지막 메시지" });
      await page.goto("/terminal");
      await page.getByTestId("terminal-start").click();
      await expect(page.getByTestId("terminal-conn")).toHaveAttribute("data-state", "reconnecting");
      await expect(page.getByTestId("terminal-conn")).toHaveAttribute("data-state", "open");
      await expect(page.getByTestId("terminal-title")).toHaveText("끊김 후 복구"); // replayed after reconnect
      expect(aborted).toBe(ABORTS);
    });

    test("an unknown stop shows an error, not a blank page", async ({ page }) => {
      await page.goto("/terminal?stop=NO_SUCH_STOP");
      await expect(page.locator(".tm-error")).toContainText("정류장을 찾을 수 없습니다");
      await expectNoScroll(page);
    });
  });
}
