/**
 * Backup demo recorder v3 (not part of the test suite). Plays the 6-step 공무원의 하루 on the live app (:5190 / :8010) in real
 * Chrome at 1920x1080 with a burned-in caption bar, a spotlight on whatever is being clicked, the QR beat, and a second
 * page (the phone terminal, 390x844) recorded for a picture-in-picture during the heat and fall moments.
 * Steps never assert: a failing step logs a warning and the recording continues.
 *
 * Usage: node scripts/record_demo.mjs <outDir> [baseUrl]
 * Output in <outDir>: main/*.webm, phone/*.webm and marks.json (offsets + PiP windows) for scripts/compose_demo.sh.
 */
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const OUT = path.resolve(process.argv[2] ?? "./rec-out");
const BASE = process.argv[3] ?? "http://localhost:5190";
const API = "http://localhost:8010";
const W = 1920;
const H = 1080;
const PHONE = { width: 390, height: 844 };
const TOTAL_STEPS = 6;

const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const warn = (m) => console.log(`WARN ${m}`);

const browser = await chromium.launch({ channel: "chrome", args: ["--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"] });

// ---------- phone page (terminal) ----------
// start from a clean terminal: an old message from an earlier run must not show on the phone
const envToken = fs.existsSync(new URL("../.env.local", import.meta.url)) ? (fs.readFileSync(new URL("../.env.local", import.meta.url), "utf8").match(/VITE_DEMO_TOKEN=(.*)/)?.[1] ?? "") : "";
async function publishClear() {
  try { await fetch(`${API}/terminal/publish`, { method: "POST", headers: { "Content-Type": "application/json", "X-Demo-Token": envToken }, body: JSON.stringify({ kind: "clear", title: "정상", body: "" }) }); } catch { warn("clear publish failed"); }
}
await publishClear();
const lan = await (await fetch(`${API}/twin/lan-url`)).json().catch(() => null);
const stopId = lan?.stop_id ?? "GGB120000034";
// the phone runs in its own Chrome process so two screencasts never starve each other
const phoneBrowser = await chromium.launch({ channel: "chrome", args: ["--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"] });
const phoneCtx = await phoneBrowser.newContext({ viewport: PHONE, deviceScaleFactor: 1, isMobile: true, hasTouch: true, locale: "ko-KR", recordVideo: { dir: path.join(OUT, "phone"), size: PHONE } });
const phone = await phoneCtx.newPage();
const phoneStart = Date.now();
await phone.goto(`${BASE}/terminal?stop=${stopId}`);
try { await phone.getByTestId("terminal-start").click({ timeout: 15000 }); } catch { warn("phone start button not found"); }

// ---------- main page ----------
const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, locale: "ko-KR", timezoneId: "Asia/Seoul", recordVideo: { dir: path.join(OUT, "main"), size: { width: W, height: H } } });
const page = await ctx.newPage();
const mainStart = Date.now();
const since = () => (Date.now() - mainStart) / 1000;
const marks = { phoneOffsetS: (phoneStart - mainStart) / 1000, windows: [] };

await page.addInitScript(() => {
  window.addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.style.cssText = "position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border:3px solid #ffb400;border-radius:50%;background:rgba(255,180,0,.25);pointer-events:none;z-index:2147483647;transition:transform 60ms linear,background 120ms";
    document.body.appendChild(c);
    window.addEventListener("mousemove", (e) => { c.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, true);
    window.addEventListener("mousedown", () => { c.style.background = "rgba(217,48,37,.6)"; }, true);
    window.addEventListener("mouseup", () => { c.style.background = "rgba(255,180,0,.25)"; }, true);
    const cap = document.createElement("div");
    cap.id = "demo-caption";
    cap.style.cssText = "position:fixed;left:920px;bottom:96px;transform:translateX(-50%);width:1000px;max-width:1000px;background:#fff;border-radius:14px;border-left:10px solid #0F4C9A;box-shadow:0 10px 36px rgba(0,0,0,.35);padding:14px 26px 16px;z-index:2147483600;font-family:'Apple SD Gothic Neo','Pretendard Variable',sans-serif;opacity:0;transition:opacity 280ms;pointer-events:none;word-break:keep-all";
    cap.innerHTML = '<div id="demo-cap-head" style="font-size:18px;font-weight:800;color:#0F4C9A;letter-spacing:-.01em"></div><div id="demo-cap-line" style="font-size:30px;font-weight:700;line-height:1.35;color:#1F2937;margin-top:2px"></div>';
    document.body.appendChild(cap);
  });
});
page.on("pageerror", (e) => warn(`pageerror ${e.message}`));

const pause = (ms) => page.waitForTimeout(ms);
const tid = (id) => page.getByTestId(id);

async function caption(head, line) {
  await page.evaluate(([h, l]) => {
    const c = document.getElementById("demo-caption");
    if (c === null) return;
    c.style.opacity = "0";
    setTimeout(() => {
      const hh = document.getElementById("demo-cap-head");
      const ll = document.getElementById("demo-cap-line");
      if (hh) hh.textContent = h;
      if (ll) ll.textContent = l;
      c.style.opacity = "1";
    }, 220);
  }, [head, line]);
}
const READ_MS = 1200; // reading time after every caption
const stepCap = async (n, title, line) => { await caption(`${n}/${TOTAL_STEPS} · ${title}`, line); await pause(READ_MS); };

/** Dim everything except the element for `ms` so viewers can read it. */
async function spot(locator, ms = 1200, label = "spot") {
  try {
    const el = locator.first();
    await el.waitFor({ state: "visible", timeout: 8000 });
    const b = await el.boundingBox();
    if (b === null) return;
    await page.evaluate(([bx, ms2]) => {
      const d = document.createElement("div");
      d.style.cssText = `position:fixed;left:${bx.x - 6}px;top:${bx.y - 6}px;width:${bx.width + 12}px;height:${bx.height + 12}px;border:4px solid #ffb400;border-radius:10px;box-shadow:0 0 0 100vmax rgba(10,20,40,.42);pointer-events:none;z-index:2147483300;transition:opacity 250ms`;
      document.body.appendChild(d);
      setTimeout(() => { d.style.opacity = "0"; setTimeout(() => d.remove(), 300); }, ms2);
    }, [b, ms]);
    await pause(ms);
  } catch (e) {
    warn(`spot ${label}: ${e.message.split("\n")[0]}`);
  }
}

async function glide(x, y, steps = 30) { await page.mouse.move(x, y, { steps }); }
async function click(locator, label, spotMs = 0) {
  try {
    const el = locator.first();
    await el.waitFor({ state: "visible", timeout: 15000 });
    const b = await el.boundingBox();
    if (b === null) throw new Error("no box");
    await glide(b.x + b.width / 2, b.y + b.height / 2);
    if (spotMs > 0) await spot(el, spotMs, label); else await pause(350);
    await page.mouse.down();
    await pause(90);
    await page.mouse.up();
    log(`click ${label}`);
    return true;
  } catch (e) {
    warn(`click ${label}: ${e.message.split("\n")[0]}`);
    return false;
  }
}
async function waitAttr(id, attr, value, timeout, label) {
  try {
    await page.waitForFunction(([i, a, v]) => document.querySelector(`[data-testid="${i}"]`)?.getAttribute(a) === v, [id, attr, value], { timeout });
    log(`${label} reached`);
    return true;
  } catch {
    warn(`${label}: not reached within ${timeout} ms`);
    return false;
  }
}
const window_ = (name) => { const w = { name, start: since(), end: null }; marks.windows.push(w); return () => { w.end = since(); }; };

// ---------- the run ----------
log("open /twin");
await page.goto(`${BASE}/twin`);
try { await tid("building-count").waitFor({ timeout: 60000 }); } catch { warn("scene not ready"); }
await page.keyboard.press("1"); // pre-warm like the pre-flight checklist
await pause(1500);
await page.reload();
try { await tid("building-count").waitFor({ timeout: 60000 }); } catch { warn("scene not ready"); }
await glide(W * 0.45, H * 0.4, 40);
await caption("고령자 안전 스마트도시 에이전트 · 관악구", "2025년 7월 22일, 폭염이던 날 공무원의 하루 · 건물 38,488동 실제 외곽선과 층수");
await pause(4500);

log("QR beat");
await caption("현장 단말", "휴대폰으로 QR을 찍으면 이 휴대폰이 정류장 안내단말이 됩니다");
await page.keyboard.press("q");
await pause(3800);
await page.keyboard.press("q");
await pause(600);

log("1 브리핑");
await stepCap(1, "브리핑", "오전 9시 · 주무관은 오늘 할 일부터 확인합니다");
await page.keyboard.press("1");
await pause(3500);

log("2 폭염 대응");
await stepCap(2, "폭염 대응", "17시 · 기상특보는 없지만 에이전트는 관악구를 위험 지역으로 판단");
await page.keyboard.press("2");
await pause(1200);
await spot(tid("effect-chip"), 3000, "효과 칩");
await stepCap(2, "폭염 대응", "재난문자 초안과 근거 · 90자·157자 두 가지 문안");
await click(tid("why").first(), "재난문자 [왜?]");
await pause(500);
await spot(tid("why-panel"), 3200, "근거 패널");
await click(page.getByRole("button", { name: "닫기" }), "근거 닫기");
await stepCap(2, "폭염 대응", "주무관이 채택해 상신 · 변조 탐지 기록이 남습니다");
await click(tid("adopt").first(), "[주무관 채택(상신)]", 1200);
await pause(1800);
await stepCap(2, "폭염 대응", "과장 결재(시연) → 안내단말로 전달 대기 · 훈련, 실제 발송 아님");
await click(tid("approve").first(), "[과장 결재(시연)]", 1200);
await pause(1500); // the heat message reaches the phone about a second after the click
const heatEnd = window_("heat");
await pause(3500);
await click(tid("tab-doc"), "[공문] 탭");
await stepCap(2, "폭염 대응", "공문 초안: 수신 · 제목 · 본문 · 근거가 자동으로 만들어집니다");
await spot(tid("preview"), 3500, "공문");
await click(tid("tab-action"), "[조치] 탭");
await stepCap(2, "폭염 대응", "고령자가 못 건너는 횡단보도는 신호 연장 협의 요청서 초안으로");
await click(tid("select-card").filter({ hasText: "보행신호 연장" }), "보행신호 연장 카드");
await pause(3800);
await click(tid("tab-doc"), "[공문] 탭");
await spot(tid("preview"), 3200, "신호 공문");
heatEnd();

log("3 현장 전달");
await stepCap(3, "현장 전달", "17시 30분 · 시연 인물이 정류장으로 걸어갑니다");
await page.keyboard.press("3");
await pause(2500);
let shown = false;
for (let attempt = 0; attempt < 2 && !shown; attempt++) {
  try { await tid("story-terminal").waitFor({ state: "visible", timeout: 22000 }); shown = true; } catch {
    warn(`정류장 단말 not shown (attempt ${attempt + 1}); story phase=${await tid("story-root").getAttribute("data-phase").catch(() => "n/a")}`);
    await page.keyboard.press("3");
  }
}
await stepCap(3, "현장 전달", "정류장 단말과 휴대폰에 같은 안내가 뜹니다 · 휴대폰 없어도 확인");
await pause(2500);
await spot(tid("story-terminal"), 3000, "정류장 단말");

log("4 실버존 재배치");
await stepCap(4, "실버존 재배치", "현행 기준 · 회색 점선이 지금의 실버존, 빨간 점이 사고 다발지");
await page.keyboard.press("4");
await pause(3800);
await stepCap(4, "실버존 재배치", "에이전트로 바꾸면 · 파란 실버존이 새로 깔리고 초록 점이 새로 보호된 다발지");
await pause(3600);
await spot(tid("silver-legend"), 3200, "범례");
await pause(1200);

log("5 낙상 대응");
await stepCap(5, "낙상 대응", "17시 40분 · 횡단보도 카메라가 낙상을 감지합니다");
await page.keyboard.press("5");
await pause(1800);
await click(tid("tab-camera"), "[현장 카메라] 탭");
await click(page.getByRole("button", { name: "공개 낙상 영상" }), "[공개 낙상 영상]");
await waitAttr("alert-panel", "data-status", "pending", 45000, "관제요원 확인 필요");
await stepCap(5, "낙상 대응", "관제요원 확인 대기 · 감지 시간과 시연 카메라 위치가 표시됩니다");
await spot(tid("alert-panel"), 3800, "알림 카드");
await stepCap(5, "낙상 대응", "확인한 뒤에만 119 연계(훈련) · 가장 가까운 열린 AED 경로");
await click(tid("confirm-fall"), "[확인]", 1200);
await waitAttr("alert-panel", "data-status", "done", 60000, "119 연계(훈련) 완료");
const fallEnd = window_("fall"); // the fall alert is on the phone once the 119 step has published it
await pause(6500);
fallEnd();

log("6 야간 AED");
const exit = tid("story-exit");
if ((await exit.count()) > 0) { await click(exit, "관제 시점 (Esc)"); await pause(1500); }
await stepCap(6, "야간 AED", "밤 11시 · 열린 AED가 하나씩 꺼집니다");
await page.keyboard.press("6");
await pause(5500);
await stepCap(6, "야간 AED", "밤에는 문자 대신 24시간 개방 요청 공문을 준비합니다");
await spot(tid("effect-chip"), 2800, "효과 칩");
await stepCap(6, "야간 AED", "설치기관이 거절하면? 사유를 남기고 다음 후보를 제안합니다");
await click(tid("hold").first(), "AED [보류(사유 기록)]");
await pause(500);
await click(tid("refuse-chip"), "[설치기관 거절]", 1200);
await pause(1500);
await spot(page.locator("[data-testid=action-card][data-next='1']"), 3200, "다음 후보");
await stepCap(6, "야간 AED", "다음 후보 카드를 채택 · 개방 요청 공문 초안이 만들어집니다");
await click(page.locator("[data-testid=action-card][data-next='1'] [data-testid=adopt]"), "다음 후보 [주무관 채택(상신)]", 1000);
await pause(1500);
await click(tid("tab-doc"), "[공문] 탭");
await spot(tid("preview"), 3600, "공문");
await stepCap(6, "야간 AED", "모든 출력은 훈련용 · 실제로 보내지 않습니다");
await glide(W * 0.45, H * 0.4, 40);
await pause(3500);

log("closing");
const mainVideo = page.video();
const phoneVideo = phone.video();
await ctx.close();
await phoneCtx.close();
await phoneBrowser.close();
await browser.close();
marks.mainVideo = await mainVideo.path();
marks.phoneVideo = await phoneVideo.path();
marks.durationS = since();
fs.writeFileSync(path.join(OUT, "marks.json"), JSON.stringify(marks, null, 2));
log(`video: ${marks.mainVideo}`);
log(`phone: ${marks.phoneVideo}`);
