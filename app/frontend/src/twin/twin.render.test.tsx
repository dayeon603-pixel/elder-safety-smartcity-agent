import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanSource, letterFor, loadActions } from "./api";
import { FORBIDDEN_SOURCE_RE } from "./constants";
import { ActionsZone, ProtocolZone, Scoreboard } from "./TwinPanels";
import type { TwinCtl } from "./useTwin";
import type { TwinState } from "./types";

const payload = {
  night: false,
  actions: [
    {
      id: "cbs", kind: "heat", title: "재난문자 초안", body: "[관악구] 폭염", chars: "149/157", status: "draft", destination: { label: "재난문자 송출 승인권자", org: "구청 재난안전 부서" }, document_url: "/doc", flow_note: "입력자 송출 요청 → 부서장 승인",
      feedback_ref: { sgg_code: "11620", date: "2025-07-22", action_id: "cbs" }, cap_url: "/x",
      drafts: [{ limit: 90, length: 72, label: "90자형(표준)", text: "A" }, { limit: 157, length: 149, label: "157자형(폭염 경보 문안)", text: "B" }],
      evidence: [
        { label: "위험 순위", value: "61/250", source: "experiments/heat 산출", display_source: "폭염 위험 모델" },
        { label: "저장 실행", value: "promoted_k3918_fold2024", source: "x" },
        { label: "체감 최고", value: "32.7", source: "promoted (silver_promoted_k3918_fold2024)" },
        { label: "ASOS", value: "32", source: "기상청 ASOS" },
        { label: "에이전트 경보", value: true, source: "x" },
        { label: "예상 환자 λ", value: 0.33831, source: "x" },
      ],
    },
  ],
  notes: [], disclaimer: null,
};

const base = (): TwinState => ({
  date: "2025-07-22", hour: 17, daytype: "weekday", aed: [], aedCandidates: [], shelters: [], crosswalks: [], silverCurrent: [], silverOurs: [], silverRadiusM: 300, accidents: [], stops: [],
  heat: { apparentMaxC: 32.7, tmaxC: 32, b1Alert: false, oursAlert: true, rank: 61, nCells: 250, expectedCases: null, rankText: "250곳 중 상위 40% 안", expectedCasesText: null },
  weather: null, aedOpenPct: 90, aedOpenCount: 9, aedTotal: 10, aedOpenPctAfter: null, seoulZones: null, sources: [], silverEval: { year: 2024, n: 59, coveredCurrent: 9, coveredOurs: 58, source: "promoted (silver_promoted_k3918_fold2024) experiments/silver" },
});

async function ctlFixture(): Promise<TwinCtl> {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } })));
  const actions = await loadActions("2025-07-22", 17);
  const noop = (): void => undefined;
  return {
    scene: null, sceneError: null, state: base(), stateError: null, actions, actionsError: null, rainDays: [], rainChecked: true, date: "2025-07-22", hour: 17, playing: false, mode: "asis", scenario: "s2", effects: null, infra: { cctv: null, signal: [], stop: [], aed: [], control: null, counts: { cctv: null, signal: 7, stop: 58, aed: 425 }, bitCandidates: null }, silverInside: null,
    stepsDone: { 1: true, 2: false, 3: false, 4: false, 5: false, 6: false }, stages: {}, holds: {}, tally: { adopt: 0, hold: 0, entries: 62, valid: true }, place: null, log: [], doc: null, decisions: {}, selectedCard: null,
    fall: { status: "idle", source: null, detectMs: null, paintMs: null, routeText: null, publishText: null, where: null, alertText: null, cameraLabel: null },
    board: { total: 59, current: 9, ours: 58, year: 2024, source: "promoted (silver_promoted_k3918_fold2024)" }, rainMm: null,
    letter: null, listen: noop, toasts: [], times: {}, inbox: null,
    setMode: noop, setHour: noop, togglePlay: noop, runScenario: noop, decide: noop, selectCard: noop, generateCapFor: noop, generateFhir: noop, approve: noop, hold: noop, confirmFall: noop, triggerFall: noop,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("source display", () => {
  it("cleanSource prefers display_source and drops internal identifiers", () => {
    expect(cleanSource("experiments/x", "표시용")).toBe("표시용");
    expect(cleanSource("experiments/x")).toBeNull();
    expect(cleanSource("promoted_k3918_fold2024", "fold 2024")).toBeNull();
    expect(cleanSource("기상청 ASOS")).toBe("기상청 ASOS");
  });

  it("rendered zones never contain experiments/, promoted, _k####, fold", async () => {
    const ctl = await ctlFixture();
    const { container } = render(<><Scoreboard ctl={ctl} /><ActionsZone ctl={ctl} /><ProtocolZone ctl={ctl} view="doc" /></>);
    await waitFor(() => expect(screen.getByTestId("action-card")).toBeTruthy());
    fireEvent.click(screen.getByTestId("why"));
    const why = screen.getByTestId("why-panel").textContent ?? "";
    expect(why).toContain("폭염 위험 모델");
    expect(why).toContain("에이전트 권고");
    expect(why).toContain("사전 조치");
    expect(why).toContain("예상 환자 (명)");
    expect(why).not.toMatch(/true|false|λ/);
    expect(container.textContent ?? "").not.toMatch(FORBIDDEN_SOURCE_RE);
  });

  it("shows destination, both CBS drafts and the approval chip; never says 발송 완료", async () => {
    const ctl = await ctlFixture();
    const { container } = render(<ActionsZone ctl={ctl} />);
    expect(screen.getByTestId("destination").textContent).toContain("전달 대상: 재난문자 송출 승인권자");
    expect(screen.getByTestId("cbs-count").textContent).toBe("72/90자 (표준) · 149/157자 (폭염 경보 문안)");
    expect(screen.getByTestId("flow-note").textContent).toContain("부서장 승인");
    expect(screen.getByTestId("approval-chip").textContent).toContain("과장 결재(시연)");
    // the authority's own name ("재난문자 송출 승인권자") comes from the API and is allowed
    // authority names and the approval-flow note are the API's own regulation wording (they say 송출 about the 행안부 system); our own text never does
    const own = (container.textContent ?? "").replace(screen.getByTestId("flow-note").textContent ?? "", "").replace(/재난문자 송출 승인권자/g, "");
    expect(own).not.toMatch(/발송 완료|송출|전송/);
    expect(screen.getByTestId("result-cards").textContent).toContain("현행 특보 기준: 대상 아님");
    expect(screen.getByTestId("result-cards").textContent).toContain("에이전트 권고: 사전 조치");
    expect(screen.getByTestId("result-cards").textContent).toContain("공공데이터 운영시간 기준 · 현장 확인 필요");
    expect(screen.getByTestId("result-cards").textContent).toContain("관악구 무더위쉼터 공개자료 없음");
  });

  it("preview lists 수신, 제목, 본문, 근거, 생성 시각 by default", async () => {
    const ctl = await ctlFixture();
    render(<ProtocolZone ctl={ctl} view="doc" />);
    const t = screen.getByTestId("preview").textContent ?? "";
    for (const k of ["수신", "제목", "본문", "근거", "생성 시각"]) expect(t).toContain(k);
    expect(screen.queryByTestId("terminal")).toBeNull();
    cleanup();
    render(<ProtocolZone ctl={ctl} view="spec" />);
    expect(screen.getByTestId("terminal")).toBeTruthy();
  });

  it("letterFor never returns another card's letter (stale response)", () => {
    const l = { to: "A", org: null, title: "T", paragraphs: [], evidence: [], createdAt: null, footer: null, never: null };
    expect(letterFor("cbs", { cardId: "cbs", letter: l })).toBe(l);
    expect(letterFor("voice", { cardId: "cbs", letter: l })).toBeNull();
    expect(letterFor("voice", null)).toBeNull();
  });

  it("voice card preview uses its own title and script, never the 재난문자 letter title", async () => {
    const ctl = await ctlFixture();
    const cbsCard = ctl.actions?.cards[0];
    expect(cbsCard).toBeTruthy();
    const voice = { ...(cbsCard as NonNullable<typeof cbsCard>), id: "voice", title: "경로당 음성 문안", summary: "음성 문안 본문", drafts: [], destination: "경로당·마을방송", destinationOrg: "주민센터" };
    const letter = { to: "경로당·마을방송", org: "주민센터", title: "재난문자 초안 송출", paragraphs: ["재난문자 본문"], evidence: [], createdAt: "t", footer: null, never: null };
    const c2: TwinCtl = { ...ctl, actions: { ...(ctl.actions as NonNullable<typeof ctl.actions>), cards: [voice] }, selectedCard: "voice", letter: { cardId: "voice", letter } };
    render(<ProtocolZone ctl={c2} view="doc" />);
    expect(screen.getByTestId("pv-title").textContent).toBe("경로당 음성 문안");
    const t = screen.getByTestId("preview").textContent ?? "";
    expect(t).toContain("음성 문안 본문");
    expect(t).not.toContain("재난문자 초안 송출");
    expect(t).not.toContain("재난문자 본문");
  });
});

describe("infra legend", () => {
  it("lists known counts and omits a type whose count is null", async () => {
    const ctl = await ctlFixture();
    const { InfraLegend } = await import("./TwinPanels");
    render(<InfraLegend ctl={ctl} />);
    const t = screen.getByTestId("infra-legend").textContent ?? "";
    expect(t).toContain("신호 횡단보도 7");
    expect(t).toContain("정류장 58");
    expect(t).toContain("AED 425");
    expect(t).not.toContain("CCTV");
  });
});
