/** Plain-Korean glosses for the metrics on the 에이전트 pages. Shown at first use, in parentheses or behind an InfoTip. */
export const ZP_GLOSS = {
  coverage: "다음 해 사상자 중 구역 300m 안에 든 비율",
  capture: "경보 낸 지역에서 나온 환자 ÷ 전체 환자",
  ci: "같은 실험을 반복해도 결과가 이 범위 안에 들어올 가능성이 높은 구간",
  pp: "퍼센트끼리의 차이. 42% → 56% = 14%p",
  newSite: "과거에 사고다발지로 잡힌 적 없는 곳(300m 안에 이전 다발지 없음)에서 새로 생긴 다발지",
  hotspot: "TAAS가 지정한 보행 노인 사상자 다발 지점",
  nightOpen: "밤 22시~06시에 문을 열어 둔 AED 비율 (평일 기준)",
  gainScore: "24시간으로 바꿨을 때 밤 시간 접근성이 늘어나는 정도 (클수록 효과가 큼)",
  greenDeficit: "횡단보도 길이를 노인 보행속도 0.7m/s로 걸을 때 녹색 신호 시간이 모자라는 곳",
  rank: "그날 전국 시군구 중 우리 모형의 위험 점수 순위 (1이 가장 높음)",
} as const;

export const MODULE_LABEL: Readonly<Record<string, string>> = {
  heat: "폭염",
  silver: "노인 보행",
  aed: "AED",
  medical: "약국·의료",
};

/** "heat" -> "폭염". Unknown module ids pass through so a new backend module is still shown. */
export function moduleLabel(id: string): string {
  return MODULE_LABEL[id] ?? id;
}
