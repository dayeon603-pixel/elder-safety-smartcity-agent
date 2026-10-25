import { describe, expect, it } from "vitest";
import { normalizeAgentResult, parseSse } from "./agent";

describe("normalizeAgentResult (real /agent/run shape)", () => {
  const raw = {
    run_id: "run-0123456789ab", planner: "llm", planner_reason: "", model_name: "qwen2.5:3b", model_digest: "abc",
    steps: [
      { n: 1, tool: "get_heat_region", source: "llm", ok: true, ms: 12.5, summary: "관악구 30.5℃", result: { tmax_c: 30.5 } },
      { n: 2, tool: "send_119_report", source: "llm", ok: true, ms: 8, summary: "202", result: { simulator: "119 시뮬레이터", http_status: 202, receipt_id: "emg-0123456789ab" } },
      { n: 3, tool: "describe_scene", source: "llm", ok: true, ms: 20000, summary: "ok", result: { description: "바닥에 사람이 누워 있음" } },
    ],
    briefing: "낙상 의심", briefing_source: "llm", grounding: { checked: true, ok: true }, total_ms: 11000.5, notes: ["n"],
  };
  it("reads steps, receipts, planner, briefing, scene text and grounding", () => {
    const r = normalizeAgentResult(raw);
    expect(r?.runId).toBe("run-0123456789ab");
    expect(r?.model).toBe("qwen2.5:3b");
    expect(r?.steps.map((s) => [s.name, s.ms])).toEqual([["get_heat_region", 12.5], ["send_119_report", 8], ["describe_scene", 20000]]);
    expect(r?.receipts).toEqual([{ target: "119 시뮬레이터", status: 202, id: "emg-0123456789ab" }]);
    expect(r?.sceneText).toContain("누워");
    expect(r?.grounded).toBe(true);
    expect(r?.totalMs).toBe(11000.5);
  });
  it("marks a template briefing as not grounded and rejects a missing step list", () => {
    expect(normalizeAgentResult({ ...raw, briefing_source: "template", grounding: { checked: true, ok: false } })?.grounded).toBe(false);
    expect(normalizeAgentResult({ ok: true })).toBeNull();
    expect(normalizeAgentResult("x")).toBeNull();
  });
});

describe("parseSse", () => {
  it("returns complete messages and keeps the partial remainder", () => {
    const { messages, rest } = parseSse('event: step\ndata: {"tool":"a","ms":1}\n\nevent: done\ndata: {"steps":[]}\n\nevent: step\ndata: {"to');
    expect(messages.map((m) => m.event)).toEqual(["step", "done"]);
    expect(rest).toBe('event: step\ndata: {"to');
  });
  it("skips non-JSON data and handles CRLF", () => {
    const { messages } = parseSse(": ping\r\n\r\ndata: not json\r\n\r\ndata: {\"x\":1}\r\n\r\n");
    expect(messages).toEqual([{ event: "message", data: { x: 1 } }]);
  });
});
