/**
 * Backup demo recorder (not part of the test suite). Follows 데모_진행표.md (rehearsed 2026-10-25) on the live app (:5190 / :8010)
 * in real Chrome at 1920x1080, paced at ~290 syllables/min of each 멘트 line. Steps never assert: a failing step logs a warning and the recording continues.
 *
 * Usage: node scripts/record_demo.mjs <outDir> [baseUrl]
 */
import { chromium } from "@playwright/test";

const OUT = process.argv[2] ?? "./rec-out";
const BASE = process.argv[3] ?? "http://localhost:5190";
const W = 1920;
const H = 1080;
const SYL_PER_MIN = 290;
const syll = (t) => (t.match(/[가-힣0-9A-Za-z]/g) ?? []).length;

const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const warn = (m) => console.log(`WARN ${m}`);

const browser = await chromium.launch({ channel: "chrome", args: ["--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"] });
const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, locale: "ko-KR", timezoneId: "Asia/Seoul", recordVideo: { dir: OUT, size: { width: W, height: H } } });
const page = await ctx.newPage();
await page.addInitScript(() => {
  // visible cursor: a ring that follows the mouse (the recorded video has no OS cursor)
  window.addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.style.cssText = "position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border:3px solid #ffd479;border-radius:50%;background:rgba(255,212,121,.25);pointer-events:none;z-index:2147483647;transition:transform 60ms linear,background 120ms";
    document.body.appendChild(c);
    window.addEventListener("mousemove", (e) => { c.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, true);
    window.addEventListener("mousedown", () => { c.style.background = "rgba(255,90,90,.6)"; }, true);
    window.addEventListener("mouseup", () => { c.style.background = "rgba(255,212,121,.25)"; }, true);
  });
});
page.on("pageerror", (e) => warn(`pageerror ${e.message}`));

async function glide(x, y, steps = 28) {
  await page.mouse.move(x, y, { steps });
}
async function click(locator, label) {
  try {
    const el = locator.first();
    await el.waitFor({ state: "visible", timeout: 15000 });
    const b = await el.boundingBox();
    if (b === null) throw new Error("no box");
    await glide(b.x + b.width / 2, b.y + b.height / 2);
    await page.waitForTimeout(350);
    await page.mouse.down();
    await page.waitForTimeout(90);
    await page.mouse.up();
    log(`click ${label}`);
    return true;
  } catch (e) {
    warn(`click ${label}: ${e.message.split("\n")[0]}`);
    return false;
  }
}
const tid = (id) => page.getByTestId(id);
const pause = (ms) => page.waitForTimeout(ms);
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

/** Run `action`, then wait until the 멘트 line has been "spoken" (counted from the start of the step). */
async function stepSay(line, action, extraMs = 0) {
  const t0 = Date.now();
  await action();
  const speak = (syll(line) / SYL_PER_MIN) * 1000 * 60 + extraMs;
  const left = speak - (Date.now() - t0);
  if (left > 0) await pause(left);
}

log("open /twin");
await page.goto(`${BASE}/twin`);
try { await tid("building-count").waitFor({ timeout: 60000 }); } catch { warn("scene not ready"); }
await page.keyboard.press("1"); // pre-warm like the pre-flight checklist, then back to a clean start
await pause(1500);
await page.reload();
try { await tid("building-count").waitFor({ timeout: 60000 }); } catch { warn("scene not ready"); }
await glide(W * 0.4, H * 0.35, 40);
await pause(2500);

await stepSay("방금 표의 네 줄이 실제로 어떻게 돌아가는지 라이브로 보여 드리겠습니다.", async () => {});
await stepSay("2025년 7월 22일, 폭염이던 날 관악구 공무원의 하루입니다. 건물 38,488동은 국토부 자료의 실제 모양과 층수입니다.", async () => {});

log("1 브리핑");
await stepSay("① 아침 9시, 주무관은 오늘 할 일부터 봅니다.", async () => { await page.keyboard.press("1"); });

log("2 언제 · 폭염 대응");
await stepSay("② 먼저 언제입니다. 오후 5시, 기상특보는 없었지만 에이전트는 관악구를 위험 지역으로 판단했습니다.", async () => { await page.keyboard.press("2"); });
await stepSay("주무관이 근거를 확인해", async () => { await click(tid("why").first(), "재난문자 [왜?]"); });
await click(page.getByRole("button", { name: "닫기" }), "근거 닫기");
await stepSay("재난문자 초안을 상신하고,", async () => { await click(tid("adopt").first(), "[주무관 채택(상신)]"); });
await stepSay("과장이 결재하면 공문 초안도 함께 만들어집니다.", async () => { await click(tid("approve").first(), "[과장 결재(시연)]"); });

log("3 현장 전달 / 시민 시점 (키 3)");
await stepSay("③ 5시 반, 이 안내가 현장에 닿습니다.", async () => { await page.keyboard.press("3"); });
await stepSay("시연 인물이 정류장에 도착하면 단말에 안내가 뜨고, 제 앞의 실물 휴대폰도 함께 켜집니다.", async () => { try { await tid("story-terminal").waitFor({ state: "visible", timeout: 45000 }); } catch { warn("정류장 단말 not shown"); } }, 2500);
await pause(2000);

log("4 어디에 · 실버존 (키 4)");
await stepSay("④ 다음은 어디에입니다. 현행 기준에서 에이전트로 바꾸면 실버존이 사고가 나는 곳으로 옮겨 가고, 서울 다발지 중 덮는 곳이 9곳에서 58곳으로 바뀝니다.", async () => { await page.keyboard.press("4"); }, 2500);

log("5 무엇을 · 낙상 (키 5, 공개 낙상 영상, 확인)");
await stepSay("⑤ 이번엔 무엇을입니다. 5시 40분, 횡단보도에서 낙상이 일어나면 카메라가 감지해 관제요원에게 먼저 알리고,", async () => {
  await page.keyboard.press("5");
  await pause(1500);
  await click(page.getByRole("button", { name: "공개 낙상 영상" }), "[공개 낙상 영상]");
  await waitAttr("alert-panel", "data-status", "pending", 45000, "관제요원 확인 필요");
}, 1500);
await pause(2500);
await stepSay("확인을 거친 뒤에만 119 연계로 넘어갑니다. 지금 열린 가장 가까운 AED까지 길도 보여 줍니다.", async () => { await click(tid("confirm-fall"), "[확인]"); });
await waitAttr("alert-panel", "data-status", "done", 60000, "119 연계(훈련) 완료");
await pause(2500);

log("6 밤 · AED (키 6)");
const exit = tid("story-exit");
if ((await exit.count()) > 0) { await click(exit, "관제 시점 (Esc)"); await pause(1500); }
await stepSay("⑥ 마지막으로 밤입니다. 밤 11시가 되면 AED가 하나씩 꺼집니다.", async () => { await page.keyboard.press("6"); }, 3000);
await stepSay("규정상 밤에는 폭염 문자를 보내지 않아, 닫힌 AED의 개방 요청 공문을 준비합니다.", async () => { await click(tid("adopt").first(), "AED [주무관 채택(상신)]"); await click(tid("tab-doc"), "[공문] 탭"); });
await stepSay("모든 출력은 훈련용이며 실제로 보내지 않습니다.", async () => { await glide(W * 0.45, H * 0.4, 40); });
await pause(1500);

log("closing");
const video = page.video();
await ctx.close();
await browser.close();
log(`video: ${await video.path()}`);
