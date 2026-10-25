import { describe, expect, it } from "vitest";
import { CLASS_PRESENTATION, DRILL_PREFIX, KIND_CLASS, classOf, formatAlertTime, senderOf, smsText } from "./alertClass";
import { TERMINAL_KINDS } from "./types";
import { contrastRatio } from "./present";

describe("alertClass", () => {
  it("presents heat and 구-level rain as 안전안내문자 (별표 1)", () => {
    expect(classOf({ kind: "heat" })).toBe("safety");
    expect(classOf({ kind: "rain" })).toBe("safety");
    expect(CLASS_PRESENTATION[KIND_CLASS.heat].header).toBe("안전 안내 문자");
  });
  it("never produces a bare real sender", () => {
    for (const s of [senderOf({ sgg_code: "11620" }, "관악구"), senderOf({ sgg_code: "99999" }, "관악구"), senderOf(null, "동작구")]) {
      expect(s).toMatch(/\(시연\)$/);
      expect(s).not.toMatch(/청$|행정안전부/);
    }
    expect(senderOf({ sgg_code: "11590" }, "관악구")).toBe("동작구(시연)");
  });
  it("formats text like a 표준문안: [훈련] first, agency in brackets last", () => {
    expect(smsText("관악구(시연)", "폭염 경보", "그늘에서 쉬세요")).toBe("[훈련] 폭염 경보. 그늘에서 쉬세요. [관악구(시연)]");
    expect(smsText("관악구(시연)", "", "본문!")).toBe(`${DRILL_PREFIX} 본문! [관악구(시연)]`);
  });
  it("covers every kind and keeps header text readable", () => {
    for (const k of TERMINAL_KINDS) expect(CLASS_PRESENTATION[KIND_CLASS[k]].header.length).toBeGreaterThan(0);
    for (const c of Object.values(CLASS_PRESENTATION)) expect(contrastRatio(c.accent, c.onAccent)).toBeGreaterThanOrEqual(4.5);
  });
  it("formats time in KST", () => {
    expect(formatAlertTime(new Date("2026-10-25T05:03:00Z"), "Asia/Seoul")).toBe("2026. 10. 25. 오후 2:03");
  });
});
