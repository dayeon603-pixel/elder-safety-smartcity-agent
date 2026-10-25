/** Public "시연판" switch. Set by the pages build only (VITE_STATIC=1); every static code path is behind this flag. */
export const IS_STATIC: boolean = import.meta.env.VITE_STATIC === "1";
export const BADGE_TEXT = "공개 시연판 · 팀 제작 시연 (서버·발송 없음) | 학생 해커톤 작품 시연 · 관악구 및 정부기관과 무관 · 모든 출력은 훈련용";
export const BADGE_AGENT_TEXT = "기록된 실행 · 실제 로컬 LLM 실행 기록을 재생 중";
export const BADGE_OTHER_DEVICE_TEXT = "시연판 · 다른 기기에서는 기록된 최신 메시지를 보여줍니다";
export const FIXTURE_DIR = "fixtures";
export const fixtureBase = (): string => `${import.meta.env.BASE_URL}${FIXTURE_DIR}`;
/** Scene fixture file name; fixed by the recorder's slug rule (path "/" -> "_"). Absolute so a worker can fetch it. */
export const staticSceneUrl = (code: string): string => new URL(`${fixtureBase()}/twin_${code}_scene.json`, globalThis.location.href).href;
