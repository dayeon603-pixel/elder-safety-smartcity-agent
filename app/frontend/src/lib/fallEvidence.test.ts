import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { evidenceDetail, evidenceText, parseFallEvidence } from "./fallEvidence";

const raw = JSON.parse(readFileSync("public/models/fall_cv_summary.json", "utf8")) as unknown;

describe("fall evidence", () => {
  it("renders the headline from the cross-validation file, not from constants", () => {
    const e = parseFallEvidence(raw);
    expect(evidenceText(e)).toBe("낙상 감지율 규칙 27% → AI 97%, 감지 지연 2.25초 → 0.73초 (UR Fall 공개 데이터, 교차검증)");
    const changed = parseFallEvidence({ ...(raw as object), logreg: { ...(raw as { logreg: object }).logreg, sens: [0.5, 0.4, 0.6] } });
    expect(evidenceText(changed)).toContain("AI 50%");
  });
  it("keeps the false-alarm rates and the lab-data limit next to the headline", () => {
    const d = evidenceDetail(parseFallEvidence(raw));
    expect(d).toContain("오경보");
    expect(d).toContain("실제 횡단보도 영상으로는 검증하지 않았습니다");
  });
  it("rejects a malformed file", () => {
    expect(() => parseFallEvidence({})).toThrow();
    expect(() => parseFallEvidence({ n_fall: 1, n_adl: 1, neg_hours: 1, rule: { sens: [1] }, logreg: {} })).toThrow();
  });
});
