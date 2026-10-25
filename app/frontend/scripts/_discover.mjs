import { chromium } from "@playwright/test";
const base = process.env.BASE ?? "http://localhost:5190";
const b = await chromium.launch({ channel: "chrome", args: ["--ignore-gpu-blocklist"] });
const page = await b.newPage({ viewport: { width: 1920, height: 1080 }, locale: "ko-KR", timezoneId: "Asia/Seoul" });
const seen = new Map();
page.on("request", (r) => { const u = new URL(r.url()); if (u.pathname.startsWith("/api/")) { const k = `${r.method()} ${u.pathname}${u.search}`; seen.set(k, (seen.get(k) ?? 0) + 1); } });
const wait = (ms) => page.waitForTimeout(ms);
await page.goto(`${base}/twin`);
await page.getByTestId("action-card").first().waitFor({ timeout: 60000 });
await wait(2000);
for (const k of ["1", "2"]) { await page.keyboard.press(k); await wait(2500); }
await page.getByTestId("select-card").nth(1).click().catch(() => {});
await page.getByTestId("adopt").first().click(); await wait(2500);
await page.getByTestId("approve").first().click(); await wait(2500);
await page.getByTestId("tab-spec").click().catch(() => {}); await wait(1500);
await page.getByTestId("tab-action").click().catch(() => {});
for (const k of ["3", "4"]) { await page.keyboard.press(k); await wait(6000); }
await page.keyboard.press("5"); await wait(1500);
await page.getByTestId("fall-button-demo").click(); await wait(8000);
await page.getByTestId("confirm-fall").click(); await wait(40000);
await page.keyboard.press("6"); await wait(4000);
await page.keyboard.press("7"); await wait(4000);
await page.getByTestId("qr-button").click().catch(() => {}); await wait(1000);
const p2 = await b.newPage(); await p2.goto(`${base}/terminal`); await wait(3000);
for (const [k, n] of [...seen].sort()) console.log(n, k);
await b.close();
