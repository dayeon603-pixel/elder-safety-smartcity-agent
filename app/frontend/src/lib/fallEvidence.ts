/** Cross-validation evidence for the fall model, read from public/models/fall_cv_summary.json (UR Fall Detection Dataset). */
import { FALL_CV_URL } from "../config";

export interface Triple {
  est: number;
  lo: number;
  hi: number;
}

export interface FallEvidence {
  nFall: number;
  nAdl: number;
  negHours: number;
  rule: { sens: Triple; latMed: Triple; faPerH: Triple };
  ai: { sens: Triple; latMed: Triple; faPerH: Triple };
}

function triple(v: unknown, name: string): Triple {
  if (!Array.isArray(v) || v.length !== 3 || v.some((x) => typeof x !== "number" || !Number.isFinite(x))) throw new Error(`evidence: ${name} must be [est, lo, hi]`);
  return { est: v[0] as number, lo: v[1] as number, hi: v[2] as number };
}

function block(v: unknown, name: string): FallEvidence["rule"] {
  if (typeof v !== "object" || v === null) throw new Error(`evidence: ${name} missing`);
  const r = v as Record<string, unknown>;
  return { sens: triple(r.sens, `${name}.sens`), latMed: triple(r.lat_med, `${name}.lat_med`), faPerH: triple(r.fa_per_h, `${name}.fa_per_h`) };
}

export function parseFallEvidence(raw: unknown): FallEvidence {
  if (typeof raw !== "object" || raw === null) throw new Error("evidence: not an object");
  const r = raw as Record<string, unknown>;
  const n = (k: string): number => {
    const v = r[k];
    if (typeof v !== "number") throw new Error(`evidence: ${k} missing`);
    return v;
  };
  return { nFall: n("n_fall"), nAdl: n("n_adl"), negHours: n("neg_hours"), rule: block(r.rule, "rule"), ai: block(r.logreg, "logreg") };
}

export async function loadFallEvidence(): Promise<FallEvidence> {
  const res = await fetch(FALL_CV_URL);
  if (!res.ok) throw new Error(`${FALL_CV_URL} ${res.status}`);
  return parseFallEvidence(await res.json());
}

const pct = (v: number): string => `${Math.round(v * 100)}%`;

export function evidenceText(e: FallEvidence): string {
  return `낙상 감지율 규칙 ${pct(e.rule.sens.est)} → AI ${pct(e.ai.sens.est)}, 감지 지연 ${e.rule.latMed.est.toFixed(2)}초 → ${e.ai.latMed.est.toFixed(2)}초 (UR Fall 공개 데이터, 교차검증)`;
}

/** Plain facts that go with the headline numbers: sample sizes, intervals and the false-alarm rate. */
export function evidenceDetail(e: FallEvidence): string {
  const ci = (t: Triple): string => `${pct(t.lo)}–${pct(t.hi)}`;
  return [
    `낙상 영상 ${e.nFall}건, 일상 동작 영상 ${e.nAdl}건(${e.negHours.toFixed(3)}시간).`,
    `감지율(낙상 영상 중 경보가 난 비율) 95% 구간: 규칙 ${ci(e.rule.sens)}, AI ${ci(e.ai.sens)}.`,
    `일상 동작 영상에서의 오경보: 규칙 시간당 ${e.rule.faPerH.est.toFixed(0)}건, AI 시간당 ${e.ai.faPerH.est.toFixed(0)}건 (0.093시간 분량이라 불확실).`,
    "실험실 옆면 카메라 영상이며 실제 횡단보도 영상으로는 검증하지 않았습니다.",
  ].join(" ");
}
