/**
 * Rehearsal of 데모_진행표.md on the live app: executes each row, waits for the expected on-screen evidence, measures
 * the elapsed time per step and prints mismatches. Never aborts: a missing element is reported and the run continues.
 * Usage: node scripts/rehearse_demo.mjs [baseUrl]
 */
import { chromium } from "@playwright/test";

const BASE = process.argv[2] ?? "http://localhost:5190";
const SYL_PER_MIN = 290;
const log = (m) => console.log(m);
const syll = (t) => (t.match(/[가-힣0-9A-Za-z]/g) ?? []).length;

const browser = await chromium.launch({ channel: "chrome", args: ["--ignore-gpu-blocklist"] });
const page = await (await browser.newContext({ viewport: { width: 1920, height: 1080 }, locale: "ko-KR", timezoneId: "Asia/Seoul" })).newPage();
const tid = (i) => page.getByTestId(i);
const body = () => page.locator("[data-testid=twin-root]").innerText();

/** Poll until `check` returns truthy; returns ms waited or null on timeout. */
async function appears(check, timeout = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try { if (await check()) return Date.now() - t0; } catch { /* element not there yet */ }
    await page.waitForTimeout(100);
  }
  return null;
}
const has = (re) => async () => re.test(await body());

const rows = [];
let cum = 0;
async function step(name, line, action, checks) {
  const t0 = Date.now();
  let actErr = null;
  try { await action(); } catch (e) { actErr = e.message.split("\n")[0]; }
  const res = [];
  for (const [label, fn, to] of checks) {
    const w = await appears(fn, to ?? 15000);
    res.push({ label, ms: w });
  }
  const spent = Date.now() - t0;
  const speak = (syll(line) / SYL_PER_MIN) * 60000;
  if (spent < speak) await page.waitForTimeout(speak - spent);
  const total = Date.now() - t0;
  const r = { name, startCum: cum, spentMs: spent, speakMs: Math.round(speak), stepMs: total, actErr, res };
  cum += total;
  rows.push(r);
  log(`${name}: cum-start ${(r.startCum / 1000).toFixed(1)}s | action+evidence ${(spent / 1000).toFixed(1)}s | speech ${(speak / 1000).toFixed(1)}s | step ${(total / 1000).toFixed(1)}s ${actErr ? "ACTERR " + actErr : ""}`);
  for (const c of res) log(`   ${c.ms === null ? "MISSING" : "ok " + c.ms + "ms"}  ${c.label}`);
}

await page.goto(`${BASE}/twin`);
await tid("building-count").waitFor({ timeout: 60000 });
await page.waitForTimeout(2000);
await page.keyboard.press("1"); // pre-warm per the checklist
await page.waitForTimeout(1500);
await page.reload();
await tid("building-count").waitFor({ timeout: 60000 });
await page.waitForTimeout(3000);

const L = {
  intro1: "방금 표의 네 줄이 실제로 어떻게 돌아가는지 라이브로 보여 드리겠습니다.",
  intro2: "2025년 7월 22일, 폭염이던 날 관악구 공무원의 하루입니다. 건물 38,488동은 국토부 자료의 실제 모양과 층수입니다.",
  s1: "① 아침 9시, 주무관은 오늘 할 일부터 봅니다.",
  s2a: "② 먼저 언제입니다. 오후 5시, 기상특보는 없었지만 에이전트는 관악구를 위험 지역으로 판단했습니다.",
  s2b: "주무관이 근거를 확인해",
  s2c: "재난문자 초안을 상신하고,",
  s2d: "과장이 결재하면 공문 초안도 함께 만들어집니다.",
  s3a: "③ 5시 반, 이 안내가 현장에 닿습니다.",
  s3b: "시연 인물이 정류장에 도착하면 단말에 안내가 뜨고, 제 앞의 실물 휴대폰도 함께 켜집니다.",
  s4: "④ 다음은 어디에입니다. 현행 기준에서 에이전트로 바꾸면 실버존이 사고가 나는 곳으로 옮겨 가고, 서울 다발지 중 덮는 곳이 9곳에서 58곳으로 바뀝니다.",
  s5a: "⑤ 이번엔 무엇을입니다. 5시 40분, 횡단보도에서 낙상이 일어나면 카메라가 감지해 관제요원에게 먼저 알리고,",
  s5b: "확인을 거친 뒤에만 119 연계로 넘어갑니다. 지금 열린 가장 가까운 AED까지 길도 보여 줍니다.",
  s6a: "⑥ 마지막으로 밤입니다. 밤 11시가 되면 AED가 하나씩 꺼집니다.",
  s6b: "규정상 밤에는 폭염 문자를 보내지 않아, 닫힌 AED의 개방 요청 공문을 준비합니다.",
  end: "모든 출력은 훈련용이며 실제로 보내지 않습니다.",
};
const chipHas = (re) => async () => (await tid("effect-chip").count()) > 0 && re.test(await tid("effect-chip").innerText());
const mode = async () => (await tid("mode-ours").getAttribute("aria-pressed")) === "true";

await step("0 시작", L.intro1, async () => {}, [["/twin 관제 시점", has(/관제 시점/)]]);
await step("1 도입", L.intro2, async () => {}, [["건물 38488동", has(/38488/)], ["현행 9곳 · 에이전트 58곳", has(/현행 9곳[\s\S]*에이전트 58곳/)], ["층수 실측 81.8%", has(/실측 81.8%/)]]);
await step("2 키1 브리핑", L.s1, async () => { await page.keyboard.press("1"); }, [["09:00", has(/09:00/)], ["단계 ① 체크", async () => (await tid("scn-s1").getAttribute("data-done")) === "1"]]);
await step("3 키2 언제", L.s2a, async () => { await page.keyboard.press("2"); }, [["17:00", has(/17:00/)], ["재난문자 초안 90/157 카운터", has(/\/90자[\s\S]*\/157자/)], ["효과 칩(49→60 계열, API)", chipHas(/\d+(\.\d+)?%/), 6000], ["공문 미리보기", async () => (await tid("preview").count()) > 0]]);
await step("4 [왜?]", L.s2b, async () => { await tid("why").first().click(); }, [["근거 패널(체감·환자)", async () => /체감/.test(await tid("why-panel").innerText()) && /환자/.test(await tid("why-panel").innerText())]]);
await page.getByRole("button", { name: "닫기" }).click().catch(() => log("   (닫기 버튼 없음)"));
await step("5 [주무관 채택(상신)]", L.s2c, async () => { await tid("adopt").first().click(); }, [["'상신됨 · 변조 탐지 기록'", has(/상신됨/), 6000], ["'채택 · 결재 대기' 문구(옛 진행표)", has(/채택 · 결재 대기/), 1500]]);
await step("6 [과장 결재(시연)]", L.s2d, async () => { await tid("approve").first().click(); }, [["'전달 대기'", has(/전달 대기/)], ["훈련 · 발송 안 함", has(/훈련 · 발송 안 함/)]]);
await step("7 키3 현장 전달", L.s3a, async () => { await page.keyboard.press("3"); }, [["17:30", has(/17:30/)], ["효과 칩 '휴대폰 없이도…'", chipHas(/휴대폰 없이도/), 4000], ["시민 시점 자동 전환", async () => (await tid("story-hud").count()) > 0, 8000]]);
await step("8 걷기·단말·휴대폰", L.s3b, async () => {}, [["정류장 단말 메시지(걷기 약 13초)", async () => (await tid("story-terminal").count()) > 0, 40000]]);
await step("9 키4 어디에·실버존", L.s4, async () => { await page.keyboard.press("4"); }, [["시민 시점 종료", async () => (await tid("story-hud").count()) === 0, 6000], ["17:30 유지", has(/17:30/)], ["처음 [현행 기준]", async () => !(await mode()), 1000], ["[에이전트]로 전환(1.4초 뒤)", mode, 6000], ["점수판 현행 9곳 · 에이전트 58곳", has(/현행 9곳[\s\S]*에이전트 58곳/)], ["효과 칩 '58곳'", chipHas(/58곳/), 4000]]);
await step("10 키5 무엇을", L.s5a, async () => { await page.keyboard.press("5"); }, [["17:40", has(/17:40/)], ["효과 칩(초, API)", chipHas(/초/), 4000], ["아직 낙상 없음(alert idle)", async () => (await tid("alert-panel").getAttribute("data-status")) === "idle", 1000]]);
await step("11 [공개 낙상 영상]", L.s5b.split(" ")[0], async () => { await page.getByRole("button", { name: "공개 낙상 영상" }).click(); }, [["관제요원 확인 필요", async () => (await tid("alert-panel").getAttribute("data-status")) === "pending", 30000], ["17:40 유지", has(/17:40/)], ["낙상 시작 → 감지 ms(버튼 시연 아님)", async () => (await tid("button-demo").count()) === 0 && /ms/.test(await tid("stopwatch").innerText()), 5000]]);
await step("12 [확인]", L.s5b, async () => { await tid("confirm-fall").click(); }, [["가장 가까운 열린 AED …m", has(/가장 가까운 열린 AED[^\n]*\d+ m/)], ["119 연계 완료(훈련)", async () => (await tid("alert-panel").getAttribute("data-status")) === "done", 60000]]);
await step("13 키6 밤", L.s6a, async () => { if ((await tid("story-exit").count()) > 0) await tid("story-exit").click(); await page.keyboard.press("6"); }, [["23:00", has(/23:00/)], ["[현행 기준]로 복귀", async () => !(await mode()), 2000], ["야간 AED 카드", async () => (await tid("action-cards").getAttribute("data-night")) === "1"], ["효과 칩(%, API)", chipHas(/\d+(\.\d+)?%/), 4000]]);
await step("14 AED [주무관 채택(상신)]", L.s6b, async () => { await tid("adopt").first().click(); }, [["공문: 설치기관 관리책임자", async () => /설치기관 관리책임자/.test(await tid("preview").innerText())]]);
await step("15 맺음", L.end, async () => {}, []);

log(`\nTOTAL ${(cum / 1000).toFixed(1)}s`);
console.log("JSON " + JSON.stringify(rows.map((r) => ({ n: r.name, s: +(r.startCum / 1000).toFixed(1), step: +(r.stepMs / 1000).toFixed(1), miss: r.res.filter((c) => c.ms === null).map((c) => c.label) }))));
await browser.close();
